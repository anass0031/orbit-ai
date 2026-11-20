"use client";
// Configuration panels of the pipeline tools (sources, transformations, quality, destinations).
import { Upload } from "lucide-react";
import React, { useRef, useState } from "react";
import useSWR from "swr";
import { Field } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { ColumnSchema, Dataset } from "@/lib/types";
import { Check, ColumnSelect, ListEditor, MultiColumns, arr, small } from "./fields";
import type { PNodeData } from "./nodes";

type Cfg = Record<string, unknown>;
interface Props {
  data: PNodeData;
  upstream: { id: string; label: string; schema: ColumnSchema[] }[];
  datasets: Dataset[];
  readOnly: boolean;
  errors: Record<string, string>;
  onChange: (config: Cfg) => void;
}
interface PipelineItem { id: string; name: string; current_version: number }
interface PipelineGraph { id: string; graph: { nodes: { id: string; type: string; label: string }[] } }

const S = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const num = (v: string) => (v === "" ? null : Number(v));
const PARAM_HINT = "Parameters: {{run_date}}, {{start_date}}, {{end_date}} and those declared in Automation.";

function DatasetSelect({ value, datasets, onChange, disabled }: { value: string; datasets: Dataset[]; onChange: (v: string) => void; disabled: boolean }) {
  return (
    <select className="orbit-input" disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select dataset…</option>
      {(["bronze", "silver", "gold"] as const).map((l) => (
        <optgroup key={l} label={l.toUpperCase()}>
          {datasets.filter((x) => x.layer === l).map((x) => <option key={x.id} value={x.id}>{x.qualified_name} (v{x.current_version})</option>)}
        </optgroup>
      ))}
    </select>
  );
}

function OnError({ c, set, d, def = "fail" }: { c: Cfg; set: (p: Cfg) => void; d: boolean; def?: string }) {
  return (
    <Field label="If this step fails" hint="Datasets written before it are kept in both cases.">
      <select className="orbit-input" disabled={d} value={S(c.on_error) || def} onChange={(e) => set({ on_error: e.target.value })}>
        <option value="fail">Fail the run</option><option value="warn">Warn and continue</option>
      </select>
    </Field>
  );
}

function OnFailure({ c, set, d }: { c: Cfg; set: (p: Cfg) => void; d: boolean }) {
  return (
    <Field label="When the check fails">
      <select className="orbit-input" disabled={d} value={S(c.on_failure) || "fail"} onChange={(e) => set({ on_failure: e.target.value })}>
        <option value="fail">Stop the run (nothing is written)</option><option value="warn">Warn only</option>
      </select>
    </Field>
  );
}

function DropFolder({ folder, disabled }: { folder: string; disabled: boolean }) {
  const notify = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const valid = /^[a-z0-9][a-z0-9_-]{0,60}$/.test(folder);
  const { data: files, mutate } = useSWR<{ name: string; size: number; modified: string }[]>(valid ? `/pipelines/dropzone/${folder}` : null);
  const upload = async (f: File) => {
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      await api(`/pipelines/dropzone/${folder}`, { method: "POST", body: fd });
      notify(`${f.name} dropped in ${folder}`);
      mutate();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  if (!valid) return <div className="text-[11px] text-orbit-subtle">Folder: lowercase letters, digits, - or _ (MinIO prefix dropzone/&lt;folder&gt;/).</div>;
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] text-orbit-muted">{files ? `${files.length} file(s) in dropzone/${folder}/` : "…"}</div>
      <div className="max-h-28 overflow-y-auto space-y-0.5">
        {files?.map((f) => <div key={f.name} className="flex justify-between text-[11px] font-mono"><span className="text-white truncate">{f.name}</span><span className="text-orbit-subtle">{bytes(f.size)}</span></div>)}
      </div>
      {!disabled && (
        <>
          <input ref={input} type="file" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <button disabled={busy} onClick={() => input.current?.click()} className="text-[11px] text-orbit-purple flex items-center gap-1"><Upload className="w-3 h-3" />{busy ? "Uploading…" : "Drop a file now"}</button>
        </>
      )}
      <p className="text-[10px] text-orbit-subtle">Files can also be put in the MinIO bucket under dropzone/{folder}/. Each file is read once.</p>
    </div>
  );
}

