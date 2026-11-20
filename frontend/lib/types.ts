// Shared API types (mirror the backend Pydantic schemas).
export type Role = "admin" | "data_engineer" | "analyst";
export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type Maturity = "implemented" | "simulated" | "planned";
export type ImportStatus = "discovered" | "ingested" | "failed";

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at?: string | null;
  created_at: string;
}
export interface Me {
  user: User;
  role_label: string;
  permissions: string[];
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ColumnSchema {
  name: string;
  source_name?: string;
  type: string;
  nullable?: boolean;
  null_count?: number;
  null_pct?: number;
  distinct_count?: number;
  min?: unknown;
  max?: unknown;
  examples?: string[];
  metadata?: boolean;
  description?: string;
}

export interface ImportSummary {
  id: string;
  filename: string;
  file_format: "csv" | "excel" | "json";
  size_bytes: number;
  status: ImportStatus;
  row_count?: number | null;
  quarantined_count: number;
  error_message?: string | null;
  dataset_id?: string | null;
  uploaded_by_id?: string | null;
  created_at: string;
  ingested_at?: string | null;
}
export interface FileImport extends ImportSummary {
  checksum_sha256: string;
  raw_location: string;
  options: { delimiter?: string; has_header?: boolean; encoding?: string; sheet_name?: string };
  schema: ColumnSchema[];
  sample_rows: Record<string, unknown>[];
  warnings: string[];
  inferred_primary_key: string[];
  sheets: string[];
  quarantine_location?: string | null;
  dataset_version_id?: string | null;
  suggested_dataset_name?: string | null;
}

export interface DatasetVersion {
  id: string;
  version: number;
  location: string;
  row_count: number;
  size_bytes: number;
  schema_changes: { added?: string[]; removed?: string[]; changed?: { column: string; from: string; to: string }[] };
  source_import_id?: string | null;
  pipeline_run_id?: string | null;
  created_by_id?: string | null;
  created_at: string;
}
export interface Dataset {
  id: string;
  layer: "bronze" | "silver" | "gold";
  name: string;
  qualified_name: string;
  description?: string | null;
  format: string;
  schema: ColumnSchema[];
  current_version: number;
  row_count?: number | null;
  size_bytes?: number | null;
  created_at: string;
  last_updated_at?: string | null;
  quality_score?: number | null;
  tags?: string[] | null;
  column_docs?: Record<string, string> | null;
  model_role?: "fact" | "dimension" | "mart" | null;
  primary_key?: string[] | null;
  relationships?: { column: string; dimension: string }[] | null;
}
export interface DatasetDetail extends Dataset {
  versions: DatasetVersion[];
}
export interface Preview {
  version: number;
  columns: string[];
  rows: Record<string, unknown>[];
  total_rows: number;
  location: string;
}

export interface AuditEvent {
  id: string;
  actor_email?: string | null;
  action: string;
  resource_type: string;
  resource_id?: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ComponentHealth {
  status: "up" | "down";
  detail: string;
  latency_ms: number | null;
}
export interface Health {
  status: "ok" | "degraded";
  storage_backend: string;
  components: Record<string, ComponentHealth>;
}
// ── Week 4: pipelines, runs, quality, dashboard ─────────────────────────────
export interface DatasetRef { dataset_id: string; qualified_name: string; version: number; rows?: number | null }

export interface Pipeline {
  id: string;
  name: string;
  description?: string | null;
  status: "draft" | "published";
  current_version: number;
  last_run_at?: string | null;
  last_run_status?: string | null;
  next_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunItem {
  id: string;
  pipeline_id: string;
  pipeline_name?: string | null;
  pipeline_version: number;
  status: RunStatus;
  queued_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  records_in?: number | null;
  records_out?: number | null;
  error_class?: string | null;
  error_message?: string | null;
  attempt: number;
  retry_of_id?: string | null;
  outputs: DatasetRef[];
  trigger?: string;
  params?: Record<string, unknown>;
}
export interface NodeRunStatus {
  status: string;
  rows?: number | null;
  message?: string | null;
  started_at?: string;
  finished_at?: string;
  quality?: { name: string; passed: boolean; failed: number; total: number; on_failure: string; error?: string | null }[];
}
export interface LogLine { ts: string; level: string; msg: string }
export interface RunDetail extends RunItem {
  correlation_id?: string | null;
  cancel_requested: boolean;
  inputs: DatasetRef[];
  graph_snapshot: { nodes: { id: string; type: string; label: string }[]; edges: { source: string; target: string }[] };
  node_status: Record<string, NodeRunStatus>;
  logs: LogLine[];
  artifacts?: RunArtifact[];
}

export type RuleType = "not_null" | "unique" | "range" | "regex" | "allowed_values" | "freshness" | "row_count" | "custom_sql";
export interface QualityResult {
  id: string;
  rule_id: string;
  dataset_id: string;
  dataset_version?: number | null;
  pipeline_run_id?: string | null;
  trigger: string;
  passed: boolean;
  total_count: number;
  failed_count: number;
  score: number;
  evidence: Record<string, unknown>[];
  check_sql?: string | null;
  error_message?: string | null;
  executed_at: string;
}
export interface QualityRule {
  id: string;
  dataset_id: string;
  dataset_name?: string | null;
  column?: string | null;
  name: string;
  rule_type: RuleType;
  params: Record<string, unknown>;
  dimension: string;
  severity: "low" | "medium" | "high";
  on_failure: "warn" | "fail" | "quarantine";
  threshold: number;
  enabled: boolean;
  created_at: string;
  last_result?: QualityResult | null;
}
export interface QualityIssue {
  id: string;
  rule_id: string;
  rule_name?: string | null;
  dataset_id: string;
  dataset_name?: string | null;
  column?: string | null;
  title: string;
  dimension: string;
  severity: "low" | "medium" | "high";
  status: "open" | "acknowledged" | "resolved";
  failed_count: number;
  evidence: Record<string, unknown>[];
  occurrences: number;
  detected_at: string;
  last_seen_at: string;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
  resolution_note?: string | null;
}
export interface QualityOverview {
  score: number | null;
  dimensions: Record<string, number>;
  open_issues: number;
  datasets: {
    id: string; qualified_name: string; layer: string; quality_score: number | null;
    rules: number; open_issues: number; dimensions: Record<string, number>;
  }[];
}

export interface Attention {
  recurring_failures: { pipeline_id: string; name: string; streak: number; last_run_id: string; last_error: string }[];
  stuck_runs: (RunItem & { reason: string })[];
  low_quality_datasets: { id: string; qualified_name: string; quality_score: number }[];
  low_score_threshold: number;
}
export interface DayRuns { date: string; succeeded: number; failed: number; cancelled: number; records: number }
export interface RunStats {
  days: number;
  counts: Record<RunStatus, number>;
  total: number;
  success_rate: number | null;
  avg_duration_ms: number | null;
  p95_duration_ms: number | null;
  records_written: number;
  running_now: number;
  queued_now: number;
}
export interface Dashboard {
  layers: Record<"bronze" | "silver" | "gold", { datasets: number; rows: number; bytes: number }>;
  imports: { total: number; by_status: Partial<Record<ImportStatus, number>> };
  pipelines: { total: number; published: number; failing: number };
  runs: RunStats;
  daily: DayRuns[];
  quality: {
    score: number | null; datasets_scored: number; dimensions: Record<string, number>;
    open_issues: number; open_by_severity: Record<string, number>;
  };
  recent_runs: RunItem[];
  recent_issues: { id: string; title: string; severity: string; status: string; dataset_id: string; detected_at: string }[];
  attention: Attention;
  automation?: { automated: number; scheduled: number; next: { id: string; name: string; triggers: string[]; schedule: string; next_run_at: string | null; last_run_status: string | null }[] };
}

// ── Pipeline automation and catalogue tools ──
export interface ParamDef { name: string; type: "string" | "integer" | "number" | "date" | "boolean"; default: unknown; description?: string }
export interface AutomationSettings {
  schedule: { enabled: boolean; kind: "interval" | "daily" | "weekly" | "monthly" | "cron"; every_minutes: number; time: string; weekdays: number[]; day_of_month: number; cron: string };
  on_new_data: { enabled: boolean; dataset_ids: string[]; file_drop: boolean };
  after_pipelines: { enabled: boolean; pipeline_ids: string[]; on: "success" | "any" };
  api_trigger: { enabled: boolean };
  conditions: { skip_if_no_new_data: boolean; min_input_quality: number | null; if_running: "skip" | "queue" };
  retry: { max_retries: number; backoff_seconds: number; backoff_factor: number };
  timeout_minutes: number | null;
  notifications: { on: string[]; in_app: boolean; roles: string[]; emails: string[]; webhook_url: string };
  sla: { enabled: boolean; dataset_id: string | null; deadline: string; max_age_hours: number | null };
  params: ParamDef[];
}
export interface AutomationEvent { at: string; kind: string; message: string; run_id: string | null }
export interface AutomationInfo {
  settings: AutomationSettings; next_run_at: string | null; schedule_text: string; has_token: boolean; trigger_url: string;
  pending: { trigger: string; at: string } | null; retry: { run_id: string; due_at: string; attempt: number } | null;
  backfill: { queue: unknown[]; total: number; done: number; failed: number; current?: string } | null;
  events: AutomationEvent[]; dependents: { id: string; name: string }[];
}
export interface RunArtifact {
  node_id: string; type: string; label: string; status: "succeeded" | "failed" | "skipped"; message: string;
  details?: Record<string, unknown>;
}
export interface NotificationItem {
  id: string; level: "info" | "success" | "warning" | "error"; title: string; message: string | null; link: string | null;
  pipeline_id: string | null; run_id: string | null; created_at: string; read: boolean;
}
export interface CalendarData {
  start: string; end: string;
  runs: { id: string; pipeline_id: string; pipeline: string | null; status: string; trigger: string; queued_at: string; duration_ms: number | null; params: Record<string, unknown> }[];
  upcoming: { pipeline_id: string; pipeline: string; at: string; schedule: string }[];
}

// ── Analyst workspace ───────────────────────────────────────────────────────
export interface GlossaryTerm { id: string; name: string; definition: string; synonyms: string[]; links: { dataset: string; column?: string }[]; owner_id: string | null; updated_at: string }
export interface ColumnProfile {
  name: string; type: string; nulls: number; null_pct: number; distinct: number; unique: boolean; description?: string | null;
  min?: unknown; max?: unknown; mean?: number | null; stddev?: number | null; median?: number | null;
  histogram?: { from: number; to: number; count: number }[]; by_month?: { month: string; count: number }[];
  top_values?: { value: string; count: number; pct: number }[]; min_length?: number; max_length?: number; avg_length?: number;
  role?: "foreign_key" | "primary_key" | "identifier"; display_name?: string; dimension?: string; label_column?: string;
}
export interface DatasetProfile { dataset: string; version: number; rows: number; columns: ColumnProfile[] }
export interface DatasetDiff {
  dataset: string; from: number; to: number; rows_from: number; rows_to: number; mode?: "keys" | "rows"; keys?: string[];
  schema: { added?: string[]; removed?: string[]; changed?: { column: string; from: string; to: string }[] };
  counts?: { added: number; removed: number; changed?: number; unchanged: number }; changed_columns?: Record<string, number>;
  samples?: { added?: Record<string, unknown>[]; removed?: Record<string, unknown>[]; changed?: Record<string, unknown>[] }; note?: string;
}
export interface SearchResult {
  datasets: { id: string; qualified_name: string; layer: string; description: string | null; tags: string[]; rows: number | null; matched: string[]; columns: string[] }[];
  terms: GlossaryTerm[];
}

// ── Medallion: lineage and star schema ──
export interface LineageRef { dataset_id: string; qualified_name: string; version: number }
export interface DatasetLineage {
  dataset: string; version: number | null; sources: LineageRef[]; produced: (LineageRef & { from_version: number })[];
  pipeline_run_id: string | null; source_import_id: string | null; ingestion_run_id?: string | null;
}
export interface DatasetModel {
  dataset: string; role: "fact" | "dimension" | "mart" | null; primary_key: string[];
  relationships: { column: string; dimension: string; dimension_id: string | null; key: string | null }[];
  used_by: { fact: string; fact_id: string; column: string }[];
}


// ── Gold models and business views ──
export interface GoldChart { id: string; type: "bar" | "line"; title: string; subtitle?: string | null; x: string; y: string; groups: number; points: { label: string; value: number }[] }
export interface GoldCharts { dataset: string; version: number; measures: string[]; measure: string | null; aggregation: string; kpis: { label: string; value: number }[]; charts: GoldChart[] }
export interface GoldLink { dimension: string; column: string; key: string; join: string; orphan_rows: number; calendar?: boolean }
export interface GoldFact { name: string; source: string; source_table: string; dataset_id: string; rows: number; type: "fact" | "bridge"; grain: string; primary_key: string[]; degenerate: string[]; measures: { column: string; aggregation: string }[]; links: GoldLink[] }
export interface GoldDimension { name: string; kind: "table" | "embedded" | "calendar"; natural_key: string; key: string; label: string; attributes: string[]; parents: { dimension: string; column: string; key: string; join: string }[]; used_by: string[] }
export interface GoldModel { id: string; name: string; schema_type: "star" | "snowflake"; constellation: boolean; facts: GoldFact[]; dimensions: GoldDimension[]; shared_dimensions: string[]; warnings: string[]; summary: string }
export interface GoldSilverTable { id: string; name: string; qualified_name: string; rows: number; role: "fact" | "dimension" | "bridge" | "standalone"; reason: string; primary_key: string[]; grain: string; measures: string[]; dates: string[]; attributes: string[] }
export interface GoldRelationship { from_table: string; from_column: string | null; to_table: string; to_column: string | null; cardinality: string; coverage: number; orphan_rows: number; confidence: string; join: string }
export interface GoldAnalysis {
  silver_tables: GoldSilverTable[]; relationships: GoldRelationship[]; functional_dependencies: { table: string; determinant: string; dependent: string }[];
  conflicts: { tables: string[]; key: string; column: string; conflicting_rows: number }[]; same_entities: { tables: string[]; labels: string[]; shared_members: number; message: string }[];
  standalone: { table: string; reason: string }[]; models: GoldModel[];
}
export interface GoldPreview { valid: boolean; issues: { message: string; severity: string }[]; tables: { name: string; role: string | null; primary_key: string[]; columns: { name: string; type: string }[]; sample: Record<string, unknown>[]; error: string | null }[] }
export interface GoldGenerated { pipeline_id: string; pipeline_name: string; tables: string[]; run: { id: string; status: string; error: string | null } | null }
export interface BatchResult { results: { filename: string; entity: string | null; dataset: string; status: "ingested" | "skipped" | "failed"; rows?: number; reason?: string | null }[]; summary: { ingested: number; skipped: number; failed: number } }
