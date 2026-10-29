import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Empty, Spinner, useToast } from "../components/ui";
import { formatBytes, formatDateTime, formatDuration } from "../format";
import type { Media } from "../types";
import { VideoUploader } from "../components/VideoUploader";

export default function MediaPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const [items, setItems] = useState<Media[] | null>(null);

  const load = useCallback(async () => setItems(await api.get<Media[]>("/api/media")), []);

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  async function remove(media: Media) {
    if (!confirm(`Удалить «${media.original_filename}»?`)) return;
    try {
      await api.del(`/api/media/${media.id}`);
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
          <h1>Видео</h1>
          <p className="muted">Файл загружается один раз и используется во всех кампаниях.</p>
        </div>
      </header>

      {canWrite && <VideoUploader onUploaded={() => load()} />}

      {items.length === 0 ? (
        <Empty title="Видео пока нет">Загрузите MP4, MOV или WebM.</Empty>
      ) : (
        <div className="media-grid">
          {items.map((m) => (
            <article className="card media-card" key={m.id}>
              <video src={`/api/media/${m.id}/file`} preload="metadata" muted controls playsInline />
              <div className="media-info">
                <div className="media-title" title={m.original_filename}>
                  {m.original_filename}
                </div>
                <div className="muted small">
                  {formatDuration(m.duration_ms)} · {m.width && m.height ? `${m.width}×${m.height}` : "—"} ·{" "}
                  {formatBytes(m.size_bytes)}
                  {m.video_codec && ` · ${m.video_codec}`}
                  {m.fps && ` · ${Math.round(m.fps)} fps`}
                </div>
                <div className="muted small">
                  Загружено {formatDateTime(m.created_at)} · sha256 {m.sha256.slice(0, 10)}…
                </div>
                {m.validation_status === "valid" ? (
                  <Badge tone="success">Подходит для TikTok</Badge>
                ) : (
                  <>
                    <Badge tone="danger">Не подходит</Badge>
                    <ul className="error-list">
                      {m.validation_issues.map((issue) => (
                        <li key={issue}>{issue}</li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="media-actions">
                  {m.campaign_count > 0 && (
                    <span className="muted small">
                      В кампаниях: {m.campaign_count}
                    </span>
                  )}
                  {canWrite && m.validation_status === "valid" && (
                    <Link className="btn btn-sm btn-primary" to={`/campaigns/new?media=${m.id}`}>
                      Создать кампанию
                    </Link>
                  )}
                  {canWrite && m.campaign_count === 0 && (
                    <button className="btn btn-sm btn-danger-ghost" onClick={() => remove(m)}>
                      Удалить
                    </button>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
