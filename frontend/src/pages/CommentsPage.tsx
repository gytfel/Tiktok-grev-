import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { AiVariantsModal, TemplatePicker } from "../components/assist";
import { Badge, Empty, Modal, Spinner, useToast } from "../components/ui";
import { COMMENT_STATUS, copyText, formatDateTime } from "../format";
import { fromInput, toInput, tomorrowAt } from "../time";
import type { CommentDraft, CommentStatus } from "../types";

const TABS: CommentStatus[] = ["draft", "sent", "archived"];

export default function CommentsPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState<CommentStatus>("draft");
  const [items, setItems] = useState<CommentDraft[] | null>(null);
  const [editing, setEditing] = useState<CommentDraft | { job_id: number | null } | null>(null);

  const load = useCallback(
    () => api.get<CommentDraft[]>(`/api/comments?status=${status}`).then(setItems),
    [status],
  );

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  // Переход со страницы кампании (?job=) или из напоминания (?open=).
  useEffect(() => {
    const job = params.get("job");
    const open = params.get("open");
    if (job && canWrite) setEditing({ job_id: Number(job) });
    if (open) {
      api
        .get<CommentDraft>(`/api/comments/${open}`)
        .then(setEditing)
        .catch((e) => toast(e.message, "danger"));
    }
    if (job || open) setParams({}, { replace: true });
  }, [params, setParams, canWrite, toast]);

  if (!items) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Ответы на комментарии</h1>
          <p className="muted">Черновики ответов зрителям под вашими публикациями.</p>
        </div>
        {canWrite && (
          <button className="btn btn-primary" onClick={() => setEditing({ job_id: null })}>
            Новый ответ
          </button>
        )}
      </header>
      <p className="hint">
        Панель не отправляет комментарии сама: скопируйте готовый ответ, откройте видео в TikTok, опубликуйте его и
        отметьте здесь как отправленный. Черновик к конкретной публикации удобно создать со страницы кампании.
      </p>

      <div className="segmented">
        {TABS.map((t) => (
          <button key={t} className={status === t ? "active" : ""} onClick={() => setStatus(t)}>
            {COMMENT_STATUS[t].label}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <Empty title="Здесь пока пусто" />
      ) : (
        <div className="comment-list">
          {items.map((c) => (
            <article className="card comment-card" key={c.id}>
              <div className="comment-meta">
                <span className="strong">{c.account_username ? `@${c.account_username}` : c.account_label ?? "Без аккаунта"}</span>
                {c.campaign_title && (
                  <Link className="small" to={`/campaigns/${c.campaign_id}`}>
                    {c.campaign_title}
                  </Link>
                )}
                <Badge tone={COMMENT_STATUS[c.status].tone}>{COMMENT_STATUS[c.status].label}</Badge>
                <span className="muted small">{formatDateTime(c.sent_at ?? c.updated_at)}</span>
              </div>
              {c.original_comment && <div className="quote">{c.original_comment}</div>}
              <div className="variant-text">{c.reply || <span className="muted">ответ ещё не написан</span>}</div>
              <div className="variant-actions">
                {c.reply && (
                  <button className="btn btn-sm" onClick={() => copyText(c.reply).then(() => toast("Ответ скопирован", "success"))}>
                    Копировать ответ
                  </button>
                )}
                {c.video_url && (
                  <a className="btn btn-sm" href={c.video_url} target="_blank" rel="noreferrer">
                    Открыть видео
                  </a>
                )}
                {canWrite && (
                  <button className="btn btn-sm" onClick={() => setEditing(c)}>
                    Открыть
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      )}

      {editing && (
        <CommentEditor
          initial={editing}
          onClose={() => setEditing(null)}
          onChanged={() => load()}
        />
      )}
    </div>
  );
}

function CommentEditor({
  initial,
  onClose,
  onChanged,
}: {
  initial: CommentDraft | { job_id: number | null };
  onClose: () => void;
  onChanged: () => void;
}) {
  const { meta } = useSession();
  const toast = useToast();
  const [draft, setDraft] = useState<CommentDraft | null>("id" in initial ? initial : null);
  const [comment, setComment] = useState("id" in initial ? initial.original_comment : "");
  const [reply, setReply] = useState("id" in initial ? initial.reply : "");
  const [videoUrl, setVideoUrl] = useState("id" in initial ? initial.video_url : "");
  const [picker, setPicker] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [remindAt, setRemindAt] = useState("");
  const [busy, setBusy] = useState(false);
  const jobId = draft?.job_id ?? ("job_id" in initial ? initial.job_id : null);

  async function save(extra: Record<string, unknown> = {}): Promise<CommentDraft | null> {
    setBusy(true);
    try {
      const body = { original_comment: comment, reply, ...extra };
      const saved = draft
        ? await api.patch<CommentDraft>(`/api/comments/${draft.id}`, { ...body, ...(draft.job_id ? {} : { video_url: videoUrl }) })
        : await api.post<CommentDraft>("/api/comments", { ...body, job_id: jobId, video_url: jobId ? "" : videoUrl });
      setDraft(saved);
      onChanged();
      return saved;
    } catch (e) {
      toast((e as ApiError).message, "danger");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function copyAndOpen() {
    const saved = await save();
    if (!saved) return;
    await copyText(saved.reply);
    toast("Ответ скопирован — вставьте его под комментарием в TikTok", "success");
    if (saved.video_url) window.open(saved.video_url, "_blank", "noopener");
  }

  async function remind(due: Date) {
    const saved = draft ?? (await save());
    if (!saved) return;
    try {
      await api.post("/api/reminders", {
        title: `Ответить на комментарий${saved.account_username ? ` (@${saved.account_username})` : ""}`,
        note: comment.slice(0, 200),
        due_at: due.toISOString(),
        comment_draft_id: saved.id,
      });
      toast(`Напомню ${formatDateTime(due)}`, "success");
      setRemindAt("");
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  async function remove() {
    if (!draft || !confirm("Удалить черновик?")) return;
    await api.del(`/api/comments/${draft.id}`);
    onChanged();
    onClose();
  }

  const tomorrow10 = tomorrowAt(10);

  return (
    <Modal title={draft ? "Ответ на комментарий" : "Новый ответ"} onClose={onClose} wide>
      {(draft?.account_username || draft?.campaign_title) && (
        <p className="muted small">
          {draft.account_username && `@${draft.account_username}`} {draft.campaign_title && `· ${draft.campaign_title}`}
        </p>
      )}
      {!jobId && (
        <label className="field">
          <span>Ссылка на видео в TikTok (необязательно)</span>
          <input value={videoUrl} onChange={(e) => setVideoUrl(e.target.value)} placeholder="https://www.tiktok.com/@аккаунт/video/…" />
        </label>
      )}
      <label className="field">
        <span>Комментарий зрителя</span>
        <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} placeholder="Скопируйте сюда комментарий, на который отвечаете" />
      </label>
      <label className="field">
        <span>Ваш ответ</span>
        <textarea value={reply} onChange={(e) => setReply(e.target.value)} rows={4} />
      </label>
      <div className="actions">
        <button className="btn btn-sm" onClick={() => setPicker(true)}>
          Шаблон ответа
        </button>
        {meta.ai_enabled && (
          <button className="btn btn-sm" onClick={() => setAiOpen(true)} disabled={!comment.trim()} title={comment.trim() ? "" : "Сначала вставьте комментарий"}>
            AI: варианты ответа
          </button>
        )}
      </div>

      <div className="editor-actions">
        <button className="btn btn-primary" onClick={() => save()} disabled={busy}>
          Сохранить
        </button>
        <button className="btn" onClick={copyAndOpen} disabled={busy || !reply.trim()}>
          Скопировать{draft?.video_url || videoUrl ? " и открыть видео" : ""}
        </button>
        {draft && draft.status !== "sent" && (
          <button className="btn" onClick={() => save({ status: "sent" }).then((d) => d && onClose())} disabled={busy}>
            Отправлено ✓
          </button>
        )}
        {draft && draft.status !== "archived" && (
          <button className="btn" onClick={() => save({ status: "archived" }).then((d) => d && onClose())} disabled={busy}>
            В архив
          </button>
        )}
        {draft && draft.status !== "draft" && (
          <button className="btn" onClick={() => save({ status: "draft" })} disabled={busy}>
            Вернуть в черновики
          </button>
        )}
      </div>

      <fieldset className="field remind">
        <legend>Напомнить ответить</legend>
        <div className="actions">
          <button className="btn btn-sm" onClick={() => remind(new Date(Date.now() + 3600_000))}>
            Через час
          </button>
          <button className="btn btn-sm" onClick={() => remind(tomorrow10)}>
            Завтра в 10:00
          </button>
          <input type="datetime-local" value={remindAt} min={toInput(new Date())} onChange={(e) => setRemindAt(e.target.value)} />
          <button className="btn btn-sm" disabled={!remindAt} onClick={() => remind(fromInput(remindAt))}>
            Напомнить
          </button>
        </div>
      </fieldset>

      {draft && (
        <button className="link-btn text-danger" onClick={remove}>
          Удалить черновик
        </button>
      )}

      {picker && (
        <TemplatePicker
          kind="reply"
          onClose={() => setPicker(false)}
          onPick={(body) => {
            setReply(body);
            setPicker(false);
          }}
        />
      )}
      {aiOpen && (
        <AiVariantsModal
          task="reply"
          comment={comment}
          initialTopic={draft?.campaign_title ?? ""}
          onClose={() => setAiOpen(false)}
          onPick={(text) => {
            setReply(text);
            setAiOpen(false);
          }}
        />
      )}
    </Modal>
  );
}
