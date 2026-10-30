import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useSession } from "../App";
import { Badge, Spinner, useToast } from "../components/ui";
import { CAMPAIGN_STATUS, plural } from "../format";
import {
  COMMON_ZONES,
  browserZone,
  dayKey,
  fromInput,
  getZone,
  keyOf,
  toInput,
  zonedDate,
  zonedParts,
} from "../time";
import type { CampaignStatus, Reminder } from "../types";

interface CalendarCampaign {
  id: number;
  title: string;
  status: CampaignStatus;
  start_at: string;
  media_filename: string;
  total: number;
  published: number;
  problems: number;
  planned: boolean;
}

interface CalendarData {
  campaigns: CalendarCampaign[];
  reminders: Reminder[];
  days: Record<string, { published: number; problems: number }>;
}

interface Cell {
  year: number;
  month: number;
  day: number;
  key: string;
}

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const MAX_CHIPS = 3;

const pad = (n: number) => String(n).padStart(2, "0");

function timeOf(iso: string): string {
  const p = zonedParts(new Date(iso));
  return `${pad(p.hour)}:${pad(p.minute)}`;
}

function monthTitle(year: number, month: number): string {
  const text = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, 15)),
  );
  return text.charAt(0).toUpperCase() + text.slice(1).replace(" г.", "");
}

function dayTitle(cell: Cell): string {
  return new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(cell.year, cell.month - 1, cell.day, 12)),
  );
}

function campaignTone(c: CalendarCampaign) {
  if (c.planned) return "planned";
  if (c.problems > 0 && c.status !== "running") return "warning";
  return CAMPAIGN_STATUS[c.status].tone;
}

