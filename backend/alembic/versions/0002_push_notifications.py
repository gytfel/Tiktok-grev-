"""push notifications

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29 18:20:37.032762
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('app_secrets',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value_enc', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('push_subscriptions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('endpoint', sa.String(length=1024), nullable=False),
    sa.Column('p256dh', sa.String(length=255), nullable=False),
    sa.Column('auth', sa.String(length=255), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=False),
    sa.Column('preferences', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('endpoint')
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.add_column('campaigns', sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('publication_jobs', sa.Column('attention_notified_at', sa.DateTime(timezone=True), nullable=True))
    # События, случившиеся до появления уведомлений, считаются уже известными.
    op.execute("UPDATE campaigns SET notified_at = CURRENT_TIMESTAMP WHERE status <> 'running'")
    op.execute(
        "UPDATE publication_jobs SET attention_notified_at = CURRENT_TIMESTAMP "
        "WHERE status IN ('failed', 'needs_reauth', 'needs_review')"
    )


def downgrade() -> None:
    op.drop_column('publication_jobs', 'attention_notified_at')
    op.drop_column('campaigns', 'notified_at')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
    op.drop_table('app_secrets')
