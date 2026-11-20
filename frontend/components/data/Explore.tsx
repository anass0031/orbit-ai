"use client";
// Dataset exploration for analysts: column profile, version comparison, documentation.
import { Save } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import useSWR from "swr";
import { Badge, Button, Card, CardHeader, DataTable, ErrorState, Field, InlineAlert, LoadingState, cn } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { compact } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { ColumnProfile, DatasetDetail, DatasetDiff, DatasetProfile } from "@/lib/types";

const fmt = (v: unknown) => (typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 3 }) : v == null ? "—" : String(v));

function Mini({ data, x, y }: { data: Record<string, unknown>[]; x: string; y: string }) {
  return (
    <ResponsiveContainer width="100%" height={70}>
      <BarChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
        <XAxis dataKey={x} hide />
        <Tooltip contentStyle={{ background: "#151F32", border: "1px solid #1E293B", borderRadius: 8, fontSize: 11 }} labelStyle={{ color: "#94A3B8" }} />
        <Bar dataKey={y} fill="#795CFF" radius={[2, 2, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function ColumnCard({ c, rows }: { c: ColumnProfile; rows: number }) {
  return (
    <div className="p-3 rounded-lg bg-orbit-bg border border-orbit-border">
      <div className="flex items-center gap-2"><span className="font-mono text-xs text-white truncate">{c.display_name ?? c.name}</span><Badge className="text-[10px]">{c.role === "foreign_key" ? "name" : c.role === "primary_key" ? "key" : c.role === "identifier" ? "identifier" : c.type}</Badge>
        {c.unique && <Badge tone="cyan" className="text-[10px]">unique</Badge>}</div>
      {c.description && <div className="text-[11px] text-orbit-muted mt-1">{c.description}</div>}
      {c.dimension && <div className="text-[11px] text-orbit-muted mt-1 truncate">{c.label_column} from {c.dimension}</div>}
      <div className="grid grid-cols-3 gap-1 mt-2 text-[11px]">
        <div><div className="text-orbit-subtle">empty</div><div className={cn("font-mono", c.null_pct > 20 ? "text-orbit-amber" : "text-white")}>{c.null_pct}%</div></div>
        <div><div className="text-orbit-subtle">distinct</div><div className="font-mono text-white">{compact(c.distinct)}</div></div>
        {c.mean != null ? <div><div className="text-orbit-subtle">mean</div><div className="font-mono text-white">{fmt(c.mean)}</div></div>
          : c.avg_length != null ? <div><div className="text-orbit-subtle">avg length</div><div className="font-mono text-white">{fmt(c.avg_length)}</div></div> : <div />}
        {c.min !== undefined && <div className="col-span-3 text-orbit-muted font-mono truncate">min {fmt(c.min)} · max {fmt(c.max)}{c.median != null ? ` · median ${fmt(c.median)}` : ""}</div>}
      </div>
      {c.histogram && <Mini data={c.histogram.map((h) => ({ ...h, label: `${fmt(h.from)} – ${fmt(h.to)}` }))} x="label" y="count" />}
      {c.by_month && <Mini data={c.by_month} x="month" y="count" />}
      {c.top_values && (
        <div className="mt-2 space-y-0.5">{c.top_values.slice(0, 6).map((t) => (
          <div key={t.value} className="flex items-center gap-2 text-[11px]">
            <span className="truncate text-orbit-text w-24">{t.value}</span>
            <div className="flex-1 h-1.5 bg-orbit-card rounded"><div className="h-1.5 bg-orbit-purple rounded" style={{ width: `${Math.max(2, t.pct)}%` }} /></div>
            <span className="font-mono text-orbit-muted w-12 text-right">{t.pct}%</span>
          </div>
        ))}</div>
      )}
      {rows === 0 && <div className="text-[11px] text-orbit-subtle">empty dataset</div>}
    </div>
  );
}

export function ProfilePanel({ ds, version }: { ds: DatasetDetail; version: number }) {
  const { data, error } = useSWR<DatasetProfile>(`/datasets/${ds.id}/profile?version=${version}`);
  if (error) return <ErrorState error={error} />;
  if (!data) return <LoadingState label="Profiling every column…" />;
  return (
    <Card>
      <CardHeader title={`Column profile — version ${data.version}`} subtitle={`${data.rows.toLocaleString()} rows · computed by DuckDB on the Parquet file`} />
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">{data.columns.map((c) => <ColumnCard key={c.name} c={c} rows={data.rows} />)}</div>
    </Card>
  );
}

export function DiffPanel({ ds }: { ds: DatasetDetail }) {
  const versions = ds.versions.map((v) => v.version).sort((a, b) => a - b);
  const [from, setFrom] = useState(versions.length > 1 ? versions[versions.length - 2] : versions[0]);
  const [to, setTo] = useState(versions[versions.length - 1]);
  const [keys, setKeys] = useState<string[]>([]);
  const [res, setRes] = useState<DatasetDiff | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api<DatasetDiff>(`/datasets/${ds.id}/diff?from=${from}&to=${to}${keys.length ? `&keys=${keys.join(",")}` : ""}`));
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  if (versions.length < 2) return <InlineAlert tone="gray">Only one version: nothing to compare yet.</InlineAlert>;
  return (
    <Card>
      <CardHeader title="Compare two versions" subtitle="Schema changes and rows added, removed or changed (choose key columns to detect changed rows)" />
      <div className="flex flex-wrap items-end gap-3">
        <Field label="From"><select className="orbit-input w-24" value={from} onChange={(e) => setFrom(Number(e.target.value))}>{versions.map((v) => <option key={v} value={v}>v{v}</option>)}</select></Field>
        <Field label="To"><select className="orbit-input w-24" value={to} onChange={(e) => setTo(Number(e.target.value))}>{versions.map((v) => <option key={v} value={v}>v{v}</option>)}</select></Field>
        <Field label="Key columns (optional)" className="flex-1 min-w-[240px]">
          <div className="flex flex-wrap gap-1">{ds.schema.filter((c) => !c.metadata).map((c) => (
            <button key={c.name} onClick={() => setKeys((k) => (k.includes(c.name) ? k.filter((x) => x !== c.name) : [...k, c.name]))}
              className={cn("px-2 py-0.5 rounded text-[11px] font-mono border", keys.includes(c.name) ? "bg-orbit-purple/20 border-orbit-purple/40 text-white" : "border-orbit-border text-orbit-muted")}>{c.name}</button>
          ))}</div>
        </Field>
        <Button variant="primary" loading={busy} onClick={run}>Compare</Button>
      </div>
      {err ? <ErrorState error={err} className="mt-3" /> : res && (
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-center">
            {[["rows", `${compact(res.rows_from)} → ${compact(res.rows_to)}`, "text-white"], ["added", res.counts?.added, "text-orbit-green"], ["removed", res.counts?.removed, "text-orbit-redLight"],
              ["changed", res.counts?.changed ?? "—", "text-orbit-amber"], ["unchanged", res.counts?.unchanged, "text-orbit-muted"]].map(([k, v, cls]) => (
              <div key={k as string} className="p-2 rounded-lg bg-orbit-bg border border-orbit-border"><div className="orbit-label">{k}</div><div className={cn("text-lg font-bold", cls as string)}>{typeof v === "number" ? v.toLocaleString() : v}</div></div>
            ))}
          </div>
          {(res.schema.added?.length || res.schema.removed?.length || res.schema.changed?.length) ? (
            <div className="text-xs space-x-2">{res.schema.added?.map((c) => <Badge key={c} tone="green">+ {c}</Badge>)}{res.schema.removed?.map((c) => <Badge key={c} tone="red">− {c}</Badge>)}
              {res.schema.changed?.map((c) => <Badge key={c.column} tone="amber">{c.column}: {c.from} → {c.to}</Badge>)}</div>
          ) : <div className="text-[11px] text-orbit-muted">Same schema.</div>}
          {res.changed_columns && <div className="text-[11px] text-orbit-muted">Changed columns: {Object.entries(res.changed_columns).map(([c, n]) => `${c} (${n})`).join(", ")}</div>}
          {res.note && <div className="text-[11px] text-orbit-subtle">{res.note}</div>}
          {(["added", "removed", "changed"] as const).map((k) => res.samples?.[k]?.length ? (
            <div key={k}><div className="orbit-label mb-1">{k} — first rows</div>
              <DataTable columns={Object.keys(res.samples[k]![0])} rows={res.samples[k]!} maxHeight="200px" /></div>
          ) : null)}
        </div>
      )}
    </Card>
  );
}

export function DocsPanel({ ds, onSaved }: { ds: DatasetDetail; onSaved: () => void }) {
  const { can } = useAuth();
  const notify = useToast();
  const edit = can("catalog.document");
  const [desc, setDesc] = useState(ds.description || "");
  const [tags, setTags] = useState((ds.tags || []).join(", "));
  const [cols, setCols] = useState<Record<string, string>>(ds.column_docs || {});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setDesc(ds.description || ""); setTags((ds.tags || []).join(", ")); setCols(ds.column_docs || {}); }, [ds]);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api(`/datasets/${ds.id}/documentation`, { method: "PUT", body: { description: desc, tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        columns: Object.fromEntries(ds.schema.filter((c) => !c.metadata).map((c) => [c.name, cols[c.name] || ""])) } });
      notify("Documentation saved — the catalogue search uses it");
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card>
      <CardHeader title="Documentation" subtitle="What the dataset and each column mean. Used by the catalogue search."
        right={edit ? <Button size="sm" variant="primary" icon={Save} loading={busy} onClick={save}>Save</Button> : undefined} />
      {err && <InlineAlert tone="red" className="mb-3">{err}</InlineAlert>}
      <div className="grid md:grid-cols-2 gap-3">
        <Field label="Description"><textarea rows={3} disabled={!edit} className="orbit-input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        <Field label="Tags (comma separated)" hint="e.g. finance, kpi, gdpr"><input disabled={!edit} className="orbit-input" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
      </div>
      <div className="mt-3 space-y-1.5">
        {ds.schema.filter((c) => !c.metadata).map((c) => (
          <div key={c.name} className="grid grid-cols-[200px_90px_1fr] gap-2 items-center">
            <span className="font-mono text-xs text-white truncate">{c.name}</span><span className="text-[11px] text-orbit-subtle">{c.type}</span>
            <input disabled={!edit} className="orbit-input !py-1 !text-xs" placeholder="what this column means, its unit…" value={cols[c.name] || ""} onChange={(e) => setCols({ ...cols, [c.name]: e.target.value })} />
          </div>
        ))}
      </div>
    </Card>
  );
}
