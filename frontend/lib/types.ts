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
export interface Overview {
  imports_total: number;
  imports_by_status: Partial<Record<ImportStatus, number>>;
  bronze_datasets: number;
  bronze_rows: number;
  bronze_bytes: number;
  users_active: number;
}
