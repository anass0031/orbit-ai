"use client";

import { AlertTriangle, ArrowRight, Boxes, CheckCircle2, Eye, GitBranch, KeyRound, Play, RefreshCw, Sparkles, Table2 } from "lucide-react";
import Link from "next/link";
import React, { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState, ErrorState, InlineAlert, LoadingState, PageContainer, Segmented, cn } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { compact } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { GoldAnalysis, GoldDimension, GoldFact, GoldGenerated, GoldModel, GoldPreview } from "@/lib/types";

const ROLE_TONE = { fact: "purple", dimension: "cyan", bridge: "amber", standalone: "gray" } as const;

interface Choice {
  facts: string[];
  dimensions: string[];
  measures: Record<string, string[]>;
  attributes: Record<string, string[]>;
  labels: Record<string, string>;
  marts: boolean;
}

const initial = (m: GoldModel): Choice => ({
  facts: m.facts.map((f) => f.name), dimensions: m.dimensions.map((d) => d.name),
  measures: Object.fromEntries(m.facts.map((f) => [f.name, f.measures.map((x) => x.column)])),
  attributes: Object.fromEntries(m.dimensions.map((d) => [d.name, d.attributes])),
  labels: Object.fromEntries(m.dimensions.map((d) => [d.name, d.label])), marts: true,
});

const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

function Check({ on, onChange, children, disabled }: { on: boolean; onChange: () => void; children: React.ReactNode; disabled?: boolean }) {
  return (
    <label className={cn("flex items-center gap-2 text-xs py-0.5", disabled ? "text-orbit-subtle" : "text-orbit-text cursor-pointer")}>
      <input type="checkbox" checked={on} onChange={onChange} disabled={disabled} />{children}
    </label>
  );
}

/** Schema drawn from the selection: facts in the middle, dimensions around them, one line per join. */
function SchemaDiagram({ model, choice }: { model: GoldModel; choice: Choice }) {
  const facts = model.facts.filter((f) => choice.facts.includes(f.name));
  const dims = model.dimensions.filter((d) => choice.dimensions.includes(d.name));
  const rowH = 46;
  const height = Math.max(facts.length, dims.length, 2) * rowH + 16;
  const y = (i: number, n: number) => 8 + (height - 16) * (i + 0.5) / Math.max(n, 1);
  const links = facts.flatMap((f, i) => f.links.filter((l) => choice.dimensions.includes(l.dimension)).map((l) => ({ key: `${f.name}-${l.key}`, from: y(i, facts.length), to: y(dims.findIndex((d) => d.name === l.dimension), dims.length) })));
  const parents = dims.flatMap((d, i) => d.parents.filter((p) => choice.dimensions.includes(p.dimension)).map((p) => ({ key: `${d.name}-${p.key}`, a: y(i, dims.length), b: y(dims.findIndex((x) => x.name === p.dimension), dims.length) })));
  const Box = ({ x, cy, w, name, tone }: { x: number; cy: number; w: number; name: string; tone: "fact" | "dim" }) => (
    <g>
      <rect x={x} y={cy - 16} width={w} height={32} rx={6} fill={tone === "fact" ? "rgba(121,92,255,.18)" : "rgba(0,212,255,.10)"} stroke={tone === "fact" ? "#795CFF" : "#22A5C3"} />
      <text x={x + w / 2} y={cy + 4} textAnchor="middle" fontSize="11" fontFamily="monospace" fill="#F1F5F9">{name.length > 25 ? `${name.slice(0, 24)}…` : name}</text>
    </g>
  );
  return (
    <div className="overflow-x-auto rounded-lg bg-orbit-bg border border-orbit-border p-2">
      <svg viewBox={`0 0 760 ${height}`} className="w-full min-w-[640px]" role="img" aria-label={`${model.schema_type} schema with ${facts.length} facts and ${dims.length} dimensions`}>
        {links.map((l) => <path key={l.key} d={`M 230 ${l.from} C 340 ${l.from}, 400 ${l.to}, 510 ${l.to}`} fill="none" stroke="#475569" strokeWidth="1.5" />)}
        {parents.map((p) => <path key={p.key} d={`M 710 ${p.a} C 760 ${p.a}, 760 ${p.b}, 710 ${p.b}`} fill="none" stroke="#22A5C3" strokeWidth="1.5" strokeDasharray="4 3" />)}
        {facts.map((f, i) => <Box key={f.name} x={20} cy={y(i, facts.length)} w={210} name={f.name} tone="fact" />)}
        {dims.map((d, i) => <Box key={d.name} x={510} cy={y(i, dims.length)} w={200} name={d.name} tone="dim" />)}
      </svg>
    </div>
  );
}

