"""analytics

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-31 03:28:09.332079
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('account_stats_daily',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('followers', sa.BigInteger(), nullable=False),
    sa.Column('following', sa.BigInteger(), nullable=False),
    sa.Column('likes', sa.BigInteger(), nullable=False),
    sa.Column('videos', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'day')
    )
    op.create_index(op.f('ix_account_stats_daily_account_id'), 'account_stats_daily', ['account_id'], unique=False)
    op.create_table('campaign_stats_daily',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('views', sa.BigInteger(), nullable=False),
    sa.Column('likes', sa.BigInteger(), nullable=False),
    sa.Column('comments', sa.BigInteger(), nullable=False),
    sa.Column('shares', sa.BigInteger(), nullable=False),
    sa.Column('videos', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('campaign_id', 'day')
    )
    op.create_index(op.f('ix_campaign_stats_daily_campaign_id'), 'campaign_stats_daily', ['campaign_id'], unique=False)
    op.add_column('publication_jobs', sa.Column('views', sa.BigInteger(), nullable=True))
    op.add_column('publication_jobs', sa.Column('likes', sa.BigInteger(), nullable=True))
    op.add_column('publication_jobs', sa.Column('comments', sa.BigInteger(), nullable=True))
    op.add_column('publication_jobs', sa.Column('shares', sa.BigInteger(), nullable=True))
    op.add_column('publication_jobs', sa.Column('stats_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('publication_jobs', 'stats_updated_at')
    op.drop_column('publication_jobs', 'shares')
    op.drop_column('publication_jobs', 'comments')
    op.drop_column('publication_jobs', 'likes')
    op.drop_column('publication_jobs', 'views')
    op.drop_index(op.f('ix_campaign_stats_daily_campaign_id'), table_name='campaign_stats_daily')
    op.drop_table('campaign_stats_daily')
    op.drop_index(op.f('ix_account_stats_daily_account_id'), table_name='account_stats_daily')
    op.drop_table('account_stats_daily')
