"""Pipeline tools: automation, pipeline state and token, run trigger / parameters / artifacts, notifications,
dataset tags and documentation, business glossary.

Revision ID: 0002b_pipeline_tools
Revises: 0002_pipelines_quality
Create Date: 2026-11-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002b_pipeline_tools'
down_revision: str | None = '0002_pipelines_quality'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table('notifications',
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('link', sa.String(length=300), nullable=True),
    sa.Column('pipeline_id', sa.String(length=32), nullable=True),
    sa.Column('run_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)

    op.create_table('glossary_terms',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('definition', sa.Text(), nullable=False),
    sa.Column('synonyms', JSON, nullable=False, server_default='[]'),
    sa.Column('links', JSON, nullable=False, server_default='[]'),
    sa.Column('owner_id', sa.String(length=32), nullable=True),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    with op.batch_alter_table('pipeline_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trigger', sa.String(length=24), nullable=False, server_default='manual'))
        batch_op.add_column(sa.Column('params', JSON, nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('artifacts', JSON, nullable=False, server_default='[]'))

    with op.batch_alter_table('pipelines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('automation', JSON, nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('state', JSON, nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('trigger_token_hash', sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_pipelines_next_run_at'), ['next_run_at'], unique=False)

    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tags', JSON, nullable=True))
        batch_op.add_column(sa.Column('column_docs', JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.drop_column('column_docs')
        batch_op.drop_column('tags')

    with op.batch_alter_table('pipelines', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pipelines_next_run_at'))
        batch_op.drop_column('next_run_at')
        batch_op.drop_column('trigger_token_hash')
        batch_op.drop_column('state')
        batch_op.drop_column('automation')

    with op.batch_alter_table('pipeline_runs', schema=None) as batch_op:
        batch_op.drop_column('artifacts')
        batch_op.drop_column('params')
        batch_op.drop_column('trigger')

    op.drop_table('glossary_terms')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))
        batch_op.drop_index(batch_op.f('ix_notifications_created_at'))

    op.drop_table('notifications')
