"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  ArrowDownUp, CalendarDays, Code2, Columns3, Combine, Copy, Database, FileOutput, Filter,
  FlaskConical, FolderInput, GitBranch, GitCompare, GitMerge, History, KeyRound, Layers, ListRestart, Mail, Merge, PenLine, Repeat,
  Rows3, ScanSearch, Send, ShieldCheck, Sigma, SplitSquareHorizontal, Table2, Terminal, Wand2, Workflow, type LucideIcon,
} from "lucide-react";
import React from "react";
import { cn } from "@/components/ui";

export type NodeType =
  | "dataset_input" | "incremental_input" | "file_drop_input" | "date_dimension" | "subpipeline"
  | "filter" | "select" | "sql" | "join" | "union" | "aggregate" | "dedupe" | "clean" | "fill_missing" | "derived_column"
  | "split_column" | "merge_columns" | "lookup" | "sort_limit" | "pivot" | "unpivot" | "window" | "scd2" | "surrogate_key" | "python"
  | "quality_check" | "schema_check" | "data_compare" | "anomaly_detection" | "branch"
  | "output" | "export_file" | "rest_output" | "email_report";

export type Category = "SOURCES" | "TRANSFORM" | "QUALITY" | "DESTINATIONS";

export interface PNodeData extends Record<string, unknown> {
  type: NodeType;
  label: string;
  config: Record<string, unknown>;
  runStatus?: string;
  runRows?: number | null;
  runMessage?: string | null;
  issues?: string[];
  subtitle?: string;
}

const B = {
  src: "bg-orbit-purple/20 text-orbit-purpleLight", tr: "bg-orbit-purple/20 text-orbit-purple", q: "bg-orbit-green/20 text-orbit-green",
  out: "bg-orbit-cyan/20 text-orbit-cyan",
};

