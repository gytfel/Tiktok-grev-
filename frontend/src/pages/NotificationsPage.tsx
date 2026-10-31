import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Spinner, useToast } from "../components/ui";
import {
  canPromptInstall,
  currentSubscription,
  disablePush,
  enablePush,
  isIOS,
  isStandalone,
  onInstallAvailabilityChange,
  promptInstall,
  pushSupport,
  savePreferences,
} from "../pwa";
import type { NotificationPreferences } from "../pwa";

const EVENTS: { key: keyof NotificationPreferences; title: string; text: string }[] = [
  {
    key: "campaign_finished",
    title: "Кампания завершена",
    text: "Сколько видео опубликовано и сколько заданий требует внимания.",
  },
  {
    key: "attention",
    title: "Задания требуют внимания",
    text: "Ошибки, потерянная авторизация, ручная проверка. Одно уведомление на кампанию, а не на каждый аккаунт.",
  },
  {
    key: "reminder",
    title: "Напоминания",
    text: "Например, ответить на комментарии к публикации в назначенное время.",
  },
  {
    key: "system",
    title: "Сбои системы",
    text: "Остановились воркеры, переполняется диск, давно не было резервной копии. Только для администраторов и операторов.",
  },
];

function errorText(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : "Не удалось выполнить действие";
}

export default function NotificationsPage() {
  return (
    <div className="page narrow">
      <header className="page-head">
        <div>
          <h1>Телефон и уведомления</h1>
          <p className="muted">Панель можно установить на телефон как приложение и получать push-уведомления.</p>
        </div>
      </header>
      <InstallCard />
      <PushCard />
    </div>
  );
}

function InstallCard() {
  const [canInstall, setCanInstall] = useState(canPromptInstall());
  useEffect(() => onInstallAvailabilityChange(() => setCanInstall(canPromptInstall())), []);

  if (isStandalone()) {
    return (
      <section className="card">
        <h2>
          Приложение <Badge tone="success">установлено</Badge>
        </h2>
        <p className="muted">Панель открыта с экрана «Домой».</p>
      </section>
    );
  }
  return (
    <section className="card">
      <h2>Установить на телефон</h2>
      {isIOS() ? (
        <ol className="steps">
          <li>Откройте эту страницу в Safari.</li>
          <li>
            Нажмите «Поделиться» <span className="kbd">⬆︎</span> внизу экрана.
          </li>
          <li>Выберите «На экран Домой» и нажмите «Добавить».</li>
          <li>Откройте «Публикации» с экрана «Домой» и вернитесь в этот раздел, чтобы включить уведомления.</li>
        </ol>
      ) : canInstall ? (
        <>
          <p className="muted">Панель откроется отдельным окном, как обычное приложение, с иконкой на главном экране.</p>
          <button className="btn btn-primary" onClick={() => promptInstall()}>
            Установить приложение
          </button>
        </>
      ) : (
        <p className="muted">
          В Chrome на Android: меню <span className="kbd">⋮</span> → «Установить приложение» или «Добавить на главный
          экран». На компьютере — значок установки в адресной строке Chrome или Edge.
        </p>
      )}
    </section>
  );
}

function PushCard() {
  const { canWrite } = useSession();
  const toast = useToast();
  const support = pushSupport();
  const [sub, setSub] = useState<PushSubscription | null | undefined>(undefined);
  const [prefs, setPrefs] = useState<NotificationPreferences>({
    campaign_finished: true,
    attention: true,
    reminder: true,
    system: true,
  });
  const [permission, setPermission] = useState<NotificationPermission>(
    "Notification" in window ? Notification.permission : "default",
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (support !== "supported") return;
    (async () => {
      const current = await currentSubscription();
      if (!current) return setSub(null);
      const state = await api.post<{ subscribed: boolean; preferences: NotificationPreferences }>(
        "/api/push/subscriptions/lookup",
        { endpoint: current.endpoint },
      );
      setPrefs(state.preferences);
      setSub(state.subscribed ? current : null);
    })().catch((e) => {
      toast(errorText(e), "danger");
      setSub(null);
    });
  }, [support, toast]);

  async function enable() {
    setBusy(true);
    try {
      setSub(await enablePush(prefs));
      toast("Уведомления включены на этом устройстве", "success");
    } catch (e) {
      toast(errorText(e), "danger");
    } finally {
      setPermission(Notification.permission);
      setBusy(false);
    }
  }

  async function disable() {
    if (!sub) return;
    setBusy(true);
    try {
      await disablePush(sub);
      setSub(null);
      toast("Уведомления на этом устройстве отключены", "info");
    } catch (e) {
      toast(errorText(e), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(key: keyof NotificationPreferences) {
    const next = { ...prefs, [key]: !prefs[key] };
    setPrefs(next);
    if (!sub) return;
    try {
      await savePreferences(sub, next);
    } catch (e) {
      setPrefs(prefs);
      toast(errorText(e), "danger");
    }
  }

  async function sendTest() {
    if (!sub) return;
    setBusy(true);
    try {
      await api.post("/api/push/test", { endpoint: sub.endpoint });
      toast("Тестовое уведомление отправлено — оно придёт через несколько секунд", "success");
    } catch (e) {
      toast(errorText(e), "danger");
    } finally {
      setBusy(false);
    }
  }

  const options = (
    <div className="option-list">
      {EVENTS.filter((event) => event.key !== "system" || canWrite).map((event) => (
        <label key={event.key} className="option">
          <input type="checkbox" checked={prefs[event.key]} onChange={() => toggle(event.key)} />
          <span>
            <strong>{event.title}</strong>
            <span className="muted small">{event.text}</span>
          </span>
        </label>
      ))}
    </div>
  );

  let content;
  if (support === "insecure") {
    content = (
      <div className="alert alert-info">
        Уведомления и установка работают только по HTTPS. Откройте панель по адресу, который начинается с https://.
      </div>
    );
  } else if (support === "needs-install") {
    content = (
      <div className="alert alert-info">
        На iPhone уведомления доступны только приложению с экрана «Домой» (iOS 16.4 и новее). Сначала установите
        панель — инструкция выше — и откройте этот раздел из установленного приложения.
      </div>
    );
  } else if (support === "unsupported") {
    content = <div className="alert alert-info">Этот браузер не поддерживает push-уведомления.</div>;
  } else if (permission === "denied") {
    content = (
      <div className="alert alert-danger">
        Уведомления запрещены для этой панели. Разрешите их: на iPhone — «Настройки» → «Уведомления» → «Публикации»; на
        Android — настройки сайта в Chrome; на компьютере — значок замка в адресной строке.
      </div>
    );
  } else if (sub === undefined) {
    content = <Spinner />;
  } else if (sub === null) {
    content = (
      <>
        <p className="muted">Выберите, о чём сообщать, и включите уведомления. Браузер попросит разрешение.</p>
        {options}
        <button className="btn btn-primary btn-block" onClick={enable} disabled={busy || !Object.values(prefs).some(Boolean)}>
          {busy ? "Подключение…" : "Включить уведомления"}
        </button>
      </>
    );
  } else {
    content = (
      <>
        {options}
        <div className="actions">
          <button className="btn" onClick={sendTest} disabled={busy}>
            Отправить тестовое
          </button>
          <button className="btn btn-danger-ghost" onClick={disable} disabled={busy}>
            Отключить на этом устройстве
          </button>
        </div>
      </>
    );
  }

  return (
    <section className="card">
      <h2>
        Уведомления на этом устройстве {sub ? <Badge tone="success">включены</Badge> : null}
      </h2>
      {content}
    </section>
  );
}
