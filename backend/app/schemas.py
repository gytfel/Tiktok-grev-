from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PrivacyLevel = Literal["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- пользователи -------------------------------------------------------------------------


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(ORM):
    id: int
    username: str
    role: str
    totp_enabled: bool = False


# --- аккаунты -----------------------------------------------------------------------------


class GroupOut(ORM):
    id: int
    name: str


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class AccountOut(ORM):
    id: int
    label: str
    open_id: str | None
    display_name: str | None
    username: str | None
    avatar_url: str | None
    connection_status: str
    creator_info: dict[str, Any] | None
    creator_info_checked_at: datetime | None
    last_error: str | None
    notes: str
    groups: list[GroupOut]
    created_at: datetime


class AccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    notes: str = ""
    group_ids: list[int] = []


class AccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = None


class AccountBulkAction(BaseModel):
    account_ids: list[int] = Field(min_length=1)
    action: Literal["add_to_group", "remove_from_group", "check", "delete"]
    group_id: int | None = None


class ImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[str]


# --- медиатека ----------------------------------------------------------------------------


class MediaOut(ORM):
    id: int
    original_filename: str
    content_type: str
    sha256: str
    size_bytes: int
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    validation_status: str
    validation_issues: list[str]
    created_at: datetime
    campaign_count: int = 0


# --- кампании -----------------------------------------------------------------------------


class PostSettings(BaseModel):
    # По правилам TikTok видимость выбирает пользователь, значения по умолчанию нет.
    privacy_level: PrivacyLevel
    # Взаимодействия по умолчанию выключены, пользователь включает их сам.
    allow_comment: bool = False
    allow_duet: bool = False
    allow_stitch: bool = False
    # Раскрытие коммерческого контента: «Ваш бренд» и «Брендированный контент».
    brand_organic: bool = False
    brand_content: bool = False
    is_aigc: bool = False
    cover_timestamp_ms: int | None = Field(default=None, ge=0)


class CampaignDraft(BaseModel):
    media_asset_id: int
    title: str = Field(min_length=1, max_length=200)
    default_caption: str = ""
    caption_overrides: dict[int, str] = {}
    account_ids: list[int] = Field(min_length=1)
    settings: PostSettings
    start_at: datetime | None = None
    allow_duplicates: bool = False

    @model_validator(mode="after")
    def _start_at_aware(self):
        if self.start_at is not None and self.start_at.tzinfo is None:
            raise ValueError("start_at must include timezone")
        return self


class CampaignConfirm(CampaignDraft):
    snapshot_hash: str
    # Пользователь согласился с Music Usage Confirmation (и Branded Content Policy, если включено).
    consent: bool


class AccountCheck(BaseModel):
    account_id: int
    label: str
    display_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    ok: bool
    problems: list[str] = []
    warnings: list[str] = []
    caption: str = ""
    caption_overridden: bool = False
    creator_info_checked_at: datetime | None = None


class CampaignPreview(BaseModel):
    snapshot_hash: str
    campaign_errors: list[str]
    ready_count: int
    blocked_count: int
    accounts: list[AccountCheck]


class JobOut(ORM):
    id: int
    account_id: int
    account_label: str
    account_display_name: str | None
    account_username: str | None
    status: str
    caption: str
    caption_overridden: bool
    scheduled_at: datetime
    auto_attempts: int
    attempt_total: int
    publish_id: str | None
    published_at: datetime | None
    external_post_id: str | None
    last_error_code: str | None
    last_error_message: str | None
    retryable: bool
    updated_at: datetime


class CampaignSummary(BaseModel):
    id: int
    title: str
    status: str
    media: MediaOut
    default_caption: str
    settings: dict[str, Any]
    start_at: datetime
    confirmed_at: datetime
    confirmed_by: str | None
    counts: dict[str, int]
    total: int


class CampaignDetail(CampaignSummary):
    jobs: list[JobOut]


class AttemptOut(ORM):
    id: int
    attempt_number: int
    publish_id: str | None
    uploaded_bytes: int
    started_at: datetime
    finished_at: datetime | None
    result_code: str | None
    error_details: str | None


class ResolveIn(BaseModel):
    outcome: Literal["published", "not_published"]


class AuditOut(ORM):
    id: int
    actor_name: str
    entity_type: str
    entity_id: str | None
    action: str
    details: dict[str, Any]
    created_at: datetime
