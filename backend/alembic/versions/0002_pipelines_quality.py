"""Week 4: ETL pipelines (versions, runs) and data quality (rules, results, issues).

Revision ID: 0002_pipelines_quality
Revises: 0001_initial
Create Date: 2026-11-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002_pipelines_quality'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table('pipelines',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('graph', JSON, nullable=False),
    sa.Column('current_version', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=True),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_run_status', sa.String(length=16), nullable=True),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('pipeline_runs',
    sa.Column('pipeline_id', sa.String(length=32), nullable=False),
    sa.Column('pipeline_version', sa.Integer(), nullable=False),
    sa.Column('graph_snapshot', JSON, nullable=False),
    sa.Column('node_status', JSON, nullable=False),
    sa.Column('inputs', JSON, nullable=False),
    sa.Column('outputs', JSON, nullable=False),
    sa.Column('engine', sa.String(length=16), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('records_in', sa.Integer(), nullable=True),
    sa.Column('records_out', sa.Integer(), nullable=True),
    sa.Column('error_class', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('retryable', sa.Boolean(), nullable=False),
    sa.Column('logs', JSON, nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('retry_of_id', sa.String(length=32), nullable=True),
    sa.Column('correlation_id', sa.String(length=32), nullable=True),
    sa.Column('triggered_by_id', sa.String(length=32), nullable=True),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('pipeline_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pipeline_runs_pipeline_id'), ['pipeline_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pipeline_runs_queued_at'), ['queued_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_pipeline_runs_status'), ['status'], unique=False)

    op.create_table('pipeline_versions',
    sa.Column('pipeline_id', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('graph', JSON, nullable=False),
    sa.Column('created_by_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('pipeline_id', 'version')
    )
    with op.batch_alter_table('pipeline_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pipeline_versions_pipeline_id'), ['pipeline_id'], unique=False)

    op.create_table('quality_rules',
    sa.Column('dataset_id', sa.String(length=32), nullable=False),
    sa.Column('column', sa.String(length=120), nullable=True),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('rule_type', sa.String(length=24), nullable=False),
    sa.Column('params', JSON, nullable=False),
    sa.Column('dimension', sa.String(length=16), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('on_failure', sa.String(length=12), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.String(length=32), nullable=True),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quality_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quality_rules_dataset_id'), ['dataset_id'], unique=False)

    op.create_table('quality_results',
    sa.Column('rule_id', sa.String(length=32), nullable=False),
    sa.Column('dataset_id', sa.String(length=32), nullable=False),
    sa.Column('dataset_version', sa.Integer(), nullable=True),
    sa.Column('pipeline_run_id', sa.String(length=32), nullable=True),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('total_count', sa.Integer(), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('check_sql', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pipeline_run_id'], ['pipeline_runs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rule_id'], ['quality_rules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quality_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quality_results_dataset_id'), ['dataset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quality_results_executed_at'), ['executed_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_quality_results_rule_id'), ['rule_id'], unique=False)

    op.create_table('quality_issues',
    sa.Column('rule_id', sa.String(length=32), nullable=False),
    sa.Column('result_id', sa.String(length=32), nullable=False),
    sa.Column('dataset_id', sa.String(length=32), nullable=False),
    sa.Column('column', sa.String(length=120), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('dimension', sa.String(length=16), nullable=False),
    sa.Column('severity', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('failed_count', sa.Integer(), nullable=False),
    sa.Column('evidence', JSON, nullable=False),
    sa.Column('occurrences', sa.Integer(), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('acknowledged_by_id', sa.String(length=32), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by_id', sa.String(length=32), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['acknowledged_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['result_id'], ['quality_results.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['rule_id'], ['quality_rules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quality_issues', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quality_issues_dataset_id'), ['dataset_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quality_issues_rule_id'), ['rule_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quality_issues_status'), ['status'], unique=False)

    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pipeline_run_id', sa.String(length=32), nullable=True))

    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quality_score', sa.Float(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('datasets', schema=None) as batch_op:
        batch_op.drop_column('quality_score')

    with op.batch_alter_table('dataset_versions', schema=None) as batch_op:
        batch_op.drop_column('pipeline_run_id')

    with op.batch_alter_table('quality_issues', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quality_issues_status'))
        batch_op.drop_index(batch_op.f('ix_quality_issues_rule_id'))
        batch_op.drop_index(batch_op.f('ix_quality_issues_dataset_id'))

    op.drop_table('quality_issues')
    with op.batch_alter_table('quality_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quality_results_rule_id'))
        batch_op.drop_index(batch_op.f('ix_quality_results_executed_at'))
        batch_op.drop_index(batch_op.f('ix_quality_results_dataset_id'))

    op.drop_table('quality_results')
    with op.batch_alter_table('quality_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quality_rules_dataset_id'))

    op.drop_table('quality_rules')
    with op.batch_alter_table('pipeline_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pipeline_versions_pipeline_id'))

    op.drop_table('pipeline_versions')
    with op.batch_alter_table('pipeline_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pipeline_runs_status'))
        batch_op.drop_index(batch_op.f('ix_pipeline_runs_queued_at'))
        batch_op.drop_index(batch_op.f('ix_pipeline_runs_pipeline_id'))

    op.drop_table('pipeline_runs')
    op.drop_table('pipelines')
