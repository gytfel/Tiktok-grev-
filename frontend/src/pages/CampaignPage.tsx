import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Spinner, StatusBar, useToast } from "../components/ui";
import { formatNumber } from "../components/charts";
import {
  CAMPAIGN_STATUS,
  JOB_STATUS,
  JOB_STATUS_ORDER,
  PRIVACY_LABELS,
  formatBytes,
  formatDateTime,
  formatDuration,
  tiktokPostUrl,
} from "../format";
import type { Attempt, CampaignDetail, Job, JobStatus } from "../types";

export default function CampaignPage() {
  const { id } = useParams();
  const { canWrite } = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [filter, setFilter] = useState<JobStatus | "all" | "attention">("all");
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(
    () => api.get<CampaignDetail>(`/api/campaigns/${id}`).then(setCampaign),
    [id],
  );

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  const running = campaign?.status === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => load().catch(() => {}), 4000);
    return () => clearInterval(timer);
  }, [running, load]);

  const jobs = useMemo(() => {
    if (!campaign) return [];
    if (filter === "all") return campaign.jobs;
    if (filter === "attention")
      return campaign.jobs.filter((j) => ["failed", "needs_reauth", "needs_review"].includes(j.status));
    return campaign.jobs.filter((j) => j.status === filter);
  }, [campaign, filter]);

  async function remove() {
    if (!campaign) return;
    const ok = confirm(
      `Удалить кампанию «${campaign.title}» из панели вместе с заданиями и статистикой?\n\n` +
        "Опубликованные видео останутся в TikTok — удалить их можно только в самом TikTok.",
    );
    if (!ok) return;
    try {
      await api.del(`/api/campaigns/${campaign.id}`);
      toast("Кампания удалена", "success");
      navigate("/campaigns");
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  async function act(path: string, body?: unknown, success?: string) {
    try {
      const result = await api.post<{ retried?: number; cancelled?: number }>(path, body);
      if (success) toast(success.replace("{n}", String(result.retried ?? result.cancelled ?? "")), "success");
      await load();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  if (!campaign) return <Spinner />;
  const s = campaign.settings;
  const attention = (campaign.counts.failed ?? 0) + (campaign.counts.needs_reauth ?? 0) + (campaign.counts.needs_review ?? 0);
  const retryable = campaign.jobs.filter((j) => (j.status === "failed" && j.retryable) || j.status === "needs_reauth").length;
  const queued = campaign.counts.queued ?? 0;
  const uploading = (campaign.counts.uploading ?? 0) + (campaign.counts.processing ?? 0);
  const interactions = [s.allow_comment && "комментарии", s.allow_duet && "Duet", s.allow_stitch && "Stitch"].filter(Boolean);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <Link to="/campaigns" className="muted small">
            ← Кампании
          </Link>
          <h1>
            {campaign.title} <Badge tone={CAMPAIGN_STATUS[campaign.status].tone}>{CAMPAIGN_STATUS[campaign.status].label}</Badge>
          </h1>
          <p className="muted">
            Подтвердил {campaign.confirmed_by ?? "—"} {formatDateTime(campaign.confirmed_at)} · старт{" "}
            {formatDateTime(campaign.start_at)}
          </p>
        </div>
        {canWrite && (
          <div className="actions">
            {retryable > 0 && (
              <button
                className="btn"
                onClick={() => act(`/api/campaigns/${campaign.id}/retry-failed`, undefined, "Повторно поставлено в очередь: {n}")}
              >
                Повторить неудачные ({retryable})
              </button>
            )}
            {queued > 0 && campaign.status !== "cancelled" && (
              <button
                className="btn btn-danger-ghost"
                onClick={() =>
                  confirm(`Отменить ${queued} заданий, которые ещё ждут в очереди? Начатые публикации завершатся.`) &&
                  act(`/api/campaigns/${campaign.id}/cancel`, undefined, "Отменено заданий: {n}")
                }
              >
                Отменить оставшиеся
              </button>
            )}
            <button
              className="btn btn-danger-ghost"
              onClick={remove}
              disabled={uploading > 0}
              title={uploading > 0 ? "Идёт загрузка в TikTok — удалить можно после её завершения" : undefined}
            >
              Удалить
            </button>
          </div>
        )}
      </header>

      {campaign.stats && (
        <div className="kpi-row">
          {(
            [
              ["views", "Просмотры"],
              ["likes", "Лайки"],
              ["comments", "Комментарии"],
              ["shares", "Репосты"],
            ] as const
          ).map(([key, label]) => (
            <div className="tile kpi" key={key}>
              <span className="muted small">{label}</span>
              <strong className="kpi-value">{formatNumber(campaign.stats![key])}</strong>
              <span className="muted small">по {campaign.stats!.videos} видео</span>
            </div>
          ))}
        </div>
      )}

      <div className="campaign-top">
        <div className="card campaign-media">
          <video src={`/api/media/${campaign.media.id}/file`} preload="metadata" muted controls playsInline />
          <div className="small muted">
            {campaign.media.original_filename} · {formatDuration(campaign.media.duration_ms)} ·{" "}
            {formatBytes(campaign.media.size_bytes)}
          </div>
        </div>
        <div className="card">
          <dl className="props">
            <dt>Подпись</dt>
            <dd className="caption-text">{campaign.default_caption || <span className="muted">без подписи</span>}</dd>
            <dt>Видимость</dt>
            <dd>{PRIVACY_LABELS[s.privacy_level]}</dd>
            <dt>Взаимодействия</dt>
            <dd>{interactions.length ? interactions.join(", ") : "выключены"}</dd>
            <dt>Коммерческий контент</dt>
            <dd>
              {s.brand_content ? "брендированный контент" : s.brand_organic ? "ваш бренд" : "нет"}
              {s.is_aigc && " · создано ИИ"}
            </dd>
          </dl>
          <StatusBar counts={campaign.counts} total={campaign.total} />
          <div className="status-legend">
            {JOB_STATUS_ORDER.filter((st) => campaign.counts[st]).map((st) => (
              <span key={st}>
                <i className={`dot seg-${JOB_STATUS[st].tone}`} /> {JOB_STATUS[st].label}: {campaign.counts[st]}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="toolbar">
        <div className="segmented">
          <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
            Все ({campaign.total})
          </button>
          {attention > 0 && (
            <button className={filter === "attention" ? "active" : ""} onClick={() => setFilter("attention")}>
              Требуют внимания ({attention})
            </button>
          )}
          {JOB_STATUS_ORDER.filter((st) => campaign.counts[st]).map((st) => (
            <button key={st} className={filter === st ? "active" : ""} onClick={() => setFilter(st)}>
              {JOB_STATUS[st].label} ({campaign.counts[st]})
            </button>
          ))}
        </div>
        {running && <span className="muted small">Обновляется автоматически</span>}
      </div>

      <div className="table-wrap">
        <table className="table table-cards">
          <thead>
            <tr>
              <th>Аккаунт</th>
              <th>Статус</th>
              <th>Результат</th>
              <th>Попытки</th>
              {campaign.stats && <th className="num">Просмотры</th>}
              {canWrite && <th className="col-actions" />}
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <Fragment key={job.id}>
                <tr>
                  <td className="cell-main">
                    <div className="strong">{job.account_display_name || job.account_label}</div>
                    <div className="muted small">
                      {job.account_username ? `@${job.account_username}` : job.account_label}
                      {job.caption_overridden && " · своя подпись"}
                    </div>
                  </td>
                  <td data-label="Статус">
                    <Badge tone={JOB_STATUS[job.status].tone}>{JOB_STATUS[job.status].label}</Badge>
                    {job.status === "queued" && job.last_error_code && (
                      <div className="small muted">повтор в {formatDateTime(job.scheduled_at)}</div>
                    )}
                  </td>
                  <td className="small cell-wide" data-label="Результат">
                    <JobResult job={job} />
                  </td>
                  <td className="small" data-label="Попытки">
                    <button className="link-btn" onClick={() => setExpanded(expanded === job.id ? null : job.id)}>
                      {job.attempt_total} {expanded === job.id ? "▴" : "▾"}
                    </button>
                  </td>
                  {campaign.stats && (
                    <td className="small num" data-label="Просмотры" title={job.views === null ? "" : `Лайки: ${job.likes}, комментарии: ${job.comments}, репосты: ${job.shares}`}>
                      {job.views === null ? "—" : formatNumber(job.views)}
                    </td>
                  )}
                  {canWrite && (
                    <td className="col-actions">
                      <JobActions job={job} act={act} />
                    </td>
                  )}
                </tr>
                {expanded === job.id && (
                  <tr className="row-detail">
                    <td colSpan={(canWrite ? 5 : 4) + (campaign.stats ? 1 : 0)} className="cell-wide">
                      <Attempts jobId={job.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobResult({ job }: { job: Job }) {
  if (job.status === "published") {
    const url = tiktokPostUrl(job.account_username, job.external_post_id);
    return (
      <>
        {formatDateTime(job.published_at)}
        {url ? (
          <div>
            <a href={url} target="_blank" rel="noreferrer">
              Открыть в TikTok
            </a>
          </div>
        ) : (
          <div className="muted">ссылка появляется только у публичных видео</div>
        )}
      </>
    );
  }
  if (job.last_error_message) {
    return (
      <span className={job.status === "queued" ? "text-warning" : "text-danger"} title={job.last_error_code ?? ""}>
        {job.last_error_message}
      </span>
    );
  }
  if (job.status === "processing") return <span className="muted">TikTok обрабатывает видео</span>;
  return <span className="muted">—</span>;
}

function JobActions({ job, act }: { job: Job; act: (path: string, body?: unknown, success?: string) => void }) {
  if (job.status === "published") {
    return (
      <Link className="btn btn-sm" to={`/comments?job=${job.id}`}>
        Ответ на комментарий
      </Link>
    );
  }
  if ((job.status === "failed" && job.retryable) || job.status === "needs_reauth") {
    return (
      <button className="btn btn-sm" onClick={() => act(`/api/jobs/${job.id}/retry`, undefined, "Задание снова в очереди")}>
        Повторить
      </button>
    );
  }
  if (job.status === "queued") {
    return (
      <button className="btn btn-sm btn-danger-ghost" onClick={() => act(`/api/jobs/${job.id}/cancel`)}>
        Отменить
      </button>
    );
  }
  if (job.status === "needs_review") {
    return (
      <div className="row-actions">
        <button
          className="btn btn-sm"
          title="Видео есть в профиле"
          onClick={() => act(`/api/jobs/${job.id}/resolve`, { outcome: "published" }, "Отмечено как опубликованное")}
        >
          Опубликовано
        </button>
        <button
          className="btn btn-sm"
          title="Видео нет в профиле — поставить в очередь снова"
          onClick={() =>
            confirm("Вы проверили профиль и видео там нет? Задание будет поставлено в очередь снова.") &&
            act(`/api/jobs/${job.id}/resolve`, { outcome: "not_published" }, "Задание снова в очереди")
          }
        >
          Не опубликовано
        </button>
      </div>
    );
  }
  return null;
}

function Attempts({ jobId }: { jobId: number }) {
  const [items, setItems] = useState<Attempt[] | null>(null);
  useEffect(() => {
    api.get<Attempt[]>(`/api/jobs/${jobId}/attempts`).then(setItems).catch(() => setItems([]));
  }, [jobId]);
  if (!items) return <Spinner />;
  if (!items.length) return <span className="muted small">Попыток ещё не было.</span>;
  return (
    <table className="table table-inner table-cards">
      <thead>
        <tr>
          <th>#</th>
          <th>Начало</th>
          <th>Конец</th>
          <th>Загружено</th>
          <th>Итог</th>
          <th>Детали</th>
        </tr>
      </thead>
      <tbody>
        {items.map((a) => (
          <tr key={a.id}>
            <td className="cell-main">#{a.attempt_number}</td>
            <td data-label="Начало">{formatDateTime(a.started_at)}</td>
            <td data-label="Конец">{formatDateTime(a.finished_at)}</td>
            <td data-label="Загружено">{formatBytes(a.uploaded_bytes)}</td>
            <td data-label="Итог">
              <code>{a.result_code ?? "—"}</code>
            </td>
            <td className="small muted break cell-wide" data-label="Детали">
              {a.publish_id && <div>publish_id: {a.publish_id}</div>}
              {a.error_details}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