export const NODE_META: Record<NodeType, { label: string; icon: LucideIcon; category: Category; badge: string; help: string }> = {
  dataset_input: { label: "Dataset input", icon: Database, category: "SOURCES", badge: B.src, help: "Reads a Bronze/Silver/Gold dataset version." },
  incremental_input: { label: "Incremental input", icon: ListRestart, category: "SOURCES", badge: B.src, help: "Reads only the rows added since the last successful run (watermark column)." },
  file_drop_input: { label: "File drop folder", icon: FolderInput, category: "SOURCES", badge: B.src, help: "Reads every new file dropped in a folder (CSV, Excel, JSON, Parquet)." },
  date_dimension: { label: "Date dimension", icon: CalendarDays, category: "SOURCES", badge: B.src, help: "Generates a calendar table: day, week, month, quarter, holidays." },
  subpipeline: { label: "Sub-pipeline", icon: Workflow, category: "SOURCES", badge: B.src, help: "Runs another published pipeline and uses one of its steps." },
  filter: { label: "Filter", icon: Filter, category: "TRANSFORM", badge: B.tr, help: "Keep rows matching a condition. {{parameters}} allowed." },
  select: { label: "Select / cast", icon: Rows3, category: "TRANSFORM", badge: B.tr, help: "Pick, rename and cast columns." },
  sql: { label: "SQL transform", icon: Code2, category: "TRANSFORM", badge: B.tr, help: "SELECT over upstream tables named input (input_1, input_2 for several)." },
  join: { label: "Join", icon: GitMerge, category: "TRANSFORM", badge: B.tr, help: "Join two inputs on key columns." },
  union: { label: "Union", icon: Combine, category: "TRANSFORM", badge: B.tr, help: "Stack inputs by column name." },
  aggregate: { label: "Aggregate", icon: Sigma, category: "TRANSFORM", badge: B.tr, help: "Group and summarise." },
  dedupe: { label: "Deduplicate", icon: Copy, category: "TRANSFORM", badge: B.tr, help: "One row per key, keeping the latest." },
  clean: { label: "Clean", icon: Wand2, category: "TRANSFORM", badge: B.tr, help: "Trim spaces, change case, blank placeholder values, normalise numbers (12,50 → 12.5) and dates." },
  fill_missing: { label: "Fill missing values", icon: PenLine, category: "TRANSFORM", badge: B.tr, help: "Fill empty cells: fixed value, mean, median, most frequent, previous or next value." },
  derived_column: { label: "Derived column", icon: FlaskConical, category: "TRANSFORM", badge: B.tr, help: "Add or replace columns computed from an expression, e.g. amount * 1.2." },
  split_column: { label: "Split column", icon: SplitSquareHorizontal, category: "TRANSFORM", badge: B.tr, help: "Split one text column into several (full_name → first, last)." },
  merge_columns: { label: "Merge columns", icon: Merge, category: "TRANSFORM", badge: B.tr, help: "Concatenate several columns into one." },
  lookup: { label: "Lookup / map values", icon: Table2, category: "TRANSFORM", badge: B.tr, help: "Translate codes into labels with a mapping, or a reference table as 2nd input." },
  sort_limit: { label: "Sort / Top N", icon: ArrowDownUp, category: "TRANSFORM", badge: B.tr, help: "Sort rows and keep the first N, optionally per group." },
  pivot: { label: "Pivot", icon: Columns3, category: "TRANSFORM", badge: B.tr, help: "Turn the values of a column into columns (one per month…)." },
  unpivot: { label: "Unpivot", icon: Rows3, category: "TRANSFORM", badge: B.tr, help: "Turn columns into (name, value) rows." },
  window: { label: "Window functions", icon: Repeat, category: "TRANSFORM", badge: B.tr, help: "Running totals, ranks, moving averages, previous / next value, % of total." },
  scd2: { label: "History (SCD2)", icon: History, category: "TRANSFORM", badge: B.tr, help: "Keeps the history of changes with valid_from / valid_to / is_current." },
  surrogate_key: { label: "Surrogate key", icon: KeyRound, category: "TRANSFORM", badge: B.tr, help: "Adds a stable numeric key computed from business columns: the same customer gets the same key at every run." },
  python: { label: "Python transform", icon: Terminal, category: "TRANSFORM", badge: B.tr, help: "def transform(df): … return df — pandas code in a restricted sandbox." },
  quality_check: { label: "Data quality", icon: ShieldCheck, category: "QUALITY", badge: B.q, help: "Assertions that fail, quarantine or warn." },
  schema_check: { label: "Schema check", icon: ScanSearch, category: "QUALITY", badge: B.q, help: "Stops or warns when a column disappears or changes type." },
  data_compare: { label: "Row count comparison", icon: GitCompare, category: "QUALITY", badge: B.q, help: "Alerts when the row count drops (or grows) too much compared with the previous run." },
  anomaly_detection: { label: "Anomaly detection", icon: ScanSearch, category: "QUALITY", badge: B.q, help: "Flags unusual values (z-score or IQR): flag, remove or fail." },
  branch: { label: "Branch (if / else)", icon: GitBranch, category: "QUALITY", badge: B.q, help: "Rows matching the condition go to the 'true' port, the others to 'false'." },
  output: { label: "Dataset output", icon: Layers, category: "DESTINATIONS", badge: B.out, help: "Writes a Silver or Gold version: overwrite, append or upsert." },
  export_file: { label: "Export file", icon: FileOutput, category: "DESTINATIONS", badge: B.out, help: "CSV / Excel / JSON / Parquet file with a download link (MinIO)." },
  rest_output: { label: "REST API output", icon: Send, category: "DESTINATIONS", badge: B.out, help: "Sends the rows to an external API or webhook (JSON)." },
  email_report: { label: "Email report", icon: Mail, category: "DESTINATIONS", badge: B.out, help: "Sends a table or a CSV attachment by e-mail." },
};

export const SOURCE_TYPES: NodeType[] = ["dataset_input", "incremental_input", "file_drop_input", "date_dimension", "subpipeline"];
// destinations whose rows leave the pipeline (no output port)
export const TERMINAL_TYPES: NodeType[] = ["export_file", "rest_output", "email_report"];

const STATUS_STYLE: Record<string, string> = {
  running: "bg-orbit-purple/20 text-orbit-purpleLight border-orbit-purple/40 animate-pulse",
  succeeded: "bg-orbit-green/15 text-orbit-green border-orbit-green/30",
  failed: "bg-orbit-red/15 text-orbit-redLight border-orbit-red/30",
  skipped: "bg-white/5 text-orbit-subtle border-orbit-border",
  pending: "bg-white/5 text-orbit-subtle border-orbit-border",
};

