export type Role = "admin" | "operator" | "viewer";

export interface User {
  id: number;
  username: string;
  role: Role;
  totp_enabled: boolean;
  timezone: string | null;
}

export interface Meta {
  tiktok_configured: boolean;
  redirect_uri: string;
  emulator: boolean;
  max_upload_bytes: number;
  require_2fa: boolean;
  ai_enabled: boolean;
}

export interface Group {
  id: number;
  name: string;
}

export type PrivacyLevel =
  | "PUBLIC_TO_EVERYONE"
  | "MUTUAL_FOLLOW_FRIENDS"
  | "FOLLOWER_OF_CREATOR"
  | "SELF_ONLY";

export interface CreatorInfo {
  privacy_level_options: PrivacyLevel[] | null;
  comment_disabled: boolean | null;
  duet_disabled: boolean | null;
  stitch_disabled: boolean | null;
  max_video_post_duration_sec: number | null;
}

export type AccountStatus = "not_connected" | "connected" | "needs_reauth";

export interface Account {
  id: number;
  label: string;
  open_id: string | null;
  display_name: string | null;
  username: string | null;
  avatar_url: string | null;
  connection_status: AccountStatus;
  creator_info: CreatorInfo | null;
  creator_info_checked_at: string | null;
  last_error: string | null;
  notes: string;
  groups: Group[];
  created_at: string;
}

export interface Media {
  id: number;
  original_filename: string;
  content_type: string;
  sha256: string;
  size_bytes: number;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  video_codec: string | null;
  validation_status: "valid" | "invalid";
  validation_issues: string[];
  created_at: string;
  campaign_count: number;
}

export interface PostSettings {
  privacy_level: PrivacyLevel;
  allow_comment: boolean;
  allow_duet: boolean;
  allow_stitch: boolean;
  brand_organic: boolean;
  brand_content: boolean;
  is_aigc: boolean;
  cover_timestamp_ms: number | null;
}

export interface CampaignDraft {
  media_asset_id: number;
  title: string;
  default_caption: string;
  caption_overrides: Record<number, string>;
  account_ids: number[];
  settings: PostSettings;
  start_at: string | null;
  allow_duplicates: boolean;
}

export interface AccountCheck {
  account_id: number;
  label: string;
  display_name: string | null;
  username: string | null;
  avatar_url: string | null;
  ok: boolean;
  problems: string[];
  warnings: string[];
  caption: string;
  caption_overridden: boolean;
  creator_info_checked_at: string | null;
}

export interface CampaignPreview {
  snapshot_hash: string;
  campaign_errors: string[];
  ready_count: number;
  blocked_count: number;
  accounts: AccountCheck[];
}

export type JobStatus =
  | "queued"
  | "uploading"
  | "processing"
  | "published"
  | "failed"
  | "needs_reauth"
  | "needs_review"
  | "cancelled";

export type CampaignStatus = "running" | "completed" | "cancelled";

export interface CampaignSummary {
  id: number;
  title: string;
  status: CampaignStatus;
  media: Media;
  default_caption: string;
  settings: PostSettings & { allow_duplicates?: boolean };
  start_at: string;
  confirmed_at: string;
  confirmed_by: string | null;
  counts: Partial<Record<JobStatus, number>>;
  total: number;
}

export interface Job {
  id: number;
  account_id: number;
  account_label: string;
  account_display_name: string | null;
  account_username: string | null;
  status: JobStatus;
  caption: string;
  caption_overridden: boolean;
  scheduled_at: string;
  auto_attempts: number;
  attempt_total: number;
  publish_id: string | null;
  published_at: string | null;
  external_post_id: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  retryable: boolean;
  updated_at: string;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
}

export interface CampaignDetail extends CampaignSummary {
  jobs: Job[];
  stats: { videos: number; views: number; likes: number; comments: number; shares: number } | null;
}

export interface Attempt {
  id: number;
  attempt_number: number;
  publish_id: string | null;
  uploaded_bytes: number;
  started_at: string;
  finished_at: string | null;
  result_code: string | null;
  error_details: string | null;
}

export interface AuditEvent {
  id: number;
  actor_name: string;
  entity_type: string;
  entity_id: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

export type TemplateKind = "caption" | "hashtags" | "reply";

export interface TextTemplate {
  id: number;
  kind: TemplateKind;
  category: string;
  title: string;
  body: string;
  updated_at: string;
}

export type AiTask = "caption" | "hashtags" | "ideas" | "reply";

export type CommentStatus = "draft" | "sent" | "archived";

export interface CommentDraft {
  id: number;
  job_id: number | null;
  campaign_id: number | null;
  campaign_title: string | null;
  account_id: number | null;
  account_label: string | null;
  account_username: string | null;
  video_url: string;
  original_comment: string;
  reply: string;
  status: CommentStatus;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
}

export interface Reminder {
  id: number;
  title: string;
  note: string;
  due_at: string;
  link: string;
  comment_draft_id: number | null;
  created_at: string;
  done_at: string | null;
  notified_at: string | null;
}