function FactCard({ f, choice, setChoice }: { f: GoldFact; choice: Choice; setChoice: (c: Choice) => void }) {
  const on = choice.facts.includes(f.name);
  const picked = choice.measures[f.name] ?? [];
  return (
    <div className={cn("p-3 rounded-lg border", on ? "border-orbit-purple/50 bg-orbit-purple/5" : "border-orbit-border")}>
      <Check on={on} onChange={() => setChoice({ ...choice, facts: toggle(choice.facts, f.name) })}><span className="font-mono text-white">{f.name}</span>{f.type === "bridge" && <Badge tone="amber">bridge</Badge>}</Check>
      <div className="text-[11px] text-orbit-muted mt-1">{f.grain} · {compact(f.rows)} rows · from <span className="font-mono">{f.source}</span></div>
      {f.measures.length > 0 && <div className="mt-2"><div className="orbit-label mb-0.5">Measures</div>
        {f.measures.map((m) => <Check key={m.column} disabled={!on} on={picked.includes(m.column)} onChange={() => setChoice({ ...choice, measures: { ...choice.measures, [f.name]: toggle(picked, m.column) } })}><span className="font-mono">{m.column}</span><span className="text-orbit-subtle">{m.aggregation}</span></Check>)}</div>}
      <div className="mt-2"><div className="orbit-label mb-0.5">Joins</div>
        {f.links.map((l) => <div key={l.key} className={cn("text-[11px] font-mono truncate", choice.dimensions.includes(l.dimension) ? "text-orbit-text" : "text-orbit-subtle line-through")} title={l.join}>{l.join}</div>)}</div>
    </div>
  );
}

