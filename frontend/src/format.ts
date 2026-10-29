import type { AccountStatus, CampaignStatus, JobStatus, PrivacyLevel } from "./types";

export const PRIVACY_LABELS: Record<PrivacyLevel, string> = {
  PUBLIC_TO_EVERYONE: "Все",
  MUTUAL_FOLLOW_FRIENDS: "Друзья",
  FOLLOWER_OF_CREATOR: "Подписчики",
  SELF_ONLY: "Только я",
};

export const PRIVACY_ORDER: PrivacyLevel[] = [
  "PUBLIC_TO_EVERYONE",
  "FOLLOWER_OF_CREATOR",
  "MUTUAL_FOLLOW_FRIENDS",
  "SELF_ONLY",
];

export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "muted";

export const JOB_STATUS: Record<JobStatus, { label: string; tone: Tone }> = {
  queued: { label: "В очереди", tone: "neutral" },
  uploading: { label: "Загрузка", tone: "info" },
  processing: { label: "Обработка в TikTok", tone: "info" },
  published: { label: "Опубликовано", tone: "success" },
  failed: { label: "Ошибка", tone: "danger" },
  needs_reauth: { label: "Нужна авторизация", tone: "warning" },
  needs_review: { label: "Ручная проверка", tone: "warning" },
  cancelled: { label: "Отменено", tone: "muted" },
};

export const JOB_STATUS_ORDER: JobStatus[] = [
  "published",
  "processing",
  "uploading",
  "queued",
  "needs_review",
  "needs_reauth",
  "failed",
  "cancelled",
];

export const CAMPAIGN_STATUS: Record<CampaignStatus, { label: string; tone: Tone }> = {
  running: { label: "Выполняется", tone: "info" },
  completed: { label: "Завершена", tone: "success" },
  cancelled: { label: "Отменена", tone: "muted" },
};

export const ACCOUNT_STATUS: Record<AccountStatus, { label: string; tone: Tone }> = {
  connected: { label: "Подключён", tone: "success" },
  not_connected: { label: "Не подключён", tone: "muted" },
  needs_reauth: { label: "Нужна авторизация", tone: "warning" },
};

export const AUDIT_ACTIONS: Record<string, string> = {
  "user.login": "Вход в панель",
  "user.created": "Создан пользователь",
  "user.deleted": "Удалён пользователь",
  "group.created": "Создана группа",
  "group.deleted": "Удалена группа",
  "account.created": "Добавлен аккаунт",
  "account.updated": "Изменён аккаунт",
  "account.connected": "Подключён аккаунт TikTok",
  "account.disconnected": "Отключён аккаунт TikTok",
  "account.deleted": "Удалён аккаунт",
  "accounts.deleted": "Удалены аккаунты",
  "accounts.imported": "Импорт аккаунтов из CSV",
  "accounts.add_to_group": "Аккаунты добавлены в группу",
  "accounts.remove_from_group": "Аккаунты убраны из группы",
  "media.uploaded": "Загружено видео",
  "media.deleted": "Удалено видео",
  "campaign.confirmed": "Кампания подтверждена и запущена",
  "campaign.cancelled": "Кампания отменена",
  "job.retried": "Повтор задания",
  "job.cancelled": "Задание отменено",
  "job.resolved": "Результат задания отмечен вручную",
};

const dateTime = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatDateTime(value: string | null | undefined): string {
  return value ? dateTime.format(new Date(value)) : "—";
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "никогда";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "только что";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return formatDateTime(value);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  const units = ["КБ", "МБ", "ГБ"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export function formatDuration(ms: number | null): string {
  if (!ms) return "—";
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function captionLength(text: string): number {
  // TikTok считает длину в UTF-16 code units — так же, как String.length.
  return text.length;
}

export function tiktokPostUrl(username: string | null, postId: string | null): string | null {
  return username && postId ? `https://www.tiktok.com/@${username}/video/${postId}` : null;
}
