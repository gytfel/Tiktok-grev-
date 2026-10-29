"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-29 17:33:44.789452
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('account_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('open_id', sa.String(length=128), nullable=True),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('avatar_url', sa.Text(), nullable=True),
    sa.Column('connection_status', sa.String(length=24), nullable=False),
    sa.Column('scopes', sa.String(length=255), nullable=True),
    sa.Column('creator_info', sa.JSON(), nullable=True),
    sa.Column('creator_info_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('open_id')
    )
    op.create_index(op.f('ix_accounts_connection_status'), 'accounts', ['connection_status'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('username')
    )
    op.create_table('account_group_members',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['account_groups.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('account_id', 'group_id')
    )
    op.create_table('account_tokens',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('access_token_enc', sa.Text(), nullable=False),
    sa.Column('refresh_token_enc', sa.Text(), nullable=False),
    sa.Column('access_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('refresh_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('account_id')
    )
    op.create_table('audit_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('actor_name', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=32), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_created_at'), 'audit_events', ['created_at'], unique=False)
    op.create_table('media_assets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('storage_key', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=64), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('fps', sa.Float(), nullable=True),
    sa.Column('video_codec', sa.String(length=32), nullable=True),
    sa.Column('validation_status', sa.String(length=16), nullable=False),
    sa.Column('validation_issues', sa.JSON(), nullable=False),
    sa.Column('uploaded_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('storage_key')
    )
    op.create_index(op.f('ix_media_assets_sha256'), 'media_assets', ['sha256'], unique=False)
    op.create_table('campaigns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('media_asset_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('default_caption', sa.Text(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('snapshot_hash', sa.String(length=64), nullable=False),
    sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('confirmed_by', sa.Integer(), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['media_asset_id'], ['media_assets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_campaigns_status'), 'campaigns', ['status'], unique=False)
    op.create_table('publication_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('caption', sa.Text(), nullable=False),
    sa.Column('caption_overridden', sa.Boolean(), nullable=False),
    sa.Column('settings', sa.JSON(), nullable=False),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('auto_attempts', sa.Integer(), nullable=False),
    sa.Column('publish_id', sa.String(length=255), nullable=True),
    sa.Column('upload_complete', sa.Boolean(), nullable=False),
    sa.Column('processing_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('incomplete_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_poll_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('external_post_id', sa.String(length=64), nullable=True),
    sa.Column('last_error_code', sa.String(length=64), nullable=True),
    sa.Column('last_error_message', sa.Text(), nullable=True),
    sa.Column('retryable', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('campaign_id', 'account_id')
    )
    op.create_index(op.f('ix_publication_jobs_account_id'), 'publication_jobs', ['account_id'], unique=False)
    op.create_index(op.f('ix_publication_jobs_campaign_id'), 'publication_jobs', ['campaign_id'], unique=False)
    op.create_index(op.f('ix_publication_jobs_next_poll_at'), 'publication_jobs', ['next_poll_at'], unique=False)
    op.create_index(op.f('ix_publication_jobs_scheduled_at'), 'publication_jobs', ['scheduled_at'], unique=False)
    op.create_index(op.f('ix_publication_jobs_status'), 'publication_jobs', ['status'], unique=False)
    op.create_table('publication_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('attempt_number', sa.Integer(), nullable=False),
    sa.Column('publish_id', sa.String(length=255), nullable=True),
    sa.Column('uploaded_bytes', sa.BigInteger(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_code', sa.String(length=64), nullable=True),
    sa.Column('error_details', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['publication_jobs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'attempt_number')
    )
    op.create_index(op.f('ix_publication_attempts_job_id'), 'publication_attempts', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_publication_attempts_job_id'), table_name='publication_attempts')
    op.drop_table('publication_attempts')
    op.drop_index(op.f('ix_publication_jobs_status'), table_name='publication_jobs')
    op.drop_index(op.f('ix_publication_jobs_scheduled_at'), table_name='publication_jobs')
    op.drop_index(op.f('ix_publication_jobs_next_poll_at'), table_name='publication_jobs')
    op.drop_index(op.f('ix_publication_jobs_campaign_id'), table_name='publication_jobs')
    op.drop_index(op.f('ix_publication_jobs_account_id'), table_name='publication_jobs')
    op.drop_table('publication_jobs')
    op.drop_index(op.f('ix_campaigns_status'), table_name='campaigns')
    op.drop_table('campaigns')
    op.drop_index(op.f('ix_media_assets_sha256'), table_name='media_assets')
    op.drop_table('media_assets')
    op.drop_index(op.f('ix_audit_events_created_at'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_table('account_tokens')
    op.drop_table('account_group_members')
    op.drop_table('users')
    op.drop_index(op.f('ix_accounts_connection_status'), table_name='accounts')
    op.drop_table('accounts')
    op.drop_table('account_groups')
