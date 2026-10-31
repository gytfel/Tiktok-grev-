from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql.expression import false


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Хранит время в UTC и всегда возвращает aware-datetime (в том числе из SQLite)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("naive datetime is not allowed")
            value = value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class AccountStatus:
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"


class CampaignStatus:
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class JobStatus:
    QUEUED = "queued"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    NEEDS_REAUTH = "needs_reauth"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"

    ACTIVE = (UPLOADING, PROCESSING)
    PENDING = (QUEUED, UPLOADING, PROCESSING)
    TERMINAL = (PUBLISHED, FAILED, NEEDS_REAUTH, NEEDS_REVIEW, CANCELLED)


class Role:
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    ALL = (ADMIN, OPERATOR, VIEWER)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    # Двухфакторный вход (TOTP). Секрет хранится зашифрованным.
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Последний принятый временной шаг TOTP: один и тот же код нельзя использовать дважды.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    # Подбор кода ограничивается на сервере: счётчик в cookie сессии можно было бы откатить.
    totp_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    totp_locked_until: Mapped[datetime | None]
    # Увеличивается при смене пароля или настроек 2FA — старые сессии перестают действовать.
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Часовой пояс для дат в панели (IANA, например Europe/Moscow). Пусто — пояс браузера.
    timezone: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


account_group_members = Table(
    "account_group_members",
    Base.metadata,
    Column("account_id", ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("account_groups.id", ondelete="CASCADE"), primary_key=True),
)


class AccountGroup(Base):
    __tablename__ = "account_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(128))
    open_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    connection_status: Mapped[str] = mapped_column(
        String(24), default=AccountStatus.NOT_CONNECTED, index=True
    )
    scopes: Mapped[str | None] = mapped_column(String(255))
    # Ответ creator_info: privacy_level_options, *_disabled, max_video_post_duration_sec.
    creator_info: Mapped[dict[str, Any] | None]
    creator_info_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    groups: Mapped[list[AccountGroup]] = relationship(
        secondary=account_group_members, lazy="selectin", order_by=AccountGroup.name
    )
    token: Mapped["AccountToken | None"] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )


