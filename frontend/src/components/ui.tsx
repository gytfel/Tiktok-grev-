import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Tone } from "../format";
import type { JobStatus } from "../types";
import { JOB_STATUS, JOB_STATUS_ORDER } from "../format";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Avatar({ name, url, size = 32 }: { name: string; url?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  const initials = name
    .replace(/^@/, "")
    .split(/[\s_.-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");
  let hue = 0;
  for (const ch of name) hue = (hue * 31 + ch.charCodeAt(0)) % 360;
  if (url && !broken) {
    return (
      <img
        className="avatar"
        src={url}
        alt=""
        width={size}
        height={size}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span
      className="avatar avatar-initials"
      style={{ width: size, height: size, fontSize: size * 0.4, background: `hsl(${hue} 45% 45%)` }}
      aria-hidden
    >
      {initials || "?"}
    </span>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            ×
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function StatusBar({ counts, total }: { counts: Partial<Record<JobStatus, number>>; total: number }) {
  if (!total) return <div className="status-bar empty" />;
  return (
    <div className="status-bar" role="img" aria-label="Распределение заданий по статусам">
      {JOB_STATUS_ORDER.map((status) => {
        const n = counts[status] ?? 0;
        if (!n) return null;
        return (
          <span
            key={status}
            className={`seg seg-${JOB_STATUS[status].tone}`}
            style={{ width: `${(n / total) * 100}%` }}
            title={`${JOB_STATUS[status].label}: ${n}`}
          />
        );
      })}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Загрузка" />;
}

// --- уведомления ----------------------------------------------------------------------

interface Toast {
  id: number;
  text: string;
  tone: "success" | "danger" | "info";
}

const ToastContext = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const push = useCallback((text: string, tone: Toast["tone"] = "info") => {
    const id = nextId.current++;
    setToasts((list) => [...list, { id, text, tone }]);
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), tone === "danger" ? 8000 : 4000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
