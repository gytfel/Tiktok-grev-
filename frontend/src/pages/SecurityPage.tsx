import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, useToast } from "../components/ui";
import type { User } from "../types";

interface Setup {
  secret: string;
  otpauth_uri: string;
  qr_data_url: string;
}

export default function SecurityPage() {
  const { user, meta, refresh } = useSession();
  const forced = meta.require_2fa && !user.totp_enabled;
  return (
    <div className="page narrow">
      <header className="page-head">
        <div>
          <h1>Безопасность</h1>
          <p className="muted">Пароль и двухфакторный вход для пользователя {user.username}.</p>
        </div>
      </header>
      {forced && (
        <div className="alert alert-info">
          Администратор требует двухфакторный вход. Включите его, чтобы продолжить работу с панелью.
        </div>
      )}
      <TwoFactorCard user={user} onChanged={refresh} />
      {!forced && <PasswordCard />}
    </div>
  );
}

function TwoFactorCard({ user, onChanged }: { user: User; onChanged: () => Promise<void> }) {
  const toast = useToast();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    try {
      setSetup(await api.post<Setup>("/api/auth/2fa/setup"));
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/auth/2fa/enable", { code });
      setSetup(null);
      setCode("");
      toast("Двухфакторный вход включён", "success");
      await onChanged();
    } catch (err) {
      toast((err as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault();
    if (!confirm("Отключить двухфакторный вход?")) return;
    setBusy(true);
    try {
      await api.post("/api/auth/2fa/disable", { password, code });
      setPassword("");
      setCode("");
      toast("Двухфакторный вход отключён", "info");
      await onChanged();
    } catch (err) {
      toast((err as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>
        Двухфакторный вход{" "}
        {user.totp_enabled ? <Badge tone="success">включён</Badge> : <Badge tone="warning">выключен</Badge>}
      </h2>
      {user.totp_enabled ? (
        <form onSubmit={disable}>
          <p className="muted">При входе, кроме пароля, нужен код из приложения-аутентификатора.</p>
          <div className="form-row">
            <input type="password" placeholder="Пароль" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="current-password" />
            <input placeholder="Код из приложения" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" required />
            <button className="btn btn-danger-ghost" disabled={busy}>
              Отключить
            </button>
          </div>
        </form>
      ) : setup ? (
        <form onSubmit={enable} className="twofa-setup">
          <ol className="steps">
            <li>Установите приложение-аутентификатор: Google Authenticator, Яндекс Ключ, Microsoft Authenticator или аналог.</li>
            <li>
              Отсканируйте QR-код или введите ключ вручную:
              <div className="qr-wrap">
                <img src={setup.qr_data_url} alt="QR-код для приложения-аутентификатора" width={200} height={200} />
                <code className="secret">{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
              </div>
            </li>
            <li>Введите 6-значный код из приложения:</li>
          </ol>
          <div className="form-row">
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" placeholder="123456" required autoFocus />
            <button className="btn btn-primary" disabled={busy}>
              Включить
            </button>
          </div>
        </form>
      ) : (
        <>
          <p className="muted">
            Даже если пароль узнают, без телефона с кодами войти не получится. Если телефон потерян, администратор
            может сбросить двухфакторный вход в разделе «Пользователи» (или командой <code>python -m app.cli reset-2fa</code>).
          </p>
          <button className="btn btn-primary" onClick={start} disabled={busy}>
            Настроить
          </button>
        </>
      )}
    </section>
  );
}

function PasswordCard() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== repeat) {
      toast("Новые пароли не совпадают", "danger");
      return;
    }
    try {
      await api.post("/api/auth/password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setRepeat("");
      toast("Пароль изменён. Другие устройства нужно будет войти заново.", "success");
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>Смена пароля</h2>
      <label className="field">
        <span>Текущий пароль</span>
        <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} required autoComplete="current-password" />
      </label>
      <label className="field">
        <span>Новый пароль (от 10 символов)</span>
        <input type="password" value={next} onChange={(e) => setNext(e.target.value)} minLength={10} required autoComplete="new-password" />
      </label>
      <label className="field">
        <span>Повторите новый пароль</span>
        <input type="password" value={repeat} onChange={(e) => setRepeat(e.target.value)} minLength={10} required autoComplete="new-password" />
      </label>
      <button className="btn btn-primary">Сменить пароль</button>
    </form>
  );
}
