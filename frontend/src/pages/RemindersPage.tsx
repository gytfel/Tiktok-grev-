import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Empty, Spinner, useToast } from "../components/ui";
import { formatDateTime, toLocalInput } from "../format";
import type { Reminder } from "../types";

export default function RemindersPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const [state, setState] = useState<"open" | "done">("open");
  const [items, setItems] = useState<Reminder[] | null>(null);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [due, setDue] = useState(() => toLocalInput(new Date(Date.now() + 3600_000)));

  const load = useCallback(() => api.get<Reminder[]>(`/api/reminders?state=${state}`).then(setItems), [state]);

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  async function create(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/reminders", { title, note, due_at: new Date(due).toISOString() });
      setTitle("");
      setNote("");
      await load();
      toast("Напоминание создано", "success");
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  async function act(r: Reminder, action: "done" | "reopen" | "delete") {
    try {
      if (action === "delete") await api.del(`/api/reminders/${r.id}`);
      else await api.post(`/api/reminders/${r.id}/${action}`);
      await load();
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  if (!items) return <Spinner />;
  const now = Date.now();

  return (
    <div className="page narrow">
      <header className="page-head">
        <div>
          <h1>Напоминания</h1>
          <p className="muted">Когда наступит время, придёт push-уведомление (если оно включено в разделе «Уведомления»).</p>
        </div>
      </header>

      {canWrite && (
        <form className="card" onSubmit={create}>
          <h2>Новое напоминание</h2>
          <label className="field">
            <span>О чём напомнить</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required placeholder="Например: ответить на комментарии к осенней кампании" />
          </label>
          <label className="field">
            <span>Заметка (необязательно)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
          </label>
          <div className="inline-form">
            <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} required />
            <button className="btn btn-primary">Создать</button>
          </div>
        </form>
      )}

      <div className="segmented">
        <button className={state === "open" ? "active" : ""} onClick={() => setState("open")}>
          Активные
        </button>
        <button className={state === "done" ? "active" : ""} onClick={() => setState("done")}>
          Выполненные
        </button>
      </div>

      {items.length === 0 ? (
        <Empty title={state === "open" ? "Активных напоминаний нет" : "Выполненных пока нет"} />
      ) : (
        <div className="comment-list">
          {items.map((r) => {
            const overdue = !r.done_at && new Date(r.due_at).getTime() <= now;
            return (
              <article className={`card reminder-card ${overdue ? "overdue" : ""}`} key={r.id}>
                <div className="comment-meta">
                  <strong>{r.title}</strong>
                  {overdue && <Badge tone="warning">пора</Badge>}
                  <span className="muted small">{formatDateTime(r.done_at ?? r.due_at)}</span>
                </div>
                {r.note && <div className="muted small">{r.note}</div>}
                <div className="variant-actions">
                  {r.link && (
                    <Link className="btn btn-sm" to={r.link}>
                      Открыть
                    </Link>
                  )}
                  {canWrite && !r.done_at && (
                    <button className="btn btn-sm btn-primary" onClick={() => act(r, "done")}>
                      Выполнено
                    </button>
                  )}
                  {canWrite && r.done_at && (
                    <button className="btn btn-sm" onClick={() => act(r, "reopen")}>
                      Вернуть
                    </button>
                  )}
                  {canWrite && (
                    <button className="btn btn-sm btn-danger-ghost" onClick={() => act(r, "delete")}>
                      Удалить
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
