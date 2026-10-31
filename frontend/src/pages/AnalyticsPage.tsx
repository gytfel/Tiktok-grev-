import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { ChartCard, ColumnChart, LineChart, formatNumber } from "../components/charts";
import type { ChartPoint } from "../components/charts";
import { Empty, Spinner, useToast } from "../components/ui";
import { formatDateTime, plural } from "../format";

type Metric = "views" | "likes" | "comments" | "shares" | "followers";

interface Overview {
  enabled: boolean;
  interval_hours: number;
  totals: Record<Metric, number>;
  deltas: Record<Metric, number>;
  series: ({ day: string; views_gain: number } & Record<Metric, number>)[];
  top_campaigns: { id: number; title: string; views: number; likes: number; comments: number; shares: number; videos: number }[];
  top_accounts: {
    id: number;
    label: string;
    username: string | null;
    followers: number;
    followers_gain: number;
    likes: number;
    videos: number;
    views: number;
  }[];
  accounts_connected: number;
  accounts_missing_scopes: number;
  videos_tracked: number;
  videos_private: number;
  last_collected_at: string | null;
}

const PERIODS = [7, 30, 90] as const;
const TILES: { metric: Metric; label: string }[] = [
  { metric: "views", label: "Просмотры" },
  { metric: "likes", label: "Лайки" },
  { metric: "comments", label: "Комментарии" },
  { metric: "shares", label: "Репосты" },
  { metric: "followers", label: "Подписчики" },
];

function dayLabel(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}

function Delta({ value, days }: { value: number; days: number }) {
  if (value === 0) return <span className="muted small">без изменений за {days} дн.</span>;
  const tone = value > 0 ? "text-success" : "text-danger";
  return (
    <span className={`small ${tone}`}>
      {value > 0 ? "▲ +" : "▼ −"}
      {formatNumber(Math.abs(value))} <span className="muted">за {days} дн.</span>
    </span>
  );
}