export function PipelineNode({ data, selected }: NodeProps<Node<PNodeData>>) {
  const meta = NODE_META[data.type] ?? NODE_META.sql;
  const Icon = meta.icon;
  const hasError = (data.issues?.length ?? 0) > 0;
  const branch = data.type === "branch";
  return (
    <div className={cn(
      "w-[190px] p-3.5 rounded-xl bg-orbit-card border shadow-lg transition relative",
      selected ? "border-orbit-purple ring-2 ring-orbit-purple/30" : hasError ? "border-orbit-red/70" : "border-orbit-border hover:border-orbit-borderStrong",
    )}>
      {!SOURCE_TYPES.includes(data.type) && <Handle type="target" position={Position.Left} />}
      {branch ? (
        <>
          <Handle type="source" id="true" position={Position.Right} style={{ top: "32%", background: "#20C997" }} />
          <Handle type="source" id="false" position={Position.Right} style={{ top: "72%", background: "#EF4444" }} />
          <span className="absolute -right-9 top-[24%] text-[9px] font-mono text-orbit-green">true</span>
          <span className="absolute -right-9 top-[64%] text-[9px] font-mono text-orbit-redLight">false</span>
        </>
      ) : !TERMINAL_TYPES.includes(data.type) && <Handle type="source" position={Position.Right} />}
      <div className="flex items-center gap-2.5">
        <div className={cn("w-7 h-7 rounded-lg flex items-center justify-center shrink-0", meta.badge)}><Icon className="w-3.5 h-3.5" /></div>
        <div className="min-w-0">
          <div className="text-xs font-semibold text-white leading-tight truncate">{data.label}</div>
          <div className="text-[11px] text-orbit-muted mt-0.5 truncate font-mono">{data.subtitle || meta.label}</div>
        </div>
      </div>
      {(data.runStatus || hasError) && (
        <div className="mt-2.5 flex items-center justify-between gap-2">
          {data.runStatus ? (
            <span className={cn("text-[10px] font-medium px-2 py-0.5 rounded-full border", STATUS_STYLE[data.runStatus] || STATUS_STYLE.pending)}>{data.runStatus}</span>
          ) : <span />}
          {data.runRows != null && <span className="text-[10px] font-mono text-orbit-muted">{data.runRows.toLocaleString()} rows</span>}
          {hasError && !data.runStatus && <span className="text-[10px] text-orbit-redLight truncate" title={data.issues!.join("\n")}>{data.issues![0]}</span>}
        </div>
      )}
    </div>
  );
}

export const nodeTypes = { pipeline: PipelineNode };

export function defaultConfig(type: NodeType): Record<string, unknown> {
  switch (type) {
    case "filter": return { condition: "" };
    case "select": return { columns: [] };
    case "sql": return { query: "SELECT *\nFROM input" };
    case "join": return { how: "inner", left_on: [""], right_on: [""] };
    case "union": return { distinct: false };
    case "aggregate": return { group_by: [], aggregations: [{ func: "count", column: "*", alias: "rows" }] };
    case "dedupe": return { keys: [], order_by: "" };
    case "quality_check": return { rules: [{ type: "not_null", column: "", on_failure: "fail" }] };
    case "output": return { layer: "silver", name: "", mode: "overwrite" };
    case "incremental_input": return { dataset_id: "", watermark_column: "" };
    case "file_drop_input": return { folder: "", pattern: "*", max_files: 50 };
    case "date_dimension": return { start: "{{start_date}}", end: "{{end_date}}", holidays: "fr", custom_holidays: [] };
    case "subpipeline": return { pipeline_id: "", node_id: "" };
    case "clean": return { columns: [], trim: true, case: "none", number_columns: [], date_columns: [] };
    case "fill_missing": return { rules: [{ column: "", strategy: "value", value: "" }], order_by: "" };
    case "derived_column": return { columns: [{ name: "", expression: "" }] };
    case "split_column": return { column: "", delimiter: " ", into: ["", ""], keep_original: true };
    case "merge_columns": return { columns: [], separator: " ", into: "", keep_originals: true };
    case "lookup": return { column: "", into: "", mapping: [{ from: "", to: "" }], keep_unmatched: true };
    case "sort_limit": return { order_by: [{ column: "", desc: true }], limit: 10, group_by: [] };
    case "pivot": return { index: [], column: "", value: "", agg: "sum" };
    case "unpivot": return { columns: [], name_column: "attribute", value_column: "value" };
    case "window": return { functions: [{ func: "running_sum", column: "", partition_by: [], order_by: "", alias: "" }] };
    case "scd2": return { keys: [], tracked: [], target_layer: "silver", target_name: "", effective: "run_time", close_missing: false };
    case "surrogate_key": return { keys: [], into: "" };
    case "python": return { code: "import pandas as pd\n\ndef transform(df):\n    # df is a pandas DataFrame; return a DataFrame\n    return df\n" };
    case "schema_check": return { expected: [], mode: "at_least", compare_previous: true, on_failure: "fail" };
    case "data_compare": return { max_drop_pct: 50, max_growth_pct: null, min_rows: null, on_failure: "fail" };
    case "anomaly_detection": return { column: "", method: "zscore", threshold: 3, action: "flag", flag_column: "is_anomaly", group_by: [] };
    case "branch": return { condition: "" };
    case "export_file": return { format: "csv", filename: "export_{{run_date}}" };
    case "rest_output": return { url: "", method: "POST", batch_size: 500, mode: "batch", headers: [] };
    case "email_report": return { to: "", subject: "Pipeline report {{run_date}}", include: "table", max_rows: 50, message: "" };
    default: return {};
  }
}
