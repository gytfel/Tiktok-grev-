import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { copyText, TEMPLATE_KINDS } from "../format";
import type { AiTask, TemplateKind, TextTemplate } from "../types";
import { Empty, Modal, Spinner, useToast } from "./ui";

const TASK_TITLES: Record<AiTask, string> = {
  caption: "AI: варианты подписи",
  hashtags: "AI: хештеги",
  ideas: "AI: идеи роликов",
  reply: "AI: варианты ответа",
};

const TONES = ["", "дружелюбный", "с юмором", "деловой", "вдохновляющий", "краткий"];

/** Генерация вариантов через AI. Вариант только предлагается — вставляет его человек. */
export function AiVariantsModal({
  task,
  initialTopic = "",
  initialText = "",
  comment = "",
  onPick,
  pickLabel = "Вставить",
  onClose,
}: {
  task: AiTask;
  initialTopic?: string;
  initialText?: string;
  comment?: string;
  onPick?: (text: string) => void;
  pickLabel?: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const [topic, setTopic] = useState(initialTopic);
  const [tone, setTone] = useState("");
  const [count, setCount] = useState(task === "hashtags" ? 10 : 5);
  const [variants, setVariants] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<Set<string>>(new Set());

  async function generate() {
    setBusy(true);
    try {
      const r = await api.post<{ variants: string[] }>("/api/ai/generate", {
        task,
        topic,
        text: initialText,
        comment,
        tone,
        count,
      });
      setVariants(r.variants);
    } catch (e) {
      toast((e as ApiError).message, "danger");
    } finally {
      setBusy(false);
    }
  }

  async function saveAsTemplate(text: string) {
    const kind: TemplateKind = task === "reply" ? "reply" : task === "hashtags" ? "hashtags" : "caption";
    try {
      await api.post("/api/templates", {
        kind,
        category: task === "ideas" ? "Идеи" : "AI",
        title: text.slice(0, 60),
        body: text,
      });
      setSaved((prev) => new Set(prev).add(text));
      toast("Сохранено в шаблоны", "success");
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  const allTags = task === "hashtags" && variants.length > 0 ? variants.join(" ") : null;

  return (
    <Modal title={TASK_TITLES[task]} onClose={onClose} wide>
      {task === "reply" && (
        <div className="quote">
          <span className="muted small">Комментарий зрителя</span>
          <div>{comment}</div>
        </div>
      )}
      <div className="ai-form">
        <label className="field">
          <span>{task === "ideas" ? "Ниша или тема" : task === "reply" ? "О чём видео (необязательно)" : "Тема видео"}</span>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Например: осенняя распродажа обуви" />
        </label>
        {task !== "hashtags" && (
          <label className="field">
            <span>Тон</span>
            <select value={tone} onChange={(e) => setTone(e.target.value)}>
              {TONES.map((t) => (
                <option key={t} value={t}>
                  {t || "любой"}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Сколько вариантов</span>
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[3, 5, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button className="btn btn-primary" onClick={generate} disabled={busy || (task !== "reply" && !topic.trim() && !initialText.trim())}>
        {busy ? "Генерация… (локальная модель может думать до минуты)" : variants.length ? "Ещё варианты" : "Сгенерировать"}
      </button>

      {busy && variants.length === 0 && (
        <div className="center-pad">
          <Spinner />
        </div>
      )}
      {allTags && onPick && (
        <div className="variant">
          <div className="variant-text">{allTags}</div>
          <div className="variant-actions">
            <button className="btn btn-sm btn-primary" onClick={() => onPick(allTags)}>
              Вставить все
            </button>
          </div>
        </div>
      )}
      {variants.map((text) => (
        <div className="variant" key={text}>
          <div className="variant-text">{text}</div>
          <div className="variant-actions">
            {onPick && (
              <button className="btn btn-sm btn-primary" onClick={() => onPick(text)}>
                {pickLabel}
              </button>
            )}
            <button className="btn btn-sm" onClick={() => copyText(text).then(() => toast("Скопировано", "success"))}>
              Копировать
            </button>
            <button className="btn btn-sm" disabled={saved.has(text)} onClick={() => saveAsTemplate(text)}>
              {saved.has(text) ? "В шаблонах" : "В шаблоны"}
            </button>
          </div>
        </div>
      ))}
      <p className="muted small">
        Тексты пишет модель — перед использованием прочитайте и при необходимости поправьте.
      </p>
    </Modal>
  );
}

/** Выбор сохранённого шаблона. */
export function TemplatePicker({
  kind,
  onPick,
  onClose,
}: {
  kind: TemplateKind;
  onPick: (body: string) => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const [items, setItems] = useState<TextTemplate[] | null>(null);
  const [category, setCategory] = useState("");

  useEffect(() => {
    api
      .get<TextTemplate[]>(`/api/templates?kind=${kind}`)
      .then(setItems)
      .catch((e) => toast(e.message, "danger"));
  }, [kind, toast]);

  const categories = useMemo(() => [...new Set(items?.map((t) => t.category).filter(Boolean))], [items]);
  const visible = items?.filter((t) => !category || t.category === category) ?? [];

  return (
    <Modal title={TEMPLATE_KINDS[kind]} onClose={onClose} wide>
      {!items ? (
        <Spinner />
      ) : items.length === 0 ? (
        <Empty title="Шаблонов пока нет">Добавьте их в разделе «Шаблоны».</Empty>
      ) : (
        <>
          {categories.length > 0 && (
            <div className="segmented">
              <button className={category === "" ? "active" : ""} onClick={() => setCategory("")}>
                Все
              </button>
              {categories.map((c) => (
                <button key={c} className={category === c ? "active" : ""} onClick={() => setCategory(c)}>
                  {c}
                </button>
              ))}
            </div>
          )}
          {visible.map((t) => (
            <button key={t.id} className="variant variant-pick" onClick={() => onPick(t.body)}>
              <strong>{t.title}</strong>
              <span className="variant-text">{t.body}</span>
            </button>
          ))}
        </>
      )}
    </Modal>
  );
}