export default function AnalyticsPage() {
  const { canWrite } = useSession();
  const toast = useToast();
  const [days, setDays] = useState<(typeof PERIODS)[number]>(30);
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.get<Overview>(`/api/analytics/overview?days=${days}`));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  async function refresh() {
    try {
      const r = await api.post<{ queued: number }>("/api/analytics/refresh");
      toast(`Сбор запущен для ${r.queued} ${plural(r.queued, "аккаунта", "аккаунтов", "аккаунтов")} — данные обновятся через минуту`, "success");
      setTimeout(() => load().catch(() => {}), 15000);
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  const viewsPoints: ChartPoint[] = useMemo(
    () => data?.series.map((p) => ({ key: p.day, label: dayLabel(p.day), value: p.views_gain })) ?? [],
    [data],
  );
  const followerPoints: ChartPoint[] = useMemo(
    () => data?.series.map((p) => ({ key: p.day, label: dayLabel(p.day), value: p.followers })) ?? [],
    [data],
  );

  if (!data) return <Spinner />;
  const hasData = data.videos_tracked > 0 || data.totals.followers > 0;
  const maxViews = Math.max(...data.top_campaigns.map((c) => c.views), 1);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Аналитика</h1>
          <p className="muted">
            Показатели ваших публичных видео и аккаунтов из TikTok. Сбор — раз в {data.interval_hours} ч
            {data.last_collected_at && <> · последний: {formatDateTime(data.last_collected_at)}</>}.
          </p>
        </div>
        <div className="actions">
          {(["videos", "campaigns", "accounts"] as const).map((kind) => (
            <a key={kind} className="btn btn-sm" href={`/api/analytics/export?kind=${kind}`}>
              CSV: {kind === "videos" ? "видео" : kind === "campaigns" ? "кампании" : "аккаунты"}
            </a>
          ))}
          {canWrite && data.enabled && (
            <button className="btn btn-primary" onClick={refresh}>
              Обновить сейчас
            </button>
          )}
        </div>
      </header>

      {!data.enabled && (
        <div className="alert alert-info">
          Аналитика выключена. Чтобы её включить, добавьте в приложение TikTok продукт Display API (разрешения{" "}
          <code>user.info.stats</code> и <code>video.list</code>), укажите <code>TIKTOK_ANALYTICS=true</code> в .env и
          переподключите аккаунты. Подробнее — в README, раздел «Аналитика».
        </div>
      )}
      {data.enabled && data.accounts_missing_scopes > 0 && (
        <div className="alert alert-info">
          {data.accounts_missing_scopes} {plural(data.accounts_missing_scopes, "аккаунт подключён", "аккаунта подключены", "аккаунтов подключены")}{" "}
          без разрешений на статистику. Переподключите их на странице <Link to="/accounts">«Аккаунты»</Link>.
        </div>
      )}
      {data.videos_private > 0 && (
        <p className="hint">
          {data.videos_private} {plural(data.videos_private, "видео опубликовано", "видео опубликованы", "видео опубликованы")} с
          ограниченной видимостью — TikTok не даёт статистику таких видео.
        </p>
      )}

      <div className="segmented" role="group" aria-label="Период">
        {PERIODS.map((p) => (
          <button key={p} className={days === p ? "active" : ""} onClick={() => setDays(p)}>
            {p} дней
          </button>
        ))}
      </div>

      {!hasData ? (
        <Empty title="Данных пока нет">
          {data.enabled
            ? "Статистика появится после первого сбора. Нажмите «Обновить сейчас» или подождите плановый сбор."
            : "Включите аналитику, чтобы видеть просмотры, лайки и подписчиков."}
        </Empty>
      ) : (
        <div className={loading ? "loading" : ""}>
          <div className="kpi-row">
            {TILES.map(({ metric, label }) => (
              <div className="tile kpi" key={metric}>
                <span className="muted small">{label}</span>
                <strong className="kpi-value">{formatNumber(data.totals[metric])}</strong>
                <Delta value={data.deltas[metric]} days={days} />
              </div>
            ))}
          </div>

          <div className="chart-grid-2">
            <ChartCard title="Просмотры за день" subtitle="Сколько просмотров набрали все видео за каждый день" points={viewsPoints} valueTitle="Просмотры">
              <ColumnChart points={viewsPoints} seriesName="Просмотры" />
            </ChartCard>
            <ChartCard title="Подписчики" subtitle="Сумма подписчиков всех аккаунтов на конец дня" points={followerPoints} valueTitle="Подписчики">
              <LineChart points={followerPoints} seriesName="Подписчики" />
            </ChartCard>
          </div>

          <section className="card">
            <h2>Лучшие кампании</h2>
            {data.top_campaigns.length === 0 ? (
              <p className="muted">Статистики по видео пока нет.</p>
            ) : (
              <div className="table-wrap flat">
                <table className="table table-cards">
                  <thead>
                    <tr>
                      <th>Кампания</th>
                      <th className="num">Видео</th>
                      <th className="col-views">Просмотры</th>
                      <th className="num">Лайки</th>
                      <th className="num">Комментарии</th>
                      <th className="num">Репосты</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.top_campaigns.map((c) => (
                      <tr key={c.id}>
                        <td className="cell-main">
                          <Link to={`/campaigns/${c.id}`}>{c.title}</Link>
                        </td>
                        <td className="num" data-label="Видео">{c.videos}</td>
                        <td className="col-views cell-wide" data-label="Просмотры">
                          <span className="inline-bar">
                            <span style={{ width: `${(c.views / maxViews) * 100}%` }} />
                          </span>
                          <span className="num">{formatNumber(c.views)}</span>
                        </td>
                        <td className="num" data-label="Лайки">{formatNumber(c.likes)}</td>
                        <td className="num" data-label="Комментарии">{formatNumber(c.comments)}</td>
                        <td className="num" data-label="Репосты">{formatNumber(c.shares)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card">
            <h2>Аккаунты</h2>
            {data.top_accounts.length === 0 ? (
              <p className="muted">Статистики по аккаунтам пока нет.</p>
            ) : (
              <>
                <div className="table-wrap flat">
                  <table className="table table-cards">
                    <thead>
                      <tr>
                        <th>Аккаунт</th>
                        <th className="num">Подписчики</th>
                        <th className="num">Прирост за {days} дн.</th>
                        <th className="num">Лайки</th>
                        <th className="num">Просмотры опубликованных</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.top_accounts.map((a) => (
                        <tr key={a.id}>
                          <td className="cell-main">
                            <span className="strong">{a.username ? `@${a.username}` : a.label}</span>
                            {a.username !== a.label && <div className="muted small">{a.label}</div>}
                          </td>
                          <td className="num" data-label="Подписчики">{formatNumber(a.followers)}</td>
                          <td className="num" data-label="Прирост">
                            <span className={a.followers_gain > 0 ? "text-success" : a.followers_gain < 0 ? "text-danger" : "muted"}>
                              {a.followers_gain > 0 ? "+" : ""}
                              {formatNumber(a.followers_gain)}
                            </span>
                          </td>
                          <td className="num" data-label="Лайки">{formatNumber(a.likes)}</td>
                          <td className="num" data-label="Просмотры">{formatNumber(a.views)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted small">Показаны 20 аккаунтов с наибольшим числом подписчиков. Полный список — в CSV.</p>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
