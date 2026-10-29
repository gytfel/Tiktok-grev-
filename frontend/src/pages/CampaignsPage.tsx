import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../App";
import { Badge, Empty, Spinner, StatusBar, useToast } from "../components/ui";
import { CAMPAIGN_STATUS, PRIVACY_LABELS, formatDateTime } from "../format";
import type { CampaignSummary } from "../types";

export default function CampaignsPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const [items, setItems] = useState<CampaignSummary[] | null>(null);

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
          <table className="table">
            <thead>
              <tr>
                <th>Кампания</th>
                <th>Статус</th>
                <th className="col-progress">Прогресс</th>
                <th>Видимость</th>
                <th>Подтверждена</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => {
                const published = c.counts.published ?? 0;
                const problems = (c.counts.failed ?? 0) + (c.counts.needs_reauth ?? 0) + (c.counts.needs_review ?? 0);
                return (
                  <tr key={c.id}>
                    <td>
                      <Link className="strong" to={`/campaigns/${c.id}`}>
                        {c.title}
                      </Link>
                      <div className="muted small">{c.media.original_filename}</div>
                    </td>
                    <td>
                      <Badge tone={CAMPAIGN_STATUS[c.status].tone}>{CAMPAIGN_STATUS[c.status].label}</Badge>
                    </td>
                    <td className="col-progress">
                      <StatusBar counts={c.counts} total={c.total} />
                      <div className="small muted">
                        {published} из {c.total} опубликовано
                        {problems > 0 && <span className="text-danger"> · {problems} требуют внимания</span>}
                      </div>
                    </td>
                    <td className="small">{PRIVACY_LABELS[c.settings.privacy_level]}</td>
                    <td className="small muted">
                      {formatDateTime(c.confirmed_at)}
                      <div>{c.confirmed_by ?? "—"}</div>
                    </td>
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
