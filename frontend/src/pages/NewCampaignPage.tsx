import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Avatar, Badge, Empty, Spinner, useToast } from "../components/ui";
import { VideoUploader } from "../components/VideoUploader";
import {
  ACCOUNT_STATUS,
  PRIVACY_LABELS,
  PRIVACY_ORDER,
  captionLength,
  formatBytes,
  formatDuration,
  formatRelative,
  plural,
} from "../format";
import type {
  Account,
  CampaignDraft,
  CampaignPreview,
  CampaignSummary,
  Group,
  Media,
  PrivacyLevel,
} from "../types";

const CAPTION_LIMIT = 2200;
const MUSIC_POLICY = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en";
const BRANDED_POLICY = "https://www.tiktok.com/legal/page/global/bc-policy/en";
const STEPS = ["Видео", "Аккаунты", "Публикация", "Проверка и запуск"];

export default function NewCampaignPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const [step, setStep] = useState(0);
  const [mediaList, setMediaList] = useState<Media[] | null>(null);
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);

  const [mediaId, setMediaId] = useState<number | null>(params.get("media") ? Number(params.get("media")) : null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [privacy, setPrivacy] = useState<PrivacyLevel | null>(null);
  const [allowComment, setAllowComment] = useState(false);
  const [allowDuet, setAllowDuet] = useState(false);
  const [allowStitch, setAllowStitch] = useState(false);
  const [disclose, setDisclose] = useState(false);
  const [brandOrganic, setBrandOrganic] = useState(false);
  const [brandContent, setBrandContent] = useState(false);
  const [isAigc, setIsAigc] = useState(false);
  const [coverSec, setCoverSec] = useState("");
  const [startMode, setStartMode] = useState<"now" | "later">("now");
  const [startAt, setStartAt] = useState("");
  const [allowDuplicates, setAllowDuplicates] = useState(false);
  const [overrides, setOverrides] = useState<Record<number, string>>({});

  const [preview, setPreview] = useState<CampaignPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [consent, setConsent] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const loadMedia = useCallback(async () => setMediaList(await api.get<Media[]>("/api/media")), []);

  useEffect(() => {
    Promise.all([loadMedia(), api.get<Account[]>("/api/accounts"), api.get<Group[]>("/api/groups")])
      .then(([, a, g]) => {
        setAccounts(a);
        setGroups(g);
      })
      .catch((e) => toast(e.message, "danger"));
  }, [loadMedia, toast]);

  const media = mediaList?.find((m) => m.id === mediaId) ?? null;
  const selectedAccounts = useMemo(
    () => accounts?.filter((a) => selected.has(a.id)) ?? [],
    [accounts, selected],
  );

  const draft: CampaignDraft | null = useMemo(() => {
    if (!mediaId || !privacy) return null;
    return {
      media_asset_id: mediaId,
      title: title.trim(),
      default_caption: caption,
      caption_overrides: overrides,
      account_ids: [...selected],
      settings: {
        privacy_level: privacy,
        allow_comment: allowComment,
        allow_duet: allowDuet,
        allow_stitch: allowStitch,
        brand_organic: disclose && brandOrganic,
        brand_content: disclose && brandContent,
        is_aigc: isAigc,
        cover_timestamp_ms: coverSec.trim() ? Math.round(Number(coverSec) * 1000) : null,
      },
      start_at: startMode === "later" && startAt ? new Date(startAt).toISOString() : null,
      allow_duplicates: allowDuplicates,
    };
  }, [
    mediaId, privacy, title, caption, overrides, selected, allowComment, allowDuet, allowStitch,
    disclose, brandOrganic, brandContent, isAigc, coverSec, startMode, startAt, allowDuplicates,
  ]);

  // Предпросмотр пересчитывается на последнем шаге при любом изменении параметров.
  const previewSeq = useRef(0);
  useEffect(() => {
    if (step !== 3 || !draft) return;
    const seq = ++previewSeq.current;
    setPreviewLoading(true);
    setConsent(false);
    const timer = setTimeout(() => {
      api
        .post<CampaignPreview>("/api/campaigns/preview", draft)
        .then((p) => seq === previewSeq.current && setPreview(p))
        .catch((e) => toast(e.message, "danger"))
        .finally(() => seq === previewSeq.current && setPreviewLoading(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [step, draft, toast]);

  const settingsErrors = useMemo(() => {
    const errors: string[] = [];
    if (!title.trim()) errors.push("Укажите название кампании");
    if (!privacy) errors.push("Выберите, кто сможет смотреть видео");
    if (captionLength(caption) > CAPTION_LIMIT) errors.push(`Подпись длиннее ${CAPTION_LIMIT} символов`);
    if (disclose && !brandOrganic && !brandContent) errors.push("Отметьте тип коммерческого контента");
    if (disclose && brandContent && privacy === "SELF_ONLY")
      errors.push("Брендированный контент нельзя публиковать с видимостью «Только я»");
    if (coverSec.trim() && (Number.isNaN(Number(coverSec)) || Number(coverSec) < 0))
      errors.push("Кадр обложки — число секунд");
    if (startMode === "later" && (!startAt || new Date(startAt).getTime() < Date.now()))
      errors.push("Время старта должно быть в будущем");
    return errors;
  }, [title, privacy, caption, disclose, brandOrganic, brandContent, coverSec, startMode, startAt]);

  const canNext = [
    !!media && media.validation_status === "valid",
    selected.size > 0,
    settingsErrors.length === 0,
  ][step];

  async function submit() {
    if (!draft || !preview) return;
    setSubmitting(true);
    try {
      const campaign = await api.post<CampaignSummary>("/api/campaigns", {
        ...draft,
        snapshot_hash: preview.snapshot_hash,
        consent,
      });
      toast(`Кампания запущена: ${campaign.total} ${plural(campaign.total, "задание", "задания", "заданий")}`, "success");
      navigate(`/campaigns/${campaign.id}`);
    } catch (e) {
      const err = e as ApiError;
      const body = err.body as { preview?: CampaignPreview } | null;
      if (body?.preview) setPreview(body.preview);
      setConsent(false);
      toast(err.message, "danger");
    } finally {
      setSubmitting(false);
    }
  }

  if (!canWrite) return <Empty title="Недостаточно прав">Создавать кампании может администратор или оператор.</Empty>;
  if (!mediaList || !accounts) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Новая кампания</h1>
          <p className="muted">Публикация одного видео на выбранные аккаунты через TikTok Direct Post.</p>
        </div>
      </header>

      <ol className="stepper">
        {STEPS.map((label, i) => (
          <li key={label} className={i === step ? "current" : i < step ? "done" : ""}>
            <button onClick={() => i < step && setStep(i)} disabled={i > step}>
              <span className="step-num">{i < step ? "✓" : i + 1}</span>
              {label}
            </button>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <section>
          <VideoUploader
            onUploaded={async (m) => {
              await loadMedia();
              if (m.validation_status === "valid") setMediaId(m.id);
            }}
          />
          {mediaList.length === 0 ? (
            <Empty title="В медиатеке нет видео">Загрузите файл выше.</Empty>
          ) : (
            <div className="media-pick">
              {mediaList.map((m) => {
                const valid = m.validation_status === "valid";
                return (
                  <label key={m.id} className={`card pick ${mediaId === m.id ? "picked" : ""} ${valid ? "" : "disabled"}`}>
                    <input
                      type="radio"
                      name="media"
                      checked={mediaId === m.id}
                      disabled={!valid}
                      onChange={() => setMediaId(m.id)}
                    />
                    <video src={`/api/media/${m.id}/file#t=0.5`} preload="metadata" muted playsInline />
                    <div>
                      <div className="media-title">{m.original_filename}</div>
                      <div className="muted small">
                        {formatDuration(m.duration_ms)} · {m.width}×{m.height} · {formatBytes(m.size_bytes)}
                      </div>
                      {!valid && <div className="small text-danger">{m.validation_issues[0]}</div>}
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </section>
      )}

      {step === 1 && (
        <AccountPicker accounts={accounts} groups={groups} selected={selected} onChange={setSelected} media={media} />
      )}

      {step === 2 && (
        <section className="form-grid">
          <div className="card">
            <h2>Публикация</h2>
            <label className="field">
              <span>Название кампании (видно только в панели)</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Например: Осенняя распродажа" />
            </label>
            <label className="field">
              <span>
                Подпись к видео{" "}
                <span className={captionLength(caption) > CAPTION_LIMIT ? "text-danger" : "muted"}>
                  {captionLength(caption)}/{CAPTION_LIMIT}
                </span>
              </span>
              <textarea
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                rows={5}
                placeholder="Текст, хештеги и упоминания"
              />
              <small className="muted">Для отдельных аккаунтов подпись можно изменить на шаге проверки.</small>
            </label>

            <fieldset className="field">
              <legend>Кто может смотреть видео</legend>
              <div className="radio-grid">
                {PRIVACY_ORDER.map((level) => {
                  const known = selectedAccounts.filter((a) => a.creator_info?.privacy_level_options);
                  const supported = known.filter((a) => a.creator_info?.privacy_level_options?.includes(level)).length;
                  return (
                    <label key={level} className={`radio-card ${privacy === level ? "picked" : ""}`}>
                      <input type="radio" name="privacy" checked={privacy === level} onChange={() => setPrivacy(level)} />
                      <strong>{PRIVACY_LABELS[level]}</strong>
                      <span className="small muted">
                        {known.length ? `доступно для ${supported} из ${known.length}` : "доступность проверится"}
                      </span>
                    </label>
                  );
                })}
              </div>
              <small className="muted">
                Варианты зависят от настроек каждого аккаунта. Без аудита приложения TikTok разрешает только «Только я».
              </small>
            </fieldset>

            <fieldset className="field">
              <legend>Разрешить зрителям</legend>
              <div className="checks">
                <label>
                  <input type="checkbox" checked={allowComment} onChange={(e) => setAllowComment(e.target.checked)} /> Комментарии
                </label>
                <label>
                  <input type="checkbox" checked={allowDuet} onChange={(e) => setAllowDuet(e.target.checked)} /> Duet
                </label>
                <label>
                  <input type="checkbox" checked={allowStitch} onChange={(e) => setAllowStitch(e.target.checked)} /> Stitch
                </label>
              </div>
              <small className="muted">Если взаимодействие выключено в настройках аккаунта, для него оно останется выключенным.</small>
            </fieldset>
          </div>

          <div className="card">
            <h2>Дополнительно</h2>
            <fieldset className="field">
              <label className="switch">
                <input type="checkbox" checked={disclose} onChange={(e) => setDisclose(e.target.checked)} />
                <span>Видео рекламирует бренд, товар или услугу</span>
              </label>
              {disclose && (
                <div className="nested">
                  <label>
                    <input type="checkbox" checked={brandOrganic} onChange={(e) => setBrandOrganic(e.target.checked)} />{" "}
                    <strong>Ваш бренд</strong> — продвижение себя или своего бизнеса
                  </label>
                  <label>
                    <input type="checkbox" checked={brandContent} onChange={(e) => setBrandContent(e.target.checked)} />{" "}
                    <strong>Брендированный контент</strong> — продвижение другого бренда за вознаграждение
                  </label>
                  {(brandOrganic || brandContent) && (
                    <div className="alert alert-info small">
                      Видео будет помечено как «{brandContent ? "Платное партнёрство" : "Рекламный контент"}».
                    </div>
                  )}
                </div>
              )}
            </fieldset>
            <label className="field switch">
              <input type="checkbox" checked={isAigc} onChange={(e) => setIsAigc(e.target.checked)} />
              <span>Контент создан с помощью ИИ</span>
            </label>
            <label className="field">
              <span>Кадр для обложки, секунда (необязательно)</span>
              <input value={coverSec} onChange={(e) => setCoverSec(e.target.value)} inputMode="decimal" placeholder="например, 1.5" />
            </label>
            <fieldset className="field">
              <legend>Запуск</legend>
              <div className="checks">
                <label>
                  <input type="radio" checked={startMode === "now"} onChange={() => setStartMode("now")} /> Сразу после подтверждения
                </label>
                <label>
                  <input type="radio" checked={startMode === "later"} onChange={() => setStartMode("later")} /> По расписанию
                </label>
              </div>
              {startMode === "later" && (
                <input type="datetime-local" value={startAt} onChange={(e) => setStartAt(e.target.value)} />
              )}
            </fieldset>
            <label className="field switch">
              <input type="checkbox" checked={allowDuplicates} onChange={(e) => setAllowDuplicates(e.target.checked)} />
              <span>Разрешить повторную публикацию этого видео на аккаунтах, где оно уже есть</span>
            </label>
          </div>

          {settingsErrors.length > 0 && (
            <ul className="error-list span-2">
              {settingsErrors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {step === 3 && (
        <ReviewStep
          preview={preview}
          loading={previewLoading}
          media={media}
          title={title}
          privacy={privacy}
          startAt={draft?.start_at ?? null}
          brandContent={disclose && brandContent}
          defaultCaption={caption}
          overrides={overrides}
          onOverride={(id, text) =>
            setOverrides((prev) => {
              const next = { ...prev };
              if (text === null) delete next[id];
              else next[id] = text;
              return next;
            })
          }
          consent={consent}
          onConsent={setConsent}
        />
      )}

      <div className="wizard-nav">
        {step > 0 && (
          <button className="btn" onClick={() => setStep(step - 1)}>
            Назад
          </button>
        )}
        <span className="grow" />
        {step === 1 && (
          <span className="muted">
            Выбрано {selected.size} {plural(selected.size, "аккаунт", "аккаунта", "аккаунтов")}
          </span>
        )}
        {step < 3 ? (
          <button className="btn btn-primary" disabled={!canNext} onClick={() => setStep(step + 1)}>
            Далее
          </button>
        ) : (
          <button
            className="btn btn-primary"
            disabled={
              !preview || previewLoading || submitting || !consent || preview.ready_count === 0 || preview.campaign_errors.length > 0
            }
            onClick={submit}
          >
            {submitting
              ? "Запуск…"
              : `Подтвердить и запустить${preview ? ` (${preview.ready_count})` : ""}`}
          </button>
        )}
      </div>
    </div>
  );
}

function AccountPicker({
  accounts,
  groups,
  selected,
  onChange,
  media,
}: {
  accounts: Account[];
  groups: Group[];
  selected: Set<number>;
  onChange: (next: Set<number>) => void;
  media: Media | null;
}) {
  const [search, setSearch] = useState("");
  const [groupId, setGroupId] = useState("");
  const q = search.trim().toLowerCase();
  const visible = accounts.filter(
    (a) =>
      (!groupId || a.groups.some((g) => String(g.id) === groupId)) &&
      (!q || [a.label, a.username, a.display_name].some((v) => v?.toLowerCase().includes(q))),
  );
  const selectable = visible.filter((a) => a.connection_status === "connected");
  const allSelected = selectable.length > 0 && selectable.every((a) => selected.has(a.id));

  function setMany(ids: number[], on: boolean) {
    const next = new Set(selected);
    ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
    onChange(next);
  }

  return (
    <section>
      <div className="toolbar">
        <input className="search" placeholder="Поиск аккаунта" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={groupId} onChange={(e) => setGroupId(e.target.value)} aria-label="Группа">
          <option value="">Все группы</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button className="btn" onClick={() => setMany(selectable.map((a) => a.id), !allSelected)} disabled={!selectable.length}>
          {allSelected ? "Снять все" : `Выбрать подключённые (${selectable.length})`}
        </button>
        {selected.size > 0 && (
          <button className="link-btn" onClick={() => onChange(new Set())}>
            Очистить выбор
          </button>
        )}
      </div>
      {accounts.length === 0 ? (
        <Empty title="Нет аккаунтов">Сначала подключите аккаунты на странице «Аккаунты».</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th className="col-check" />
                <th>Аккаунт</th>
                <th>Группы</th>
                <th>Статус</th>
                <th>Видимость</th>
                <th>Лимит длительности</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((a) => {
                const connected = a.connection_status === "connected";
                const maxSec = a.creator_info?.max_video_post_duration_sec;
                const tooLong = !!(maxSec && media?.duration_ms && media.duration_ms > maxSec * 1000);
                return (
                  <tr key={a.id} className={`${connected ? "" : "row-disabled"} ${selected.has(a.id) ? "row-selected" : ""}`}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        disabled={!connected}
                        checked={selected.has(a.id)}
                        onChange={(e) => setMany([a.id], e.target.checked)}
                        aria-label={`Выбрать ${a.label}`}
                      />
                    </td>
                    <td>
                      <div className="who">
                        <Avatar name={a.display_name || a.label} url={a.avatar_url} size={28} />
                        <div>
                          <div className="strong">{a.label}</div>
                          <div className="muted small">{a.username ? `@${a.username}` : "—"}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="chips">
                        {a.groups.map((g) => (
                          <span className="chip" key={g.id}>
                            {g.name}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <Badge tone={ACCOUNT_STATUS[a.connection_status].tone}>{ACCOUNT_STATUS[a.connection_status].label}</Badge>
                    </td>
                    <td className="small">
                      {a.creator_info?.privacy_level_options?.map((p) => PRIVACY_LABELS[p]).join(", ") || "не проверено"}
                    </td>
                    <td className={`small ${tooLong ? "text-danger" : "muted"}`}>
                      {maxSec ? `${maxSec} с${tooLong ? " — видео длиннее" : ""}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ReviewStep({
  preview,
  loading,
  media,
  title,
  privacy,
  startAt,
  brandContent,
  defaultCaption,
  overrides,
  onOverride,
  consent,
  onConsent,
}: {
  preview: CampaignPreview | null;
  loading: boolean;
  media: Media | null;
  title: string;
  privacy: PrivacyLevel | null;
  startAt: string | null;
  brandContent: boolean;
  defaultCaption: string;
  overrides: Record<number, string>;
  onOverride: (id: number, text: string | null) => void;
  consent: boolean;
  onConsent: (v: boolean) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [filter, setFilter] = useState<"all" | "ready" | "blocked">("all");
  if (!preview) return <Spinner />;
  const rows = preview.accounts.filter((c) => (filter === "all" ? true : filter === "ready" ? c.ok : !c.ok));

  return (
    <section className={loading ? "loading" : ""}>
      <div className="summary-grid">
        <div className="tile">
          <span className="muted small">Будет создано заданий</span>
          <strong className="tile-num">{preview.ready_count}</strong>
        </div>
        <div className="tile">
          <span className="muted small">Исключено</span>
          <strong className={`tile-num ${preview.blocked_count ? "text-danger" : ""}`}>{preview.blocked_count}</strong>
        </div>
        <div className="tile">
          <span className="muted small">Видео</span>
          <strong>{media?.original_filename}</strong>
          <span className="muted small">sha256 {media?.sha256.slice(0, 12)}…</span>
        </div>
        <div className="tile">
          <span className="muted small">Видимость · старт</span>
          <strong>{privacy ? PRIVACY_LABELS[privacy] : "—"}</strong>
          <span className="muted small">{startAt ? new Date(startAt).toLocaleString("ru-RU") : "сразу после подтверждения"}</span>
        </div>
      </div>

      {preview.campaign_errors.length > 0 && (
        <ul className="error-list">
          {preview.campaign_errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}

      <div className="toolbar">
        <div className="segmented">
          {(
            [
              ["all", `Все (${preview.accounts.length})`],
              ["ready", `Готовы (${preview.ready_count})`],
              ["blocked", `Исключены (${preview.blocked_count})`],
            ] as const
          ).map(([key, label]) => (
            <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>
              {label}
            </button>
          ))}
        </div>
        {loading && <span className="muted small">Пересчёт…</span>}
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Аккаунт</th>
              <th>Проверка</th>
              <th>Подпись</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.account_id} className={c.ok ? "" : "row-disabled"}>
                <td>
                  <div className="who">
                    <Avatar name={c.display_name || c.label} url={c.avatar_url} size={28} />
                    <div>
                      <div className="strong">{c.display_name || c.label}</div>
                      <div className="muted small">
                        {c.username ? `@${c.username}` : c.label} · проверен {formatRelative(c.creator_info_checked_at)}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="small">
                  {c.ok ? <Badge tone="success">Готов</Badge> : <Badge tone="danger">Исключён</Badge>}
                  {c.problems.map((p) => (
                    <div key={p} className="text-danger">
                      {p}
                    </div>
                  ))}
                  {c.warnings.map((w) => (
                    <div key={w} className="text-warning">
                      {w}
                    </div>
                  ))}
                </td>
                <td className="caption-cell">
                  {editing === c.account_id ? (
                    <CaptionEditor
                      initial={overrides[c.account_id] ?? defaultCaption}
                      onSave={(text) => {
                        onOverride(c.account_id, text === defaultCaption ? null : text);
                        setEditing(null);
                      }}
                      onCancel={() => setEditing(null)}
                    />
                  ) : (
                    <>
                      <div className="caption-text">{c.caption || <span className="muted">без подписи</span>}</div>
                      <div className="small">
                        {c.caption_overridden && <Badge tone="info">своя подпись</Badge>}{" "}
                        <button className="link-btn" onClick={() => setEditing(c.account_id)}>
                          Изменить
                        </button>
                        {c.caption_overridden && (
                          <>
                            {" · "}
                            <button className="link-btn" onClick={() => onOverride(c.account_id, null)}>
                              Сбросить
                            </button>
                          </>
                        )}
                      </div>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card consent">
        <h2>Подтверждение</h2>
        <p>
          Кампания «{title}»: видео будет опубликовано на <strong>{preview.ready_count}</strong>{" "}
          {plural(preview.ready_count, "аккаунт", "аккаунта", "аккаунтов")} с видимостью «{privacy ? PRIVACY_LABELS[privacy] : "—"}».
          После подтверждения параметры фиксируются; публикацию, которую TikTok уже принял, отменить из панели нельзя.
        </p>
        <p className="small muted">
          Публикуя, вы соглашаетесь с{" "}
          {brandContent && (
            <>
              <a href={BRANDED_POLICY} target="_blank" rel="noreferrer">
                Branded Content Policy
              </a>{" "}
              и{" "}
            </>
          )}
          <a href={MUSIC_POLICY} target="_blank" rel="noreferrer">
            Music Usage Confirmation
          </a>{" "}
          TikTok. Видео может появиться в профиле через несколько минут после обработки.
        </p>
        <label className="switch">
          <input
            type="checkbox"
            checked={consent}
            disabled={loading || preview.ready_count === 0}
            onChange={(e) => onConsent(e.target.checked)}
          />
          <span>
            Я проверил список и подтверждаю публикацию на {preview.ready_count}{" "}
            {plural(preview.ready_count, "аккаунт", "аккаунта", "аккаунтов")}
          </span>
        </label>
      </div>
    </section>
  );
}

function CaptionEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  return (
    <div className="caption-editor">
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} autoFocus />
      <div className="small">
        <span className={captionLength(text) > CAPTION_LIMIT ? "text-danger" : "muted"}>
          {captionLength(text)}/{CAPTION_LIMIT}
        </span>{" "}
        <button className="btn btn-sm btn-primary" onClick={() => onSave(text)}>
          Сохранить
        </button>{" "}
        <button className="btn btn-sm" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </div>
  );
}
