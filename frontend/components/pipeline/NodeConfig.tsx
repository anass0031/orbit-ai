"use client";

import { Plus, Trash2 } from "lucide-react";
import React from "react";
import { Field, cn } from "@/components/ui";
import type { ColumnSchema, Dataset } from "@/lib/types";
import { ColumnSelect, ListEditor, MultiColumns, arr, small } from "./fields";
import { ExtraConfig } from "./NodeConfigExtra";
import { type PNodeData } from "./nodes";

type Cfg = Record<string, unknown>;
interface Props {
  data: PNodeData;
  upstream: { id: string; label: string; schema: ColumnSchema[] }[];
  datasets: Dataset[];
  readOnly: boolean;
  errors: Record<string, string>;
  onChange: (config: Cfg) => void;
}

export function NodeConfig({ data, upstream, datasets, readOnly, errors, onChange }: Props) {
  const c = data.config as Cfg;
  const set = (patch: Cfg) => onChange({ ...c, ...patch });
  const cols = upstream[0]?.schema || [];
  const d = readOnly;

  switch (data.type) {
    case "dataset_input":
      return (
        <>
          <Field label="Dataset" error={errors.dataset_id}>
            <select className="orbit-input" disabled={d} value={String(c.dataset_id || "")} onChange={(e) => set({ dataset_id: e.target.value, version: null })}>
              <option value="">Select dataset…</option>
              {(["bronze", "silver", "gold"] as const).map((l) => (
                <optgroup key={l} label={l.toUpperCase()}>
                  {datasets.filter((x) => x.layer === l).map((x) => <option key={x.id} value={x.id}>{x.qualified_name} (v{x.current_version}, {x.row_count?.toLocaleString()} rows)</option>)}
                </optgroup>
              ))}
            </select>
          </Field>
          <Field label="Version" hint="Pin a version for reproducible runs, or follow the latest.">
            <select className="orbit-input" disabled={d} value={String(c.version ?? "")} onChange={(e) => set({ version: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Latest committed version</option>
              {Array.from({ length: datasets.find((x) => x.id === c.dataset_id)?.current_version || 0 }, (_, i) => i + 1).reverse().map((v) => <option key={v} value={v}>v{v}</option>)}
            </select>
          </Field>
        </>
      );
    case "filter":
      return (
        <Field label="Condition (SQL boolean expression)" error={errors.condition} hint="e.g. amount >= 0 AND status = 'completed'">
          <textarea rows={4} disabled={d} className="orbit-input font-mono text-[11px]" value={String(c.condition || "")} onChange={(e) => set({ condition: e.target.value })} />
        </Field>
      );
    case "sql":
      return (
        <Field label="SQL expression" error={errors.query} hint={upstream.length > 1 ? `Tables: input (= input_1)${upstream.map((u, i) => `, input_${i + 1} = ${u.label}`).join("")}` : "Upstream table is named input. Only a single SELECT is allowed."}>
          <textarea rows={10} disabled={d} spellCheck={false} className="w-full p-3 rounded-lg bg-orbit-bg border border-orbit-border font-mono text-[11px] text-[#E2E8F0] focus:outline-none focus:border-orbit-purple" value={String(c.query || "")} onChange={(e) => set({ query: e.target.value })} />
        </Field>
      );
    case "select": {
      const chosen = (c.columns as { name: string; alias?: string; cast?: string }[]) || [];
      const byName = Object.fromEntries(chosen.map((x) => [x.name, x]));
      return (
        <Field label="Columns" error={errors.columns}>
          {!cols.length ? <div className="text-[11px] text-orbit-subtle">Connect an input and validate to list columns.</div> : (
            <div className="space-y-1 max-h-72 overflow-y-auto">
              {cols.map((col) => {
                const sel = byName[col.name];
                const update = (patch: Partial<{ alias: string; cast: string }> | null) => {
                  if (patch === null) set({ columns: chosen.filter((x) => x.name !== col.name) });
                  else if (!sel) set({ columns: [...chosen, { name: col.name, ...patch }] });
                  else set({ columns: chosen.map((x) => (x.name === col.name ? { ...x, ...patch } : x)) });
                };
                return (
                  <div key={col.name} className={cn("p-1.5 rounded-lg border text-[11px]", sel ? "border-orbit-purple/40 bg-orbit-purple/5" : "border-orbit-border")}>
                    <label className="flex items-center gap-2 font-mono text-white">
                      <input type="checkbox" disabled={d} checked={!!sel} onChange={(e) => update(e.target.checked ? {} : null)} />{col.name}<span className="text-orbit-subtle">{col.type}</span>
                    </label>
                    {sel && (
                      <div className="grid grid-cols-2 gap-1.5 mt-1.5">
                        <input className="orbit-input !py-1 !text-[11px] font-mono" placeholder="alias" disabled={d} value={sel.alias || ""} onChange={(e) => update({ alias: e.target.value })} />
                        <select className="orbit-input !py-1 !text-[11px]" disabled={d} value={sel.cast || ""} onChange={(e) => update({ cast: e.target.value })}>
                          <option value="">keep type</option>{["string", "integer", "double", "boolean", "date", "timestamp"].map((t) => <option key={t}>{t}</option>)}
                        </select>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Field>
      );
    }
    case "join": {
      const leftId = (c.left as string) || upstream[0]?.id;
      const left = upstream.find((u) => u.id === leftId) || upstream[0];
      const right = upstream.find((u) => u.id !== left?.id);
      const lk = arr(c.left_on), rk = arr(c.right_on);
      return (
        <>
          <Field label="Join type" error={errors.how}>
            <select className="orbit-input" disabled={d} value={String(c.how || "inner")} onChange={(e) => set({ how: e.target.value })}>
              {["inner", "left", "right", "full"].map((h) => <option key={h}>{h}</option>)}
            </select>
          </Field>
          <Field label="Left input">
            <select className="orbit-input" disabled={d || upstream.length < 2} value={left?.id || ""} onChange={(e) => set({ left: e.target.value })}>
              {upstream.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
            </select>
          </Field>
          <Field label="Keys (left = right)" error={errors.left_on || errors.right_on}>
            <div className="space-y-1.5">
              {lk.map((_, i) => (
                <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-1.5">
                  <ColumnSelect disabled={d} value={lk[i]} columns={left?.schema || []} onChange={(v) => set({ left_on: lk.map((x, j) => (j === i ? v : x)) })} />
                  <ColumnSelect disabled={d} value={rk[i] || ""} columns={right?.schema || []} onChange={(v) => set({ right_on: lk.map((_x, j) => (j === i ? v : rk[j] || "")) })} />
                  <button disabled={d || lk.length < 2} onClick={() => set({ left_on: lk.filter((_x, j) => j !== i), right_on: rk.filter((_x, j) => j !== i) })} className="text-orbit-subtle hover:text-orbit-redLight disabled:opacity-30"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              ))}
              {!d && <button className="text-[11px] text-orbit-purple flex items-center gap-1" onClick={() => set({ left_on: [...lk, ""], right_on: [...rk, ""] })}><Plus className="w-3 h-3" />Add key pair</button>}
            </div>
          </Field>
          <p className="text-[11px] text-orbit-subtle">Right-side columns that clash with left names get a <span className="font-mono">_right</span> suffix.</p>
        </>
      );
    }
    case "union":
      return (
        <label className="flex items-center gap-2 text-xs text-white"><input type="checkbox" disabled={d} checked={!!c.distinct} onChange={(e) => set({ distinct: e.target.checked })} />Remove duplicate rows (UNION instead of UNION ALL)</label>
      );
    case "aggregate": {
      const aggs = (c.aggregations as { func: string; column: string; alias: string }[]) || [];
      return (
        <>
          <Field label="Group by" error={errors.group_by}><MultiColumns disabled={d} value={arr(c.group_by)} columns={cols} onChange={(v) => set({ group_by: v })} /></Field>
          <Field label="Aggregations" error={errors.aggregations}>
            <div className="space-y-1.5">
              {aggs.map((a, i) => {
                const upd = (patch: Partial<typeof a>) => set({ aggregations: aggs.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
                return (
                  <div key={i} className="grid grid-cols-[80px_1fr] gap-1.5 p-1.5 rounded-lg border border-orbit-border">
                    <select className="orbit-input !py-1 !text-[11px]" disabled={d} value={a.func} onChange={(e) => upd({ func: e.target.value })}>
                      {["count", "count_distinct", "sum", "avg", "min", "max"].map((f) => <option key={f}>{f}</option>)}
                    </select>
                    <ColumnSelect disabled={d} allowStar={a.func === "count"} value={a.column === "*" ? "" : a.column} columns={cols} onChange={(v) => upd({ column: v || "*" })} />
                    <input className="orbit-input !py-1 !text-[11px] font-mono" placeholder="alias" disabled={d} value={a.alias} onChange={(e) => upd({ alias: e.target.value })} />
                    <button disabled={d} onClick={() => set({ aggregations: aggs.filter((_x, j) => j !== i) })} className="text-[11px] text-orbit-subtle hover:text-orbit-redLight text-left">Remove</button>
                  </div>
                );
              })}
              {!d && <button className="text-[11px] text-orbit-purple flex items-center gap-1" onClick={() => set({ aggregations: [...aggs, { func: "sum", column: "", alias: "" }] })}><Plus className="w-3 h-3" />Add aggregation</button>}
            </div>
          </Field>
        </>
      );
    }
    case "dedupe":
      return (
        <>
          <Field label="Key columns" error={errors.keys}><MultiColumns disabled={d} value={arr(c.keys)} columns={cols} onChange={(v) => set({ keys: v })} /></Field>
          <Field label="Keep the row with the latest" error={errors.order_by} hint="Optional — otherwise an arbitrary row per key is kept.">
            <ColumnSelect disabled={d} value={String(c.order_by || "")} columns={cols} onChange={(v) => set({ order_by: v })} />
          </Field>
        </>
      );
    case "quality_check": {
      const rules = (c.rules as { type: string; column?: string; params?: Cfg; on_failure: string }[]) || [];
      return (
        <Field label="Assertions" error={errors.rules}>
          <div className="space-y-2">
            {rules.map((r, i) => {
              const upd = (patch: Cfg) => set({ rules: rules.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
              const p = r.params || {};
              return (
                <div key={i} className="p-2 rounded-lg border border-orbit-border space-y-1.5">
                  <div className="grid grid-cols-2 gap-1.5">
                    <select className="orbit-input !py-1 !text-[11px]" disabled={d} value={r.type} onChange={(e) => upd({ type: e.target.value, params: {} })}>
                      {["not_null", "unique", "range", "regex", "allowed_values", "row_count", "custom_sql"].map((t) => <option key={t}>{t}</option>)}
                    </select>
                    <select className="orbit-input !py-1 !text-[11px]" disabled={d} value={r.on_failure} onChange={(e) => upd({ on_failure: e.target.value })}>
                      <option value="fail">on failure: fail run</option><option value="quarantine">quarantine rows</option><option value="warn">warn only</option>
                    </select>
                  </div>
                  {!["row_count", "custom_sql"].includes(r.type) && <ColumnSelect disabled={d} value={r.column || ""} columns={cols} onChange={(v) => upd({ column: v })} />}
                  {r.type === "range" && <div className="grid grid-cols-2 gap-1.5"><input className="orbit-input !py-1 !text-[11px]" placeholder="min" disabled={d} value={String(p.min ?? "")} onChange={(e) => upd({ params: { ...p, min: e.target.value === "" ? null : Number(e.target.value) } })} /><input className="orbit-input !py-1 !text-[11px]" placeholder="max" disabled={d} value={String(p.max ?? "")} onChange={(e) => upd({ params: { ...p, max: e.target.value === "" ? null : Number(e.target.value) } })} /></div>}
                  {r.type === "regex" && <input className="orbit-input !py-1 !text-[11px] font-mono" placeholder="pattern, e.g. ^[^@]+@[^@]+$" disabled={d} value={String(p.pattern ?? "")} onChange={(e) => upd({ params: { ...p, pattern: e.target.value } })} />}
                  {r.type === "allowed_values" && <input className="orbit-input !py-1 !text-[11px]" placeholder="comma separated values" disabled={d} value={arr(p.values).join(",")} onChange={(e) => upd({ params: { ...p, values: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } })} />}
                  {r.type === "row_count" && <input className="orbit-input !py-1 !text-[11px]" placeholder="minimum rows" disabled={d} value={String(p.min ?? "")} onChange={(e) => upd({ params: { ...p, min: e.target.value === "" ? null : Number(e.target.value) } })} />}
                  {r.type === "custom_sql" && <input className="orbit-input !py-1 !text-[11px] font-mono" placeholder="valid-row condition, e.g. quantity > 0" disabled={d} value={String(p.expression ?? "")} onChange={(e) => upd({ params: { ...p, expression: e.target.value } })} />}
                  {!d && <button className="text-[11px] text-orbit-subtle hover:text-orbit-redLight" onClick={() => set({ rules: rules.filter((_x, j) => j !== i) })}>Remove assertion</button>}
                </div>
              );
            })}
            {!d && <button className="text-[11px] text-orbit-purple flex items-center gap-1" onClick={() => set({ rules: [...rules, { type: "not_null", column: "", on_failure: "fail" }] })}><Plus className="w-3 h-3" />Add assertion</button>}
          </div>
        </Field>
      );
    }
    case "output":
      return (
        <>
          <Field label="Layer" error={errors.layer}>
            <select className="orbit-input" disabled={d} value={String(c.layer || "silver")} onChange={(e) => set({ layer: e.target.value })}>
              <option value="silver">Silver — cleaned & conformed</option><option value="gold">Gold — business-ready</option>
            </select>
          </Field>
          <Field label="Dataset name" error={errors.name} hint={`Each run writes a new version of ${String(c.layer || "silver")}.${String(c.name || "…")}; older versions are kept.`}>
            <input className="orbit-input font-mono" disabled={d} value={String(c.name || "")} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field label="Write mode" error={errors.mode} hint={c.mode === "upsert" ? "Rows whose key already exists are replaced, the others are added." : c.mode === "append" ? "The new rows are added to the previous version." : "The new version contains exactly these rows."}>
            <select className="orbit-input" disabled={d} value={String(c.mode || "overwrite")} onChange={(e) => set({ mode: e.target.value })}>
              <option value="overwrite">Overwrite</option><option value="append">Append</option><option value="upsert">Upsert (merge on keys)</option>
            </select>
          </Field>
          {c.mode === "upsert" && <Field label="Key columns" error={errors.keys}><MultiColumns disabled={d} value={arr(c.keys)} columns={cols} onChange={(v) => set({ keys: v })} /></Field>}
          {c.layer === "gold" && (
            <>
              <Field label="Role in the data model" error={errors.model_role} hint="Facts and dimensions form the star schema; their keys are checked before anything is written.">
                <select className="orbit-input" disabled={d} value={String(c.model_role || "")} onChange={(e) => set({ model_role: e.target.value || undefined })}>
                  <option value="">None</option><option value="fact">Fact</option><option value="dimension">Dimension</option><option value="mart">Mart</option>
                </select>
              </Field>
              {(c.model_role === "fact" || c.model_role === "dimension") && <Field label="Primary key" error={errors.primary_key}><MultiColumns disabled={d} value={arr(c.primary_key)} columns={cols} onChange={(v) => set({ primary_key: v })} /></Field>}
              {(c.model_role === "fact" || c.model_role === "dimension") && (
                <Field label="Relationships" error={errors.relationships} hint="Foreign key column → Gold dimension. A value without a match becomes -1 (Unknown).">
                  <ListEditor disabled={d} items={(c.relationships as { column: string; dimension: string }[]) || []} empty={{ column: "", dimension: "" }} addLabel="Add a relationship" onChange={(v) => set({ relationships: v })}
                    render={(r, up) => <div className="grid grid-cols-2 gap-1.5"><input className={small} placeholder="customer_key" disabled={d} value={r.column} onChange={(e) => up({ column: e.target.value })} /><input className={small} placeholder="dim_customer" disabled={d} value={r.dimension} onChange={(e) => up({ dimension: e.target.value })} /></div>} />
                </Field>
              )}
            </>
          )}
          <Field label="Description"><input className="orbit-input" disabled={d} value={String(c.description || "")} onChange={(e) => set({ description: e.target.value })} /></Field>
        </>
      );
    default:
      return <ExtraConfig data={data} upstream={upstream} datasets={datasets} readOnly={readOnly} errors={errors} onChange={onChange} />;
  }
}
