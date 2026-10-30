"""assistant comments reminders 2fa

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 18:35:26.984796
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('text_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_text_templates_kind'), 'text_templates', ['kind'], unique=False)
    op.create_table('comment_drafts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('video_url', sa.String(length=500), nullable=False),
    sa.Column('original_comment', sa.Text(), nullable=False),
    sa.Column('reply', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_id'], ['publication_jobs.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_comment_drafts_job_id'), 'comment_drafts', ['job_id'], unique=False)
    op.create_index(op.f('ix_comment_drafts_status'), 'comment_drafts', ['status'], unique=False)
    op.create_table('reminders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('link', sa.String(length=500), nullable=False),
    sa.Column('comment_draft_id', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['comment_draft_id'], ['comment_drafts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reminders_due_at'), 'reminders', ['due_at'], unique=False)
    op.add_column('users', sa.Column('totp_secret_enc', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('users', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
    op.add_column('users', sa.Column('totp_failures', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('totp_locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('session_version', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'session_version')
    op.drop_column('users', 'totp_locked_until')
    op.drop_column('users', 'totp_failures')
    op.drop_column('users', 'totp_last_step')
    op.drop_column('users', 'totp_enabled')
    op.drop_column('users', 'totp_secret_enc')
    op.drop_index(op.f('ix_reminders_due_at'), table_name='reminders')
    op.drop_table('reminders')
    op.drop_index(op.f('ix_comment_drafts_status'), table_name='comment_drafts')
    op.drop_index(op.f('ix_comment_drafts_job_id'), table_name='comment_drafts')
    op.drop_table('comment_drafts')
    op.drop_index(op.f('ix_text_templates_kind'), table_name='text_templates')
    op.drop_table('text_templates')