function DimensionCard({ d, choice, setChoice }: { d: GoldDimension; choice: Choice; setChoice: (c: Choice) => void }) {
  const on = choice.dimensions.includes(d.name);
  const picked = choice.attributes[d.name] ?? [];
  const names = [d.natural_key, ...d.attributes];
  return (
    <div className={cn("p-3 rounded-lg border", on ? "border-orbit-cyan/40 bg-orbit-cyan/5" : "border-orbit-border")}>
      <Check on={on} onChange={() => setChoice({ ...choice, dimensions: toggle(choice.dimensions, d.name) })}><span className="font-mono text-white">{d.name}</span><Badge>{d.kind}</Badge></Check>
      <div className="text-[11px] text-orbit-muted mt-1">Used by {d.used_by.join(", ") || "another dimension"}</div>
      <label className="block mt-2 text-[11px] text-orbit-muted">Name shown in charts
        <select className="orbit-input !py-1 !text-xs mt-0.5" disabled={!on} value={choice.labels[d.name]} onChange={(e) => setChoice({ ...choice, labels: { ...choice.labels, [d.name]: e.target.value } })}>
          {names.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      {d.kind !== "calendar" && d.attributes.length > 0 && <div className="mt-2"><div className="orbit-label mb-0.5">Attributes</div>
        {d.attributes.map((a) => <Check key={a} disabled={!on || a === choice.labels[d.name]} on={picked.includes(a) || a === choice.labels[d.name]} onChange={() => setChoice({ ...choice, attributes: { ...choice.attributes, [d.name]: toggle(picked, a) } })}><span className="font-mono">{a}</span></Check>)}</div>}
    </div>
  );
}

function ModelBuilder({ model, onDone }: { model: GoldModel; onDone: () => void }) {
  const { can } = useAuth();
  const notify = useToast();
  const [choice, setChoice] = useState<Choice>(() => initial(model));
  const [preview, setPreview] = useState<GoldPreview | null>(null);
  const [done, setDone] = useState<GoldGenerated | null>(null);
  const [busy, setBusy] = useState<"preview" | "generate" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setChoice(initial(model)); setPreview(null); setDone(null); setErr(null); }, [model]);
  const allowed = can("pipelines.write");
  const body = { model_id: model.id, selection: choice };
  const call = async (kind: "preview" | "generate") => {
    setBusy(kind);
    setErr(null);
    try {
      if (kind === "preview") setPreview(await api<GoldPreview>("/gold-models/preview", { method: "POST", body }));
      else {
        const out = await api<GoldGenerated>("/gold-models/generate", { method: "POST", body });
        setDone(out);
        notify(out.run?.status === "failed" ? "The model was created but its run failed" : "Gold model generation started");
        onDone();
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Boxes className="w-4 h-4 text-orbit-cyan" />{model.name}</span>}
        subtitle={`${model.summary}${model.constellation ? " · facts share dimensions" : ""}`}
        right={<Badge tone="cyan">{model.schema_type}</Badge>} />
      {model.warnings.map((w) => <InlineAlert key={w} className="mb-3">{w}</InlineAlert>)}
      <SchemaDiagram model={model} choice={choice} />
      <div className="grid lg:grid-cols-2 gap-4 mt-4">
        <div className="space-y-2"><div className="orbit-label">Fact tables</div>{model.facts.map((f) => <FactCard key={f.name} f={f} choice={choice} setChoice={setChoice} />)}</div>
        <div className="space-y-2"><div className="orbit-label">Dimension tables</div>{model.dimensions.map((d) => <DimensionCard key={d.name} d={d} choice={choice} setChoice={setChoice} />)}</div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 mt-4 pt-4 border-t border-orbit-border">
        <Check on={choice.marts} onChange={() => setChoice({ ...choice, marts: !choice.marts })}>Add KPI tables by month and main dimension</Check>
        {allowed ? <div className="flex gap-2">
          <Button icon={Eye} loading={busy === "preview"} disabled={!choice.facts.length} onClick={() => call("preview")}>Preview tables</Button>
          <Button variant="primary" icon={Play} loading={busy === "generate"} disabled={!choice.facts.length} onClick={() => call("generate")}>Generate Gold model</Button>
        </div> : <span className="text-[11px] text-orbit-muted">Generating a model is reserved to Data Engineers and Administrators.</span>}
      </div>
      {err && <InlineAlert tone="red" className="mt-3">{err}</InlineAlert>}
      {done && (
        <InlineAlert tone={done.run?.status === "failed" ? "red" : "green"} className="mt-3">
          <div className="flex flex-wrap items-center gap-3">
            <span>{done.run?.status === "failed" ? `Run failed: ${done.run.error}` : `${done.tables.length} Gold tables are being written (${done.run?.status ?? "created"}).`}</span>
            <Link className="underline" href={`/pipelines/${done.pipeline_id}`}>Open the pipeline</Link>
            {done.run && <Link className="underline" href={`/runs/${done.run.id}`}>Follow the run</Link>}
            <Link className="underline" href="/datasets">Open the catalog</Link>
          </div>
        </InlineAlert>
      )}
      {preview && (
        <div className="mt-4 space-y-3">
          {preview.issues.filter((i) => i.severity === "error").map((i) => <InlineAlert key={i.message} tone="red">{i.message}</InlineAlert>)}
          {preview.tables.map((t) => (
            <div key={t.name}>
              <div className="flex flex-wrap items-center gap-2 mb-1"><span className="font-mono text-xs text-white">gold.{t.name}</span>{t.role && <Badge tone={t.role === "fact" ? "purple" : "cyan"}>{t.role}</Badge>}
                {t.primary_key.length > 0 && <span className="text-[11px] text-orbit-muted inline-flex items-center gap-1"><KeyRound className="w-3 h-3" />{t.primary_key.join(" + ")}</span>}</div>
              {t.error ? <InlineAlert tone="red">{t.error}</InlineAlert> : <DataTable columns={t.columns.map((c) => c.name)} rows={t.sample} maxHeight="170px" />}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function GoldModelsPage() {
  const { data, error, mutate, isValidating } = useSWR<GoldAnalysis>("/gold-models/analysis", { revalidateOnFocus: false });
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<"models" | "silver" | "relations">("models");
  const model = useMemo(() => data?.models.find((m) => m.id === selected) ?? data?.models[0], [data, selected]);
  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!data) return <PageContainer><LoadingState label="Analysing every Silver table…" /></PageContainer>;
  const signals = data.conflicts.length + data.same_entities.length;
  return (
    <PageContainer>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented value={tab} onChange={setTab} options={[{ value: "models", label: `Proposed models (${data.models.length})` }, { value: "silver", label: `Silver tables (${data.silver_tables.length})` }, { value: "relations", label: `Relationships (${data.relationships.length})` }]} />
        <Button icon={RefreshCw} loading={isValidating} onClick={() => mutate()}>Analyse again</Button>
      </div>
      {signals > 0 && <InlineAlert><span className="inline-flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5" />{signals} possible data conflict(s) between Silver tables. See Relationships.</span></InlineAlert>}

      {tab === "models" && (data.models.length === 0 ? (
        <Card><EmptyState icon={Sparkles} title="No dimensional model found yet" description="Silver needs at least one table with measures or events. Tables without them are listed under Silver tables." action={<Link href="/pipelines"><Button variant="primary" icon={GitBranch}>Open ETL Pipelines</Button></Link>} /></Card>
      ) : (
        <div className="grid xl:grid-cols-[280px_1fr] gap-4 items-start">
          <div className="space-y-2">
            {data.models.map((m) => (
              <button key={m.id} onClick={() => setSelected(m.id)} className={cn("w-full text-left p-3 rounded-lg border transition", m.id === model?.id ? "border-orbit-purple/60 bg-orbit-purple/10" : "border-orbit-border hover:bg-white/[0.03]")}>
                <div className="flex items-center justify-between gap-2"><span className="text-xs font-semibold text-white truncate">{m.name}</span><Badge tone="cyan">{m.schema_type}</Badge></div>
                <div className="text-[11px] text-orbit-muted mt-1">{m.summary}</div>
              </button>
            ))}
          </div>
          {model && <ModelBuilder model={model} onDone={() => mutate()} />}
        </div>
      ))}

      {tab === "silver" && (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.silver_tables.map((t) => (
            <Card key={t.id}>
              <div className="flex items-center justify-between gap-2"><Link href={`/datasets/${t.id}`} className="font-mono text-sm text-white hover:text-orbit-purpleLight truncate">{t.qualified_name}</Link><Badge tone={ROLE_TONE[t.role]}>{t.role}</Badge></div>
              <p className="text-[11px] text-orbit-muted mt-2">{t.reason}</p>
              <dl className="text-[11px] mt-3 space-y-1">
                <div className="flex gap-2"><dt className="text-orbit-subtle w-16 shrink-0">Grain</dt><dd className="text-orbit-text">{t.grain}</dd></div>
                <div className="flex gap-2"><dt className="text-orbit-subtle w-16 shrink-0">Rows</dt><dd className="text-orbit-text">{compact(t.rows)}</dd></div>
                {t.measures.length > 0 && <div className="flex gap-2"><dt className="text-orbit-subtle w-16 shrink-0">Measures</dt><dd className="font-mono text-orbit-text break-all">{t.measures.join(", ")}</dd></div>}
                {t.dates.length > 0 && <div className="flex gap-2"><dt className="text-orbit-subtle w-16 shrink-0">Dates</dt><dd className="font-mono text-orbit-text break-all">{t.dates.join(", ")}</dd></div>}
              </dl>
            </Card>
          ))}
        </div>
      )}

      {tab === "relations" && (
        <div className="space-y-4">
          <Card>
            <CardHeader title={<span className="flex items-center gap-2"><Table2 className="w-4 h-4 text-orbit-purple" />Relationships found in the data</span>} subtitle="Measured from the values of each column" />
            {data.relationships.length === 0 ? <EmptyState title="No relationship between Silver tables" /> : (
              <div className="overflow-x-auto"><table className="orbit-table w-full">
                <thead><tr><th>Join</th><th>Type</th><th className="text-right">Values matched</th><th className="text-right">Rows without match</th></tr></thead>
                <tbody>{data.relationships.map((r) => <tr key={r.join}><td className="font-mono">{r.join}</td><td><Badge>{r.cardinality}</Badge></td><td className="text-right font-mono">{r.coverage}%</td><td className="text-right font-mono">{r.orphan_rows.toLocaleString()}</td></tr>)}</tbody>
              </table></div>
            )}
          </Card>
          {signals > 0 && <Card>
            <CardHeader title="Data conflicts to review" />
            <ul className="space-y-1.5 text-xs text-orbit-text">
              {data.conflicts.map((c) => <li key={c.tables.join() + c.column} className="flex items-center gap-2"><AlertTriangle className="w-3.5 h-3.5 text-orbit-amber shrink-0" />{c.tables.join(" and ")} disagree on <span className="font-mono">{c.column}</span> for {c.conflicting_rows} record(s).</li>)}
              {data.same_entities.map((c) => <li key={c.tables.join()} className="flex items-center gap-2"><ArrowRight className="w-3.5 h-3.5 text-orbit-amber shrink-0" />{c.message}</li>)}
            </ul>
          </Card>}
          {data.functional_dependencies.length > 0 && <Card>
            <CardHeader title={<span className="flex items-center gap-2"><CheckCircle2 className="w-4 h-4 text-orbit-green" />Hierarchies inside tables</span>} subtitle="Each value on the left always has one value on the right" />
            <div className="flex flex-wrap gap-2">{data.functional_dependencies.map((f) => <Badge key={f.table + f.determinant + f.dependent}>{f.table}: {f.determinant} → {f.dependent}</Badge>)}</div>
          </Card>}
        </div>
      )}
    </PageContainer>
  );
}
