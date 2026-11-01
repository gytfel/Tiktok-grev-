import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Empty, Spinner, StatusBar, useToast } from "../components/ui";
import { CAMPAIGN_STATUS, PRIVACY_LABELS, formatDateTime } from "../format";
import type { CampaignSummary } from "../types";

export default function CampaignsPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const [items, setItems] = useState<CampaignSummary[] | null>(null);

  async function remove(c: CampaignSummary) {
    const ok = confirm(
      `Удалить кампанию «${c.title}» из панели вместе с заданиями и статистикой?\n\n` +
        "Опубликованные видео останутся в TikTok — удалить их можно только в самом TikTok.",
    );
    if (!ok) return;
    try {
      await api.del(`/api/campaigns/${c.id}`);
      setItems((list) => list?.filter((x) => x.id !== c.id) ?? null);
      toast("Кампания удалена", "success");
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get<CampaignSummary[]>("/api/campaigns")
        .then((data) => alive && setItems(data))
        .catch((e) => toast(e.message, "danger"));
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [toast]);

  if (!items) return <Spinner />;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Кампании</h1>
          <p className="muted">Одно видео → выбранные аккаунты → отдельное задание на каждый аккаунт.</p>
        </div>
        {canWrite && (
          <Link className="btn btn-primary" to="/campaigns/new">
            Новая кампания
          </Link>
        )}
      </header>

      {items.length === 0 ? (
        <Empty title="Кампаний пока нет">Создайте кампанию: выберите видео, аккаунты и подтвердите публикацию.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table table-cards">
            <thead>
              <tr>
                <th>Кампания</th>
                <th>Статус</th>
                <th className="col-progress">Прогресс</th>
                <th>Видимость</th>
                <th>Подтверждена</th>
                {canWrite && <th className="col-actions" />}
              </tr>
            </thead>
            <tbody>
              {items.map((c) => {
                const published = c.counts.published ?? 0;
                const problems = (c.counts.failed ?? 0) + (c.counts.needs_reauth ?? 0) + (c.counts.needs_review ?? 0);
                return (
                  <tr key={c.id}>
                    <td className="cell-main">
                      <Link className="strong" to={`/campaigns/${c.id}`}>
                        {c.title}
                      </Link>
                      <div className="muted small">{c.media.original_filename}</div>
                    </td>
                    <td data-label="Статус">
                      <Badge tone={CAMPAIGN_STATUS[c.status].tone}>{CAMPAIGN_STATUS[c.status].label}</Badge>
                    </td>
                    <td className="col-progress cell-wide" data-label="Прогресс">
                      <StatusBar counts={c.counts} total={c.total} />
                      <div className="small muted">
                        {published} из {c.total} опубликовано
                        {problems > 0 && <span className="text-danger"> · {problems} требуют внимания</span>}
                      </div>
                    </td>
                    <td className="small" data-label="Видимость">{PRIVACY_LABELS[c.settings.privacy_level]}</td>
                    <td className="small muted" data-label="Подтверждена">
                      {formatDateTime(c.confirmed_at)}
                      <div>{c.confirmed_by ?? "—"}</div>
                    </td>
                    {canWrite && (
                      <td className="col-actions">
                        {!(c.counts.uploading || c.counts.processing) && (
                          <button className="link-btn small text-danger" onClick={() => remove(c)}>
                            Удалить
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
