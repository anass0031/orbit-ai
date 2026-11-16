"""Initial schema: users, audit log, file imports and the lakehouse catalogue (Bronze).

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", TS, nullable=True),
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "audit_events",
        sa.Column("actor_id", sa.String(32), nullable=True),
        sa.Column("actor_email", sa.String(255), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(48), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("details", JSON, nullable=False),
        sa.Column("correlation_id", sa.String(32), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("id", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    op.create_table(
        "datasets",
        sa.Column("layer", sa.String(8), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("schema", JSON, nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.String(32), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("owner_id", sa.String(32), nullable=True),
        sa.Column("last_updated_at", TS, nullable=True),
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("layer", "name"),
    )
    op.create_index("ix_datasets_layer", "datasets", ["layer"])

    op.create_table(
        "dataset_versions",
        sa.Column("dataset_id", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("schema", JSON, nullable=False),
        sa.Column("schema_changes", JSON, nullable=False),
        sa.Column("source_import_id", sa.String(32), nullable=True),
        sa.Column("created_by_id", sa.String(32), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("id", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "version"),
    )
    op.create_index("ix_dataset_versions_dataset_id", "dataset_versions", ["dataset_id"])

    op.create_table(
        "file_imports",
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("raw_location", sa.Text(), nullable=False),
        sa.Column("options", JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("schema", JSON, nullable=False),
        sa.Column("sample_rows", JSON, nullable=False),
        sa.Column("warnings", JSON, nullable=False),
        sa.Column("inferred_primary_key", JSON, nullable=False),
        sa.Column("quarantined_count", sa.Integer(), nullable=False),
        sa.Column("quarantine_location", sa.Text(), nullable=True),
        sa.Column("sheets", JSON, nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("dataset_id", sa.String(32), nullable=True),
        sa.Column("dataset_version_id", sa.String(32), nullable=True),
        sa.Column("uploaded_by_id", sa.String(32), nullable=True),
        sa.Column("ingested_at", TS, nullable=True),
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["dataset_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_file_imports_status", "file_imports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_file_imports_status", table_name="file_imports")
    op.drop_table("file_imports")
    op.drop_index("ix_dataset_versions_dataset_id", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.drop_index("ix_datasets_layer", table_name="datasets")
    op.drop_table("datasets")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
