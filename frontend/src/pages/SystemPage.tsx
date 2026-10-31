import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../App";
import { Spinner, useToast } from "../components/ui";
import { JOB_STATUS, JOB_STATUS_ORDER, copyText } from "../format";
import type { JobStatus } from "../types";

type CheckStatus = "ok" | "warning" | "critical" | "unknown";

interface Check {
  key: string;
  title: string;
  status: CheckStatus;
  value: string;
  detail: string;
}

interface SystemStatus {
  status: CheckStatus;
  generated_at: string;
  checks: Check[];
  jobs: Partial<Record<JobStatus, number>>;
  monitor_interval_s: number;
  metrics_enabled: boolean;
}

const REFRESH_MS = 15000;

// Статус показывается значком и словом, а не только цветом.
const STATUS: Record<CheckStatus, { label: string; icon: string }> = {
  ok: { label: "Норма", icon: "✓" },
  warning: { label: "Внимание", icon: "!" },
  critical: { label: "Сбой", icon: "✕" },
  unknown: { label: "Нет данных", icon: "?" },
};

const SUMMARY: Record<CheckStatus, string> = {
  ok: "Все сервисы работают",
  warning: "Есть предупреждения",
  critical: "Есть сбои",
  unknown: "Все сервисы работают",
};

function secondsAgo(iso: string, now: number): string {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  return s < 5 ? "только что" : `${s} сек назад`;
}

export default function SystemPage() {
  const { user } = useSession();
  const toast = useToast();
  const [data, setData] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    try {
      setData(await api.get<SystemStatus>("/api/system/status"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    // Пока вкладка скрыта, сервер не опрашивается.
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, REFRESH_MS);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  if (!data) return error ? <div className="page"><div className="alert alert-danger">{error}</div></div> : <Spinner />;

  const problems = data.checks.filter((c) => c.status === "warning" || c.status === "critical");
  const ordered = [...problems, ...data.checks.filter((c) => !problems.includes(c))];
  const metricsUrl = `${window.location.origin}/api/metrics`;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Система</h1>
          <p className="muted">
            Состояние сервисов панели. Страница обновляется каждые {REFRESH_MS / 1000} секунд · проверено{" "}
            {secondsAgo(data.generated_at, now)}.
          </p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => load()}>
            Проверить сейчас
          </button>
        </div>
      </header>

      {error && <div className="alert alert-danger">Не удалось обновить: {error}. Показаны последние данные.</div>}

      <div className={`system-banner sys-${data.status}`} role="status">
        <span className="status-icon" aria-hidden="true">
          {STATUS[data.status].icon}
        </span>
        <div>
          <strong>{SUMMARY[data.status]}</strong>
          <div className="small">
            {problems.length
              ? problems.map((c) => `${c.title}: ${c.value}`).join(" · ")
              : `Проверок: ${data.checks.length}, проблем нет.`}
          </div>
        </div>
      </div>

      <div className="check-grid">
        {ordered.map((c) => (
          <section key={c.key} className={`card check sys-${c.status}`}>
            <div className="check-head">
              <span className="check-title">{c.title}</span>
              <span className="check-status">
                <span className="status-icon" aria-hidden="true">
                  {STATUS[c.status].icon}
                </span>
                {STATUS[c.status].label}
              </span>
            </div>
            <strong className="check-value">{c.value}</strong>
            {c.detail && <p className="muted small">{c.detail}</p>}
          </section>
        ))}
      </div>

      <section className="card">
        <h2>Задания по статусам</h2>
        <div className="job-counts">
          {JOB_STATUS_ORDER.map((s) => (
            <div key={s} className="job-count">
              <span className="muted small">{JOB_STATUS[s].label}</span>
              <strong>{data.jobs[s] ?? 0}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Уведомления и внешний мониторинг</h2>
        <p>
          {data.monitor_interval_s > 0 ? (
            <>
              Панель проверяет сервисы раз в {Math.round(data.monitor_interval_s)} сек. Если проблема держится две проверки
              подряд, администраторам и операторам приходит push-уведомление, а когда она уходит — сообщение «Система снова в
              норме». Включить их можно на странице <Link to="/notifications">«Уведомления»</Link>.
            </>
          ) : (
            <>Фоновые проверки выключены (MONITOR_INTERVAL_S=0): уведомления о сбоях не приходят.</>
          )}
        </p>
        {data.metrics_enabled ? (
          <p>
            Метрики для Prometheus:{" "}
            <button className="link-btn" onClick={() => copyText(metricsUrl).then(() => toast("Адрес скопирован", "success"))}>
              <code>{metricsUrl}</code>
            </button>{" "}
            — с заголовком <code>Authorization: Bearer &lt;METRICS_TOKEN&gt;</code>.
          </p>
        ) : (
          user.role === "admin" && (
            <p className="muted small">
              Для Prometheus или Grafana задайте <code>METRICS_TOKEN</code> в .env — откроется адрес <code>/api/metrics</code>.
            </p>
          )
        )}
      </section>
    </div>
  );
}