class AccountToken(Base):
    """Токены доступа хранятся отдельно от реестра и только в зашифрованном виде."""

    __tablename__ = "account_tokens"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[datetime]
    refresh_expires_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="token")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None]
    width: Mapped[int | None]
    height: Mapped[int | None]
    fps: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(32))
    validation_status: Mapped[str] = mapped_column(String(16))
    validation_issues: Mapped[list[Any]] = mapped_column(default=list)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(ForeignKey("media_assets.id"))
    title: Mapped[str] = mapped_column(String(200))
    default_caption: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(16), default=CampaignStatus.RUNNING, index=True)
    # Хеш снимка параметров, который пользователь видел на экране подтверждения.
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    start_at: Mapped[datetime]
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime]
    # Когда отправлено уведомление о завершении; сбрасывается, если кампания снова запущена.
    notified_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    media: Mapped[MediaAsset] = relationship(lazy="joined")
    confirmer: Mapped[User | None] = relationship(lazy="joined")
    jobs: Mapped[list["PublicationJob"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", passive_deletes=True
    )


class PublicationJob(Base):
    __tablename__ = "publication_jobs"
    __table_args__ = (UniqueConstraint("campaign_id", "account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default=JobStatus.QUEUED, index=True)
    # Подпись и настройки фиксируются в момент подтверждения кампании.
    caption: Mapped[str] = mapped_column(Text, default="")
    caption_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict[str, Any]]
    scheduled_at: Mapped[datetime] = mapped_column(index=True)
    dispatched_at: Mapped[datetime | None]
    lease_until: Mapped[datetime | None]
    # Попытки в текущем цикле автоматических повторов; обнуляется при ручном повторе.
    auto_attempts: Mapped[int] = mapped_column(Integer, default=0)
    publish_id: Mapped[str | None] = mapped_column(String(255))
    upload_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    processing_since: Mapped[datetime | None]
    incomplete_since: Mapped[datetime | None]
    next_poll_at: Mapped[datetime | None] = mapped_column(index=True)
    published_at: Mapped[datetime | None]
    external_post_id: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    # True только когда известно, что публикация не состоялась и повтор не создаст дубликат.
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Когда о проблеме с заданием сообщили уведомлением; сбрасывается при повторе.
    attention_notified_at: Mapped[datetime | None]
    # Показатели видео из Display API TikTok (есть только у публичных видео с известным id).
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    stats_updated_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    campaign: Mapped[Campaign] = relationship(back_populates="jobs")
    account: Mapped[Account] = relationship(lazy="joined", innerjoin=True)
    attempts: Mapped[list["PublicationAttempt"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PublicationAttempt.attempt_number",
    )


class PublicationAttempt(Base):
    __tablename__ = "publication_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("publication_jobs.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int]
    publish_id: Mapped[str | None] = mapped_column(String(255))
    uploaded_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    result_code: Mapped[str | None] = mapped_column(String(64))
    error_details: Mapped[str | None] = mapped_column(Text)

    job: Mapped[PublicationJob] = relationship(back_populates="attempts")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_name: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class AppSecret(Base):
    """Секреты, которые приложение создаёт само (например, ключи VAPID). Хранятся зашифрованными."""

    __tablename__ = "app_secrets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PushSubscription(Base):
    """Подписка браузера или установленного PWA на Web Push."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    # Какие события присылать: {"campaign_finished": bool, "attention": bool, "reminder": bool}.
    preferences: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_success_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)


class TemplateKind:
    CAPTION = "caption"
    HASHTAGS = "hashtags"
    REPLY = "reply"
    ALL = (CAPTION, HASHTAGS, REPLY)


class TextTemplate(Base):
    """Готовые подписи, наборы хештегов и ответы на комментарии."""

    __tablename__ = "text_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class CommentDraftStatus:
    DRAFT = "draft"
    SENT = "sent"
    ARCHIVED = "archived"
    ALL = (DRAFT, SENT, ARCHIVED)


class CommentDraft(Base):
    """Черновик ответа на комментарий под своей публикацией. Отправляет его человек вручную."""

    __tablename__ = "comment_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("publication_jobs.id", ondelete="SET NULL"), index=True
    )
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    video_url: Mapped[str] = mapped_column(String(500), default="")
    original_comment: Mapped[str] = mapped_column(Text, default="")
    reply: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=CommentDraftStatus.DRAFT, index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    sent_at: Mapped[datetime | None]

    job: Mapped["PublicationJob | None"] = relationship(lazy="joined")
    account: Mapped[Account | None] = relationship(lazy="joined")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime] = mapped_column(index=True)
    # Страница панели, которую открывает напоминание, например /comments/5.
    link: Mapped[str] = mapped_column(String(500), default="")
    comment_draft_id: Mapped[int | None] = mapped_column(ForeignKey("comment_drafts.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    done_at: Mapped[datetime | None]
    notified_at: Mapped[datetime | None]


class CampaignStatsDaily(Base):
    """Суммарные показатели видео кампании на конец дня (UTC) — для графиков динамики."""

    __tablename__ = "campaign_stats_daily"
    __table_args__ = (UniqueConstraint("campaign_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    day: Mapped[date]
    views: Mapped[int] = mapped_column(BigInteger, default=0)
    likes: Mapped[int] = mapped_column(BigInteger, default=0)
    comments: Mapped[int] = mapped_column(BigInteger, default=0)
    shares: Mapped[int] = mapped_column(BigInteger, default=0)
    videos: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AccountStatsDaily(Base):
    """Подписчики, лайки и число видео аккаунта на конец дня (UTC)."""

    __tablename__ = "account_stats_daily"
    __table_args__ = (UniqueConstraint("account_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    day: Mapped[date]
    followers: Mapped[int] = mapped_column(BigInteger, default=0)
    following: Mapped[int] = mapped_column(BigInteger, default=0)
    likes: Mapped[int] = mapped_column(BigInteger, default=0)
    videos: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
