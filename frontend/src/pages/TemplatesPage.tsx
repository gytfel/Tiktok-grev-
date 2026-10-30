import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { AiVariantsModal } from "../components/assist";
import { Empty, Modal, Spinner, useToast } from "../components/ui";
import { TEMPLATE_KINDS, copyText } from "../format";
import type { AiTask, TemplateKind, TextTemplate } from "../types";

const KINDS = Object.keys(TEMPLATE_KINDS) as TemplateKind[];

export default function TemplatesPage() {
  const { canWrite, meta } = useSession();
  const toast = useToast();
  const [kind, setKind] = useState<TemplateKind>("caption");
  const [items, setItems] = useState<TextTemplate[] | null>(null);
  const [editing, setEditing] = useState<TextTemplate | "new" | null>(null);
  const [aiTask, setAiTask] = useState<AiTask | null>(null);

  const load = useCallback(() => api.get<TextTemplate[]>("/api/templates").then(setItems), []);

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  const grouped = useMemo(() => {
    const map = new Map<string, TextTemplate[]>();
    items
      ?.filter((t) => t.kind === kind)
      .forEach((t) => map.set(t.category || "Без категории", [...(map.get(t.category || "Без категории") ?? []), t]));
    return [...map.entries()];
  }, [items, kind]);

  async function remove(t: TextTemplate) {
    if (!confirm(`Удалить шаблон «${t.title}»?`)) return;
    try {
      await api.del(`/api/templates/${t.id}`);
      await load();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  if (!items) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Шаблоны</h1>
          <p className="muted">Готовые подписи, наборы хештегов и ответы на комментарии. Работают и без AI.</p>
        </div>
        {canWrite && (
          <div className="actions">
            {meta.ai_enabled && (
              <>
                <button className="btn" onClick={() => setAiTask("ideas")}>
                  AI: идеи роликов
                </button>
                <button className="btn" onClick={() => setAiTask(kind === "reply" ? "ideas" : kind)}>
                  AI: {kind === "hashtags" ? "хештеги" : kind === "reply" ? "идеи" : "подписи"}
                </button>
              </>
            )}
            <button className="btn btn-primary" onClick={() => setEditing("new")}>
              Новый шаблон
            </button>
          </div>
        )}
      </header>

      {!meta.ai_enabled && (
        <p className="hint">
          AI-помощник не подключён. Его можно включить бесплатно — через локальную модель Ollama или бесплатный тариф
          облачного сервиса (см. README, раздел «AI-помощник»).
        </p>
      )}

      <div className="segmented">
        {KINDS.map((k) => (
          <button key={k} className={kind === k ? "active" : ""} onClick={() => setKind(k)}>
            {TEMPLATE_KINDS[k]} ({items.filter((t) => t.kind === k).length})
          </button>
        ))}
      </div>

      {grouped.length === 0 ? (
        <Empty title="Шаблонов этого вида пока нет">Создайте первый — он появится в мастере кампании и в ответах на комментарии.</Empty>
      ) : (
        grouped.map(([category, list]) => (
          <section key={category}>
            <h2 className="group-title">{category}</h2>
            <div className="template-grid">
              {list.map((t) => (
                <article className="card template-card" key={t.id}>
                  <strong>{t.title}</strong>
                  <div className="variant-text">{t.body}</div>
                  <div className="variant-actions">
                    <button className="btn btn-sm" onClick={() => copyText(t.body).then(() => toast("Скопировано", "success"))}>
                      Копировать
                    </button>
                    {canWrite && (
                      <>
                        <button className="btn btn-sm" onClick={() => setEditing(t)}>
                          Изменить
                        </button>
                        <button className="btn btn-sm btn-danger-ghost" onClick={() => remove(t)}>
                          Удалить
                        </button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))
      )}

      {editing && (
        <TemplateEditor
          template={editing === "new" ? null : editing}
          kind={kind}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}
      {aiTask && (
        <AiVariantsModal
          task={aiTask}
          onClose={() => {
            setAiTask(null);
            load();
          }}
        />
      )}
    </div>
  );
}

function TemplateEditor({
  template,
  kind,
  onClose,
  onSaved,
}: {
  template: TextTemplate | null;
  kind: TemplateKind;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState({
    kind: template?.kind ?? kind,
    category: template?.category ?? "",
    title: template?.title ?? "",
    body: template?.body ?? "",
  });
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      if (template) {
        await api.patch(`/api/templates/${template.id}`, { category: form.category, title: form.title, body: form.body });
      } else {
        await api.post("/api/templates", form);
      }
      onSaved();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={template ? "Шаблон" : "Новый шаблон"}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !form.title.trim() || !form.body.trim()}>
            Сохранить
          </button>
        </>
      }
    >
      {!template && (
        <label className="field">
          <span>Вид</span>
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as TemplateKind })}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {TEMPLATE_KINDS[k]}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        <span>Название</span>
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={200} autoFocus />
      </label>
      <label className="field">
        <span>Категория (необязательно)</span>
        <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength={100} placeholder="Например: Осень" />
      </label>
      <label className="field">
        <span>{form.kind === "hashtags" ? "Хештеги через пробел" : "Текст"}</span>
        <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} rows={5} />
      </label>
    </Modal>
  );
}