export function ExtraConfig({ data, upstream, datasets, readOnly, errors, onChange }: Props) {
  const c = data.config as Cfg;
  const set = (patch: Cfg) => onChange({ ...c, ...patch });
  const cols = upstream[0]?.schema || [];
  const d = readOnly;
  const t = data.type;
  const { data: pipelines } = useSWR<PipelineItem[]>(t === "subpipeline" ? "/pipelines" : null);
  const { data: sub } = useSWR<PipelineGraph>(t === "subpipeline" && c.pipeline_id ? `/pipelines/${c.pipeline_id}` : null);

  switch (t) {
    // ── sources ──
    case "incremental_input": {
      const ds = datasets.find((x) => x.id === c.dataset_id);
      return (
        <>
          <Field label="Dataset" error={errors.dataset_id}><DatasetSelect disabled={d} value={S(c.dataset_id)} datasets={datasets} onChange={(v) => set({ dataset_id: v, watermark_column: "" })} /></Field>
          <Field label="Watermark column" error={errors.watermark_column} hint="A date, timestamp or increasing number. Only rows above the last value read are processed.">
            <ColumnSelect disabled={d} value={S(c.watermark_column)} columns={(ds?.schema || []) as ColumnSchema[]} onChange={(v) => set({ watermark_column: v })} />
          </Field>
          <Field label="Start after (first run, optional)" error={errors.initial_value}><input className="orbit-input font-mono" disabled={d} placeholder="e.g. 2026-01-01" value={S(c.initial_value)} onChange={(e) => set({ initial_value: e.target.value })} /></Field>
        </>
      );
    }
    case "file_drop_input":
      return (
        <>
          <Field label="Folder" error={errors.folder}><input className="orbit-input font-mono" disabled={d} placeholder="e.g. supplier-invoices" value={S(c.folder)} onChange={(e) => set({ folder: e.target.value.toLowerCase() })} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="File pattern"><input className="orbit-input font-mono" disabled={d} value={S(c.pattern) || "*"} onChange={(e) => set({ pattern: e.target.value })} /></Field>
            <Field label="Max files / run"><input type="number" className="orbit-input" disabled={d} value={S(c.max_files) || "50"} onChange={(e) => set({ max_files: num(e.target.value) })} /></Field>
          </div>
          <DropFolder folder={S(c.folder)} disabled={d} />
        </>
      );
    case "date_dimension":
      return (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start" error={errors.start}><input className="orbit-input font-mono" disabled={d} value={S(c.start)} onChange={(e) => set({ start: e.target.value })} /></Field>
            <Field label="End" error={errors.end}><input className="orbit-input font-mono" disabled={d} value={S(c.end)} onChange={(e) => set({ end: e.target.value })} /></Field>
          </div>
          <p className="text-[10px] text-orbit-subtle">YYYY-MM-DD or a parameter such as {"{{start_date}}"}.</p>
          <Field label="Public holidays (fixed dates)">
            <select className="orbit-input" disabled={d} value={S(c.holidays)} onChange={(e) => set({ holidays: e.target.value })}>
              <option value="">None</option><option value="fr">France</option><option value="ma">Morocco</option><option value="us">United States</option>
            </select>
          </Field>
          <Field label="Other holidays (Easter, Eid…)" error={errors.custom_holidays}>
            <ListEditor disabled={d} items={(c.custom_holidays as { date: string; name: string }[]) || []} empty={{ date: "", name: "" }} addLabel="Add a holiday"
              onChange={(v) => set({ custom_holidays: v })} render={(h, up) => (
                <div className="grid grid-cols-2 gap-1.5"><input className={small} placeholder="2026-03-20" disabled={d} value={h.date} onChange={(e) => up({ date: e.target.value })} />
                  <input className={small} placeholder="name" disabled={d} value={h.name} onChange={(e) => up({ name: e.target.value })} /></div>)} />
          </Field>
        </>
      );
    case "subpipeline":
      return (
        <>
          <Field label="Pipeline" error={errors.pipeline_id}>
            <select className="orbit-input" disabled={d} value={S(c.pipeline_id)} onChange={(e) => set({ pipeline_id: e.target.value, node_id: "" })}>
              <option value="">Select a published pipeline…</option>
              {pipelines?.filter((p) => p.current_version > 0).map((p) => <option key={p.id} value={p.id}>{p.name} (v{p.current_version})</option>)}
            </select>
          </Field>
          <Field label="Use the result of the step" error={errors.node_id}>
            <select className="orbit-input" disabled={d || !sub} value={S(c.node_id)} onChange={(e) => set({ node_id: e.target.value })}>
              <option value="">Select a step…</option>
              {sub?.graph.nodes.map((n) => <option key={n.id} value={n.id}>{n.label} ({n.type})</option>)}
            </select>
          </Field>
          <p className="text-[11px] text-orbit-subtle">Its latest published version runs inside this pipeline, with the same period parameters; nothing is written by it.</p>
        </>
      );
    // ── transformations ──
    case "clean":
      return (
        <>
          <Field label="Text columns to clean (none = all text columns)"><MultiColumns disabled={d} value={arr(c.columns)} columns={cols.filter((x) => /VARCHAR|string/i.test(x.type))} onChange={(v) => set({ columns: v })} /></Field>
          <Check disabled={d} label="Trim and collapse spaces" value={c.trim !== false} onChange={(v) => set({ trim: v })} />
          <Field label="Case">
            <select className="orbit-input" disabled={d} value={S(c.case) || "none"} onChange={(e) => set({ case: e.target.value })}>
              <option value="none">Keep</option><option value="upper">UPPER</option><option value="lower">lower</option><option value="title">Title Case</option>
            </select>
          </Field>
          <Field label="Values treated as empty" hint="Comma separated; compared without case."><input className="orbit-input font-mono" disabled={d} placeholder=",NA,N/A,null,-,?" value={c.null_tokens === undefined ? "" : arr(c.null_tokens).join(",")} onChange={(e) => set({ null_tokens: e.target.value === "" ? undefined : e.target.value.split(",") })} /></Field>
          <Field label="Convert to numbers (12,50 · 1 234,50 · €9.90)"><MultiColumns disabled={d} value={arr(c.number_columns)} columns={cols} onChange={(v) => set({ number_columns: v })} /></Field>
          <Field label="Convert to dates (05/01/2024 · 2024-01-05 · 5.1.2024)"><MultiColumns disabled={d} value={arr(c.date_columns)} columns={cols} onChange={(v) => set({ date_columns: v })} /></Field>
        </>
      );
    case "fill_missing": {
      const rules = (c.rules as { column: string; strategy: string; value?: string }[]) || [];
      return (
        <>
          <Field label="Columns to fill" error={errors.rules}>
            <ListEditor disabled={d} items={rules} empty={{ column: "", strategy: "value", value: "" }} addLabel="Add a column" onChange={(v) => set({ rules: v })}
              render={(r, up) => (
                <>
                  <ColumnSelect disabled={d} value={r.column} columns={cols} onChange={(v) => up({ column: v })} />
                  <div className="grid grid-cols-2 gap-1.5">
                    <select className={small} disabled={d} value={r.strategy} onChange={(e) => up({ strategy: e.target.value })}>
                      {["value", "zero", "empty_text", "mean", "median", "mode", "previous", "next"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                    </select>
                    {r.strategy === "value" && <input className={small} placeholder="value" disabled={d} value={S(r.value)} onChange={(e) => up({ value: e.target.value })} />}
                  </div>
                </>
              )} />
          </Field>
          <Field label="Order (for previous / next)" error={errors.order_by}><ColumnSelect disabled={d} value={S(c.order_by)} columns={cols} onChange={(v) => set({ order_by: v })} /></Field>
          <Field label="Per group (mean, median, previous… within)"><MultiColumns disabled={d} value={arr(c.partition_by)} columns={cols} onChange={(v) => set({ partition_by: v })} /></Field>
        </>
      );
    }
    case "derived_column":
      return (
        <Field label="New / replaced columns" error={errors.columns} hint={`SQL expressions over upstream columns, e.g. amount * 1.2, year(order_date). ${PARAM_HINT}`}>
          <ListEditor disabled={d} items={(c.columns as { name: string; expression: string }[]) || []} empty={{ name: "", expression: "" }} addLabel="Add a column"
            onChange={(v) => set({ columns: v })} render={(x, up) => (
              <>
                <input className={`${small} font-mono`} placeholder="column name" disabled={d} value={x.name} onChange={(e) => up({ name: e.target.value })} />
                <textarea rows={2} className={`${small} font-mono`} placeholder="expression" disabled={d} value={x.expression} onChange={(e) => up({ expression: e.target.value })} />
              </>
            )} />
        </Field>
      );
    case "split_column":
      return (
        <>
          <Field label="Column" error={errors.column}><ColumnSelect disabled={d} value={S(c.column)} columns={cols} onChange={(v) => set({ column: v })} /></Field>
          <Field label="Delimiter" error={errors.delimiter} hint="The last part keeps the rest of the text."><input className="orbit-input font-mono" disabled={d} value={S(c.delimiter)} onChange={(e) => set({ delimiter: e.target.value })} /></Field>
          <Field label="New columns" error={errors.into}>
            <input className="orbit-input font-mono" disabled={d} placeholder="first_name, last_name" value={arr(c.into).join(", ")} onChange={(e) => set({ into: e.target.value.split(",").map((x) => x.trim()) })} />
          </Field>
          <Check disabled={d} label="Keep the original column" value={c.keep_original !== false} onChange={(v) => set({ keep_original: v })} />
        </>
      );
    case "merge_columns":
      return (
        <>
          <Field label="Columns (in order)" error={errors.columns}><MultiColumns disabled={d} value={arr(c.columns)} columns={cols} onChange={(v) => set({ columns: v })} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Separator"><input className="orbit-input font-mono" disabled={d} value={S(c.separator)} onChange={(e) => set({ separator: e.target.value })} /></Field>
            <Field label="New column" error={errors.into}><input className="orbit-input font-mono" disabled={d} value={S(c.into)} onChange={(e) => set({ into: e.target.value })} /></Field>
          </div>
          <Check disabled={d} label="Skip empty values" value={c.skip_nulls !== false} onChange={(v) => set({ skip_nulls: v })} />
          <Check disabled={d} label="Keep the original columns" value={c.keep_originals !== false} onChange={(v) => set({ keep_originals: v })} />
        </>
      );
    case "lookup": {
      const ref = upstream[1];
      const mapping = Array.isArray(c.mapping) ? (c.mapping as { from: string; to: string }[]) : Object.entries((c.mapping as Record<string, string>) || {}).map(([from, to]) => ({ from, to }));
      return (
        <>
          <Field label="Code column" error={errors.column}><ColumnSelect disabled={d} value={S(c.column)} columns={cols} onChange={(v) => set({ column: v })} /></Field>
          <Field label="Label column (new or replaced)" error={errors.into}><input className="orbit-input font-mono" disabled={d} placeholder={`${S(c.column) || "code"}_label`} value={S(c.into)} onChange={(e) => set({ into: e.target.value })} /></Field>
          {ref ? (
            <div className="grid grid-cols-2 gap-2">
              <Field label={`Key in ${ref.label}`} error={errors.ref_key}><ColumnSelect disabled={d} value={S(c.ref_key)} columns={ref.schema} onChange={(v) => set({ ref_key: v })} /></Field>
              <Field label="Label" error={errors.ref_value}><ColumnSelect disabled={d} value={S(c.ref_value)} columns={ref.schema} onChange={(v) => set({ ref_value: v })} /></Field>
            </div>
          ) : (
            <Field label="Mapping" error={errors.mapping} hint="Or connect a reference table as second input.">
              <ListEditor disabled={d} items={mapping} empty={{ from: "", to: "" }} addLabel="Add a pair" onChange={(v) => set({ mapping: v })}
                render={(m, up) => <div className="grid grid-cols-2 gap-1.5"><input className={small} placeholder="FR" disabled={d} value={m.from} onChange={(e) => up({ from: e.target.value })} />
                  <input className={small} placeholder="France" disabled={d} value={m.to} onChange={(e) => up({ to: e.target.value })} /></div>} />
            </Field>
          )}
          <Field label="When no match"><input className="orbit-input" disabled={d} placeholder="keep the original value" value={S(c.default)} onChange={(e) => set({ default: e.target.value })} /></Field>
        </>
      );
    }
    case "sort_limit":
      return (
        <>
          <Field label="Sort by" error={errors.order_by}>
            <ListEditor disabled={d} items={(c.order_by as { column: string; desc: boolean }[]) || []} empty={{ column: "", desc: true }} addLabel="Add a sort column"
              onChange={(v) => set({ order_by: v })} render={(o, up) => (
                <div className="grid grid-cols-[1fr_auto] gap-1.5"><ColumnSelect disabled={d} value={o.column} columns={cols} onChange={(v) => up({ column: v })} />
                  <select className={small} disabled={d} value={o.desc ? "desc" : "asc"} onChange={(e) => up({ desc: e.target.value === "desc" })}><option value="desc">desc</option><option value="asc">asc</option></select></div>)} />
          </Field>
          <Field label="Keep the first N rows (empty = all)" error={errors.limit}><input type="number" className="orbit-input" disabled={d} value={S(c.limit)} onChange={(e) => set({ limit: num(e.target.value) })} /></Field>
          <Field label="…per group (Top N per region…)"><MultiColumns disabled={d} value={arr(c.group_by)} columns={cols} onChange={(v) => set({ group_by: v })} /></Field>
        </>
      );
    case "pivot":
      return (
        <>
          <Field label="Rows (index)" error={errors.index}><MultiColumns disabled={d} value={arr(c.index)} columns={cols} onChange={(v) => set({ index: v })} /></Field>
          <Field label="Column whose values become columns" error={errors.column}><ColumnSelect disabled={d} value={S(c.column)} columns={cols} onChange={(v) => set({ column: v })} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Value" error={errors.value}><ColumnSelect disabled={d} value={S(c.value)} columns={cols} onChange={(v) => set({ value: v })} /></Field>
            <Field label="Aggregate" error={errors.agg}>
              <select className="orbit-input" disabled={d} value={S(c.agg) || "sum"} onChange={(e) => set({ agg: e.target.value })}>{["sum", "count", "avg", "min", "max", "first"].map((a) => <option key={a}>{a}</option>)}</select>
            </Field>
          </div>
          <Field label="Fixed column values (optional)" hint="e.g. Q1, Q2, Q3, Q4 — otherwise the values found in the data (columns known at run time).">
            <input className="orbit-input font-mono" disabled={d} value={arr(c.values).join(", ")} onChange={(e) => set({ values: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
          </Field>
        </>
      );
    case "unpivot":
      return (
        <>
          <Field label="Columns to turn into rows" error={errors.columns}>
            {cols.length ? <MultiColumns disabled={d} value={arr(c.columns)} columns={cols} onChange={(v) => set({ columns: v })} />
              : <input className="orbit-input font-mono" disabled={d} placeholder="jan, feb, mar" value={arr(c.columns).join(", ")} onChange={(e) => set({ columns: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />}
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Name column" error={errors.name_column}><input className="orbit-input font-mono" disabled={d} value={S(c.name_column)} onChange={(e) => set({ name_column: e.target.value })} /></Field>
            <Field label="Value column" error={errors.value_column}><input className="orbit-input font-mono" disabled={d} value={S(c.value_column)} onChange={(e) => set({ value_column: e.target.value })} /></Field>
          </div>
          <Check disabled={d} label="Keep empty values" value={!!c.keep_nulls} onChange={(v) => set({ keep_nulls: v })} />
        </>
      );
    case "window":
      return (
        <Field label="Functions" error={errors.functions}>
          <ListEditor disabled={d} items={(c.functions as Cfg[]) || []} empty={{ func: "running_sum", column: "", partition_by: [], order_by: "", alias: "" }} addLabel="Add a function"
            onChange={(v) => set({ functions: v })} render={(f, up) => (
              <>
                <div className="grid grid-cols-2 gap-1.5">
                  <select className={small} disabled={d} value={S(f.func)} onChange={(e) => up({ func: e.target.value })}>
                    {["running_sum", "running_avg", "running_count", "running_min", "running_max", "moving_avg", "moving_sum", "rank", "dense_rank", "row_number", "percent_rank", "lag", "lead", "first_value", "last_value", "percent_of_total"].map((x) => <option key={x}>{x}</option>)}
                  </select>
                  <input className={`${small} font-mono`} placeholder="alias" disabled={d} value={S(f.alias)} onChange={(e) => up({ alias: e.target.value })} />
                </div>
                {!["rank", "dense_rank", "row_number", "percent_rank"].includes(S(f.func)) && <ColumnSelect disabled={d} placeholder="value column…" value={S(f.column)} columns={cols} onChange={(v) => up({ column: v })} />}
                <div className="grid grid-cols-[1fr_auto] gap-1.5">
                  <ColumnSelect disabled={d} placeholder="order by…" value={S(f.order_by)} columns={cols} onChange={(v) => up({ order_by: v })} />
                  <select className={small} disabled={d} value={f.desc ? "desc" : "asc"} onChange={(e) => up({ desc: e.target.value === "desc" })}><option value="asc">asc</option><option value="desc">desc</option></select>
                </div>
                {S(f.func).startsWith("moving") && <input type="number" className={small} placeholder="window size (rows)" disabled={d} value={S(f.size)} onChange={(e) => up({ size: num(e.target.value) })} />}
                {["lag", "lead"].includes(S(f.func)) && <input type="number" className={small} placeholder="offset (1)" disabled={d} value={S(f.offset)} onChange={(e) => up({ offset: num(e.target.value) })} />}
                <div className="text-[10px] text-orbit-subtle">per group:</div>
                <MultiColumns disabled={d} value={arr(f.partition_by)} columns={cols} onChange={(v) => up({ partition_by: v })} />
              </>
            )} />
        </Field>
      );
    case "scd2":
      return (
        <>
          <Field label="Entity key" error={errors.keys}><MultiColumns disabled={d} value={arr(c.keys)} columns={cols} onChange={(v) => set({ keys: v })} /></Field>
          <Field label="Columns whose changes are tracked (none = all)" error={errors.tracked}><MultiColumns disabled={d} value={arr(c.tracked)} columns={cols} onChange={(v) => set({ tracked: v })} /></Field>
          <div className="grid grid-cols-[90px_1fr] gap-2">
            <Field label="Layer"><select className="orbit-input" disabled={d} value={S(c.target_layer) || "silver"} onChange={(e) => set({ target_layer: e.target.value })}><option>silver</option><option>gold</option></select></Field>
            <Field label="History dataset" error={errors.target_name}><input className="orbit-input font-mono" disabled={d} value={S(c.target_name)} onChange={(e) => set({ target_name: e.target.value })} /></Field>
          </div>
          <p className="text-[11px] text-orbit-subtle">Connect a Dataset output (overwrite) with the same name after this step: it holds the full history.</p>
          <Field label="Change date"><select className="orbit-input" disabled={d} value={S(c.effective) || "run_time"} onChange={(e) => set({ effective: e.target.value })}><option value="run_time">time of the run</option><option value="run_date">{"{{run_date}}"} (backfills)</option></select></Field>
          <Check disabled={d} label="Close rows that disappeared from the source" value={!!c.close_missing} onChange={(v) => set({ close_missing: v })} />
        </>
      );
    case "surrogate_key":
      return (
        <>
          <Field label="Business key column(s)" error={errors.keys} hint="The same values always give the same key. An empty key gives -1 (Unknown)."><MultiColumns disabled={d} value={arr(c.keys)} columns={cols} onChange={(v) => set({ keys: v })} /></Field>
          <Field label="New key column" error={errors.into}><input className="orbit-input font-mono" disabled={d} placeholder="customer_key" value={S(c.into)} onChange={(e) => set({ into: e.target.value })} /></Field>
        </>
      );
    case "python":
      return (
        <Field label="Code" error={errors.code} hint="Allowed imports: pandas, numpy, math, datetime, re, statistics, json, decimal, string. No files, network or system access; 60 s limit.">
          <textarea rows={14} disabled={d} spellCheck={false} className="w-full p-3 rounded-lg bg-orbit-bg border border-orbit-border font-mono text-[11px] text-[#E2E8F0] focus:outline-none focus:border-orbit-purple" value={S(c.code)} onChange={(e) => set({ code: e.target.value })} />
        </Field>
      );
    // ── quality ──
    case "schema_check":
      return (
        <>
          <Field label="Expected columns (optional)" error={errors.expected}>
            <ListEditor disabled={d} items={(c.expected as { name: string; type: string }[]) || []} empty={{ name: "", type: "any" }} addLabel="Add a column"
              onChange={(v) => set({ expected: v })} render={(x, up) => (
                <div className="grid grid-cols-[1fr_100px] gap-1.5"><input className={`${small} font-mono`} placeholder="column" disabled={d} value={x.name} onChange={(e) => up({ name: e.target.value })} />
                  <select className={small} disabled={d} value={x.type} onChange={(e) => up({ type: e.target.value })}>{["any", "string", "integer", "double", "boolean", "date", "timestamp"].map((y) => <option key={y}>{y}</option>)}</select></div>)} />
          </Field>
          {cols.length > 0 && !d && <button className="text-[11px] text-orbit-purple" onClick={() => set({ expected: cols.map((x) => ({ name: x.name, type: "any" })) })}>Fill with the current columns</button>}
          <Field label="Mode"><select className="orbit-input" disabled={d} value={S(c.mode) || "at_least"} onChange={(e) => set({ mode: e.target.value })}><option value="at_least">These columns at least</option><option value="exact">Exactly these columns</option></select></Field>
          <Check disabled={d} label="Also compare with the previous run (disappeared / retyped columns)" value={c.compare_previous !== false} onChange={(v) => set({ compare_previous: v })} />
          <OnFailure c={c} set={set} d={d} />
        </>
      );
    case "data_compare":
      return (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Max drop (%)" error={errors.max_drop_pct}><input type="number" className="orbit-input" disabled={d} value={S(c.max_drop_pct)} onChange={(e) => set({ max_drop_pct: num(e.target.value) })} /></Field>
            <Field label="Max growth (%)"><input type="number" className="orbit-input" disabled={d} value={S(c.max_growth_pct)} onChange={(e) => set({ max_growth_pct: num(e.target.value) })} /></Field>
          </div>
          <Field label="Minimum rows"><input type="number" className="orbit-input" disabled={d} value={S(c.min_rows)} onChange={(e) => set({ min_rows: num(e.target.value) })} /></Field>
          <p className="text-[11px] text-orbit-subtle">Compared with the row count at this step during the previous successful run.</p>
          <OnFailure c={c} set={set} d={d} />
        </>
      );
    case "anomaly_detection":
      return (
        <>
          <Field label="Numeric column" error={errors.column}><ColumnSelect disabled={d} value={S(c.column)} columns={cols} onChange={(v) => set({ column: v })} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Method"><select className="orbit-input" disabled={d} value={S(c.method) || "zscore"} onChange={(e) => set({ method: e.target.value, threshold: e.target.value === "iqr" ? 1.5 : 3 })}><option value="zscore">z-score</option><option value="iqr">IQR</option></select></Field>
            <Field label="Threshold"><input type="number" step="0.1" className="orbit-input" disabled={d} value={S(c.threshold)} onChange={(e) => set({ threshold: num(e.target.value) })} /></Field>
          </div>
          <Field label="Then"><select className="orbit-input" disabled={d} value={S(c.action) || "flag"} onChange={(e) => set({ action: e.target.value })}><option value="flag">Flag (add a column)</option><option value="remove">Remove the rows</option><option value="fail">Stop the run</option></select></Field>
          {(c.action || "flag") === "flag" && <Field label="Flag column"><input className="orbit-input font-mono" disabled={d} value={S(c.flag_column) || "is_anomaly"} onChange={(e) => set({ flag_column: e.target.value })} /></Field>}
          <Field label="Per group"><MultiColumns disabled={d} value={arr(c.group_by)} columns={cols} onChange={(v) => set({ group_by: v })} /></Field>
        </>
      );
    case "branch":
      return (
        <>
          <Field label="Condition" error={errors.condition} hint={`Rows where it is true leave by the green 'true' port, the others by the red 'false' port. ${PARAM_HINT}`}>
            <textarea rows={3} disabled={d} className="orbit-input font-mono text-[11px]" value={S(c.condition)} onChange={(e) => set({ condition: e.target.value })} />
          </Field>
        </>
      );
    // ── destinations ──
    case "export_file":
      return (
        <>
          <Field label="Format" error={errors.format}><select className="orbit-input" disabled={d} value={S(c.format) || "csv"} onChange={(e) => set({ format: e.target.value })}><option value="csv">CSV</option><option value="xlsx">Excel</option><option value="json">JSON</option><option value="parquet">Parquet</option></select></Field>
          <Field label="File name (without extension)" error={errors.filename} hint={PARAM_HINT}><input className="orbit-input font-mono" disabled={d} value={S(c.filename)} onChange={(e) => set({ filename: e.target.value })} /></Field>
          <p className="text-[11px] text-orbit-subtle">Stored in MinIO; the download link appears on the run page. A copy also goes to ORBIT_EXPORT_DIR when it is set.</p>
          <OnError c={c} set={set} d={d} />
        </>
      );
    case "rest_output":
      return (
        <>
          <Field label="URL" error={errors.url}><input className="orbit-input font-mono" disabled={d} placeholder="https://hooks.example.com/…" value={S(c.url)} onChange={(e) => set({ url: e.target.value })} /></Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label="Method"><select className="orbit-input" disabled={d} value={S(c.method) || "POST"} onChange={(e) => set({ method: e.target.value })}><option>POST</option><option>PUT</option><option>PATCH</option></select></Field>
            <Field label="Send"><select className="orbit-input" disabled={d} value={S(c.mode) || "batch"} onChange={(e) => set({ mode: e.target.value })}><option value="batch">batches</option><option value="row">one per row</option></select></Field>
            <Field label="Batch size" error={errors.batch_size}><input type="number" className="orbit-input" disabled={d} value={S(c.batch_size)} onChange={(e) => set({ batch_size: num(e.target.value) })} /></Field>
          </div>
          <Field label="Wrap the array in a key (optional)"><input className="orbit-input font-mono" disabled={d} placeholder="records" value={S(c.wrapper_key)} onChange={(e) => set({ wrapper_key: e.target.value })} /></Field>
          <Field label="Extra headers">
            <ListEditor disabled={d} items={(c.headers as { name: string; value: string }[]) || []} empty={{ name: "", value: "" }} addLabel="Add a header" onChange={(v) => set({ headers: v })}
              render={(h, up) => <div className="grid grid-cols-2 gap-1.5"><input className={small} placeholder="X-Source" disabled={d} value={h.name} onChange={(e) => up({ name: e.target.value })} /><input className={small} placeholder="value or {{run_date}}" disabled={d} value={h.value} onChange={(e) => up({ value: e.target.value })} /></div>} />
          </Field>
          <OnError c={c} set={set} d={d} />
        </>
      );
    case "email_report":
      return (
        <>
          <Field label="To" error={errors.to}><input className="orbit-input" disabled={d} placeholder="a@company.com, b@company.com" value={Array.isArray(c.to) ? arr(c.to).join(", ") : S(c.to)} onChange={(e) => set({ to: e.target.value })} /></Field>
          <Field label="Subject" error={errors.subject} hint={PARAM_HINT}><input className="orbit-input" disabled={d} value={S(c.subject)} onChange={(e) => set({ subject: e.target.value })} /></Field>
          <Field label="Message"><textarea rows={2} className="orbit-input" disabled={d} value={S(c.message)} onChange={(e) => set({ message: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Include"><select className="orbit-input" disabled={d} value={S(c.include) || "table"} onChange={(e) => set({ include: e.target.value })}><option value="table">Table in the e-mail</option><option value="csv">CSV attachment</option><option value="both">Both</option></select></Field>
            <Field label="Rows in the table"><input type="number" className="orbit-input" disabled={d} value={S(c.max_rows)} onChange={(e) => set({ max_rows: num(e.target.value) })} /></Field>
          </div>
          <p className="text-[11px] text-orbit-subtle">Needs ORBIT_SMTP_HOST in .env (the Docker stack includes Mailpit: http://localhost:8025).</p>
          <OnError c={c} set={set} d={d} />
        </>
      );
    default:
      return <div className="text-xs text-orbit-muted">Unknown node type.</div>;
  }
}
