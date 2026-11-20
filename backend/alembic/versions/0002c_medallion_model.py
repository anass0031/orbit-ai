"""Medallion model: model role, keys and relationships of Gold datasets, lineage of dataset versions.

Revision ID: 0002c_medallion_model
Revises: 0002b_pipeline_tools
Create Date: 2026-11-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002c_medallion_model'
down_revision: str | None = '0002b_pipeline_tools'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('model_role', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('primary_key', JSON, nullable=True))
        batch_op.add_column(sa.Column('relationships', JSON, nullable=True))

    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lineage', JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.drop_column('lineage')

    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.drop_column('relationships')
        batch_op.drop_column('primary_key')
        batch_op.drop_column('model_role')