export default function CalendarPage() {
  const { canWrite, user, refresh } = useSession();
  const toast = useToast();
  const now = zonedParts(new Date());
  const todayKey = keyOf(now.year, now.month, now.day);
  const [cursor, setCursor] = useState({ year: now.year, month: now.month });
  const [selected, setSelected] = useState(todayKey);
  const [data, setData] = useState<CalendarData | null>(null);

  const cells = useMemo<Cell[]>(() => {
    const first = new Date(Date.UTC(cursor.year, cursor.month - 1, 1));
    const shift = (first.getUTCDay() + 6) % 7; // неделя начинается с понедельника
    const list: Cell[] = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(Date.UTC(cursor.year, cursor.month - 1, 1 - shift + i));
      const cell = { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
      list.push({ ...cell, key: keyOf(cell.year, cell.month, cell.day) });
    }
    return list[35].month !== cursor.month ? list.slice(0, 35) : list;
  }, [cursor]);

  const load = useCallback(async () => {
    const first = cells[0];
    const last = cells[cells.length - 1];
    const query = new URLSearchParams({
      start: zonedDate(first.year, first.month, first.day).toISOString(),
      end: zonedDate(last.year, last.month, last.day + 1).toISOString(),
      tz: getZone(),
    });
    setData(await api.get<CalendarData>(`/api/calendar?${query}`));
    // user.timezone в зависимостях: после смены пояса календарь перезагружается.
  }, [cells, user.timezone]);

  useEffect(() => {
    load().catch((e) => toast(e.message, "danger"));
  }, [load, toast]);

  const byDay = useMemo(() => {
    const map = new Map<string, { campaigns: CalendarCampaign[]; reminders: Reminder[] }>();
    const get = (key: string) => {
      if (!map.has(key)) map.set(key, { campaigns: [], reminders: [] });
      return map.get(key)!;
    };
    data?.campaigns.forEach((c) => get(dayKey(new Date(c.start_at))).campaigns.push(c));
    data?.reminders.forEach((r) => get(dayKey(new Date(r.due_at))).reminders.push(r));
    return map;
  }, [data]);

  function move(delta: number) {
    setCursor(({ year, month }) => {
      const d = new Date(Date.UTC(year, month - 1 + delta, 1));
      return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1 };
    });
  }

  function goToday() {
    setCursor({ year: now.year, month: now.month });
    setSelected(todayKey);
  }

  async function changeZone(value: string) {
    try {
      await api.patch("/api/auth/preferences", { timezone: value || null });
      await refresh();
      toast(`Часовой пояс: ${value || `как на устройстве (${browserZone})`}`, "success");
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  const zones = [...new Set([user.timezone, browserZone, ...COMMON_ZONES].filter(Boolean) as string[])];
  const selectedCell = cells.find((c) => c.key === selected) ?? null;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Календарь</h1>
          <p className="muted">Запланированные и прошедшие кампании, напоминания и итоги публикаций по дням.</p>
        </div>
        <label className="zone-select">
          <span className="muted small">Часовой пояс</span>
          <select value={user.timezone ?? ""} onChange={(e) => changeZone(e.target.value)}>
            <option value="">Как на устройстве ({browserZone})</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
      </header>

      <div className="cal-toolbar">
        <button className="icon-btn" onClick={() => move(-1)} aria-label="Предыдущий месяц">
          ‹
        </button>
        <h2 className="cal-title">{monthTitle(cursor.year, cursor.month)}</h2>
        <button className="icon-btn" onClick={() => move(1)} aria-label="Следующий месяц">
          ›
        </button>
        <button className="btn btn-sm" onClick={goToday}>
          Сегодня
        </button>
        <div className="cal-legend small muted">
          <span>
            <i className="dot cal-planned" /> запланирована
          </span>
          <span>
            <i className="dot seg-info" /> выполняется
          </span>
          <span>
            <i className="dot seg-success" /> завершена
          </span>
          <span>
            <i className="dot seg-warning" /> есть проблемы
          </span>
          <span>🔔 напоминание</span>
        </div>
      </div>

      {!data ? (
        <Spinner />
      ) : (
        <div className="cal-layout">
          <div className="cal-grid" role="grid">
            {WEEKDAYS.map((d) => (
              <div key={d} className="cal-weekday">
                {d}
              </div>
            ))}
            {cells.map((cell) => {
              const events = byDay.get(cell.key);
              const summary = data.days[cell.key];
              const chips = [
                ...(events?.campaigns.map((c) => ({ kind: "c" as const, c })) ?? []),
                ...(events?.reminders.map((r) => ({ kind: "r" as const, r })) ?? []),
              ];
              return (
                <button
                  key={cell.key}
                  data-day={cell.key}
                  className={[
                    "cal-cell",
                    cell.month !== cursor.month ? "other" : "",
                    cell.key === todayKey ? "today" : "",
                    cell.key === selected ? "selected" : "",
                  ].join(" ")}
                  onClick={() => setSelected(cell.key)}
                >
                  <span className="cal-day">{cell.day}</span>
                  <span className="cal-chips">
                    {chips.slice(0, MAX_CHIPS).map((item) =>
                      item.kind === "c" ? (
                        <span key={`c${item.c.id}`} className={`cal-chip tone-${campaignTone(item.c)}`}>
                          <b>{timeOf(item.c.start_at)}</b> {item.c.title}
                        </span>
                      ) : (
                        <span
                          key={`r${item.r.id}`}
                          className={`cal-chip tone-reminder ${item.r.done_at ? "done" : ""}`}
                        >
                          🔔 <b>{timeOf(item.r.due_at)}</b> {item.r.title}
                        </span>
                      ),
                    )}
                    {chips.length > MAX_CHIPS && <span className="cal-more">ещё {chips.length - MAX_CHIPS}</span>}
                  </span>
                  {summary && (
                    <span className="cal-summary">
                      {summary.published > 0 && <span className="text-success">✓{summary.published}</span>}
                      {summary.problems > 0 && <span className="text-danger">✕{summary.problems}</span>}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {selectedCell && (
            <DayPanel
              cell={selectedCell}
              isPast={selectedCell.key < todayKey}
              isToday={selectedCell.key === todayKey}
              campaigns={byDay.get(selectedCell.key)?.campaigns ?? []}
              reminders={byDay.get(selectedCell.key)?.reminders ?? []}
              summary={data.days[selectedCell.key]}
              canWrite={canWrite}
              onChanged={() => load()}
            />
          )}
        </div>
      )}
    </div>
  );
}

function DayPanel({
  cell,
  isPast,
  isToday,
  campaigns,
  reminders,
  summary,
  canWrite,
  onChanged,
}: {
  cell: Cell;
  isPast: boolean;
  isToday: boolean;
  campaigns: CalendarCampaign[];
  reminders: Reminder[];
  summary?: { published: number; problems: number };
  canWrite: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [time, setTime] = useState("10:00");

  function planTime(): string {
    if (!isToday) return `${cell.key}T10:00`;
    // Сегодня — ближайший целый час.
    return toInput(new Date(Math.ceil((Date.now() + 60_000) / 3600_000) * 3600_000));
  }

  async function addReminder(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/reminders", { title, due_at: fromInput(`${cell.key}T${time}`).toISOString() });
      setTitle("");
      toast("Напоминание добавлено", "success");
      onChanged();
    } catch (err) {
      toast((err as ApiError).message, "danger");
    }
  }

  const empty = campaigns.length === 0 && reminders.length === 0;

  return (
    <aside className="card day-panel">
      <h2>{dayTitle(cell)}</h2>
      {summary && (
        <p className="small">
          Опубликовано за день: <strong>{summary.published}</strong>
          {summary.problems > 0 && (
            <>
              {" "}
              · требуют внимания: <strong className="text-danger">{summary.problems}</strong>
            </>
          )}
        </p>
      )}
      {empty && <p className="muted">На этот день ничего не запланировано.</p>}

      {campaigns.map((c) => (
        <DayCampaign key={c.id} campaign={c} canWrite={canWrite} onChanged={onChanged} />
      ))}
      {reminders.map((r) => (
        <DayReminder key={r.id} reminder={r} canWrite={canWrite} onChanged={onChanged} />
      ))}

      {canWrite && !isPast && (
        <div className="day-actions">
          <button className="btn btn-primary" onClick={() => navigate(`/campaigns/new?start=${encodeURIComponent(planTime())}`)}>
            Запланировать кампанию
          </button>
          <form className="form-row" onSubmit={addReminder}>
            <input placeholder="Напоминание на этот день" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} />
            <input type="time" value={time} onChange={(e) => setTime(e.target.value)} required className="time-input" />
            <button className="btn">Добавить</button>
          </form>
        </div>
      )}
    </aside>
  );
}

function DayCampaign({
  campaign: c,
  canWrite,
  onChanged,
}: {
  campaign: CalendarCampaign;
  canWrite: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [when, setWhen] = useState(toInput(new Date(c.start_at)));

  async function reschedule() {
    try {
      await api.post(`/api/campaigns/${c.id}/reschedule`, { start_at: fromInput(when).toISOString() });
      toast("Кампания перенесена", "success");
      onChanged();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  async function cancel() {
    if (!confirm(`Отменить кампанию «${c.title}»? Ни одно видео ещё не опубликовано.`)) return;
    try {
      await api.post(`/api/campaigns/${c.id}/cancel`);
      toast("Кампания отменена", "info");
      onChanged();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  return (
    <div className={`day-item tone-${campaignTone(c)}`}>
      <div className="day-item-head">
        <b>{timeOf(c.start_at)}</b>
        <Link className="strong" to={`/campaigns/${c.id}`}>
          {c.title}
        </Link>
        {c.planned ? <Badge tone="info">запланирована</Badge> : <Badge tone={CAMPAIGN_STATUS[c.status].tone}>{CAMPAIGN_STATUS[c.status].label}</Badge>}
      </div>
      <div className="small muted">
        {c.media_filename} · {c.total} {plural(c.total, "аккаунт", "аккаунта", "аккаунтов")}
        {!c.planned && ` · опубликовано ${c.published}`}
        {c.problems > 0 && <span className="text-danger"> · требуют внимания: {c.problems}</span>}
      </div>
      {canWrite && c.planned && (
        <div className="form-row">
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
          <button className="btn btn-sm" onClick={reschedule}>
            Перенести
          </button>
          <button className="btn btn-sm btn-danger-ghost" onClick={cancel}>
            Отменить
          </button>
        </div>
      )}
    </div>
  );
}

function DayReminder({
  reminder: r,
  canWrite,
  onChanged,
}: {
  reminder: Reminder;
  canWrite: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [when, setWhen] = useState(toInput(new Date(r.due_at)));

  async function act(fn: () => Promise<unknown>, message: string) {
    try {
      await fn();
      toast(message, "success");
      onChanged();
    } catch (e) {
      toast((e as ApiError).message, "danger");
    }
  }

  return (
    <div className={`day-item tone-reminder ${r.done_at ? "done" : ""}`}>
      <div className="day-item-head">
        <b>🔔 {timeOf(r.due_at)}</b>
        <span className="strong">{r.title}</span>
        {r.done_at && <Badge tone="muted">выполнено</Badge>}
      </div>
      {r.note && <div className="small muted">{r.note}</div>}
      <div className="form-row">
        {r.link && (
          <Link className="btn btn-sm" to={r.link}>
            Открыть
          </Link>
        )}
        {canWrite && !r.done_at && (
          <>
            <button className="btn btn-sm" onClick={() => act(() => api.post(`/api/reminders/${r.id}/done`), "Отмечено как выполненное")}>
              Выполнено
            </button>
            <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
            <button
              className="btn btn-sm"
              onClick={() => act(() => api.patch(`/api/reminders/${r.id}`, { due_at: fromInput(when).toISOString() }), "Напоминание перенесено")}
            >
              Перенести
            </button>
          </>
        )}
      </div>
    </div>
  );
}
