/**
 * Часовой пояс панели. Все даты показываются и вводятся в поясе пользователя (настройка в календаре),
 * а не в поясе устройства: так время старта кампании одинаково на компьютере и на телефоне.
 */

export const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
let zone = browserZone;

export function setZone(value: string | null | undefined) {
  zone = value || browserZone;
}

export function getZone(): string {
  return zone;
}

interface Parts {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
}

const partsFormatters = new Map<string, Intl.DateTimeFormat>();

export function zonedParts(date: Date, tz = zone): Parts {
  let f = partsFormatters.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    });
    partsFormatters.set(tz, f);
  }
  const p = Object.fromEntries(f.formatToParts(date).map((x) => [x.type, x.value]));
  return { year: +p.year, month: +p.month, day: +p.day, hour: +p.hour, minute: +p.minute };
}

function offsetMs(date: Date, tz: string): number {
  const p = zonedParts(date, tz);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  return asUtc - Math.floor(date.getTime() / 60000) * 60000;
}

/** Момент времени для «настенного» времени в поясе tz (день и месяц могут выходить за границы). */
export function zonedDate(year: number, month: number, day: number, hour = 0, minute = 0, tz = zone): Date {
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  const offset = offsetMs(new Date(guess), tz);
  let result = guess - offset;
  // Переход на летнее время: смещение в найденный момент может отличаться.
  const second = offsetMs(new Date(result), tz);
  if (second !== offset) result = guess - second;
  return new Date(result);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Значение для <input type="datetime-local"> в поясе панели. */
export function toInput(date: Date): string {
  const p = zonedParts(date);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}`;
}

/** Значение <input type="datetime-local"> (время в поясе панели) → момент времени. */
export function fromInput(value: string): Date {
  const [d, t = "00:00"] = value.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [h, min] = t.split(":").map(Number);
  return zonedDate(y, m, day, h, min);
}

/** Ключ дня YYYY-MM-DD в поясе панели. */
export function dayKey(date: Date): string {
  const p = zonedParts(date);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

export function keyOf(year: number, month: number, day: number): string {
  return `${year}-${pad(month)}-${pad(day)}`;
}

/** Завтра в указанный час в поясе панели. */
export function tomorrowAt(hour: number): Date {
  const p = zonedParts(new Date());
  return zonedDate(p.year, p.month, p.day + 1, hour, 0);
}

/** Часто используемые пояса; пояс браузера и сохранённый добавляются к списку. */
export const COMMON_ZONES = [
  "Europe/Kaliningrad",
  "Europe/Moscow",
  "Europe/Samara",
  "Asia/Yekaterinburg",
  "Asia/Omsk",
  "Asia/Novosibirsk",
  "Asia/Krasnoyarsk",
  "Asia/Irkutsk",
  "Asia/Yakutsk",
  "Asia/Vladivostok",
  "Asia/Magadan",
  "Asia/Kamchatka",
  "Europe/Minsk",
  "Europe/Kyiv",
  "Asia/Almaty",
  "Asia/Tashkent",
  "Asia/Bishkek",
  "Asia/Tbilisi",
  "Asia/Yerevan",
  "Asia/Baku",
  "Europe/Istanbul",
  "Asia/Dubai",
  "Europe/Berlin",
  "Europe/London",
  "America/New_York",
  "UTC",
];
