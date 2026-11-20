"use client";

import { ArrowLeft, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { ScoreRing } from "@/components/data/RunParts";
import { BusinessCharts } from "@/components/data/BusinessCharts";
import { DiffPanel, DocsPanel, ProfilePanel } from "@/components/data/Explore";
import { BronzeToSilverButton, LineagePanel, ModelPanel } from "@/components/data/Medallion";
import { SchemaTable } from "@/components/data/SchemaTable";
import { Badge, Button, Card, CardHeader, DataTable, ErrorState, LoadingState, PageContainer, Segmented, cn } from "@/components/ui";
import { bytes, compact, dateTime } from "@/lib/format";
import type { DatasetDetail, Preview, QualityRule } from "@/lib/types";

const LAYER_TONE = { bronze: "amber", silver: "gray", gold: "cyan" } as const;

function changes(c: DatasetDetail["versions"][number]["schema_changes"]): string {
  const parts = [];
  if (c.added?.length) parts.push(`+${c.added.join(", ")}`);
  if (c.removed?.length) parts.push(`−${c.removed.join(", ")}`);
  if (c.changed?.length) parts.push(c.changed.map((x) => `${x.column}: ${x.from}→${x.to}`).join(", "));
  return parts.join(" · ") || "no schema change";
}

export default function DatasetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<"overview" | "charts" | "profile" | "diff" | "docs" | "lineage" | "model">("overview");
  const [technical, setTechnical] = useState(false);
  const { data: ds, error, mutate } = useSWR<DatasetDetail>(`/datasets/${id}`);
  const { data: rules } = useSWR<QualityRule[]>(`/quality/rules?dataset_id=${id}`);
  const [version, setVersion] = useState<number | null>(null);
  const v = version ?? ds?.current_version;
  const modeled = ds?.layer === "gold" && !!ds.model_role;
  const { data: preview, error: pErr } = useSWR<Preview>(ds ? `/datasets/${id}/preview?version=${v}${modeled && !technical ? "&view=business" : ""}` : null);

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!ds) return <LoadingState />;

  return (
    <PageContainer>
      <Link href="/datasets" className="inline-flex items-center gap-1 text-xs text-orbit-muted hover:text-white">
        <ArrowLeft className="w-3.5 h-3.5" /> Lakehouse catalog
      </Link>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-white font-mono">{ds.qualified_name}</h2>
              <Badge tone={LAYER_TONE[ds.layer]}>{ds.layer}</Badge>
              <Badge>v{ds.current_version}</Badge>
              {ds.model_role && <Badge tone="cyan">{ds.model_role}</Badge>}
              <BronzeToSilverButton ds={ds} />
            </div>
            {ds.description && <p className="text-xs text-orbit-muted mt-1">{ds.description}</p>}
            {(ds.tags?.length ?? 0) > 0 && <div className="flex gap-1 mt-1">{ds.tags!.map((t) => <Badge key={t} tone="purple" className="text-[10px]">#{t}</Badge>)}</div>}
            <div className="text-xs text-orbit-muted mt-2">
              {compact(ds.row_count)} rows · {bytes(ds.size_bytes)} Parquet · updated {dateTime(ds.last_updated_at)}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <ScoreRing score={ds.quality_score} size={64} label="quality" />
            <div className="text-xs text-orbit-muted">
              {rules ? `${rules.filter((r) => r.enabled).length} active rule(s)` : "…"}
              <div className="mt-1.5">
                <Link href={`/quality?tab=rules&dataset=${ds.id}`}><Button size="sm" icon={ShieldCheck}>Quality rules</Button></Link>
              </div>
            </div>
          </div>
        </div>
      </Card>

      <Segmented value={tab} onChange={setTab} options={[{ value: "overview", label: "Overview" }, { value: "profile", label: "Profile" },
        { value: "diff", label: "Compare versions" }, { value: "docs", label: "Documentation" }, { value: "lineage", label: "Lineage" },
        ...(ds.layer === "gold" ? [{ value: "charts" as const, label: "Charts" }, { value: "model" as const, label: "Data model" }] : [])]} />
      {tab === "lineage" && <LineagePanel ds={ds} version={v!} />}
      {tab === "model" && <ModelPanel ds={ds} />}
      {tab === "charts" && <BusinessCharts ds={ds} version={v!} />}
      {tab === "profile" && <ProfilePanel ds={ds} version={v!} />}
      {tab === "diff" && <DiffPanel ds={ds} />}
      {tab === "docs" && <DocsPanel ds={ds} onSaved={() => mutate()} />}

      {tab === "overview" && <>
      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader title="Schema" subtitle={ds.layer === "bronze" ? "Greyed rows are technical columns added at ingestion" : undefined} />
          <SchemaTable schema={ds.schema} />
        </Card>
        <Card>
          <CardHeader title="Versions" subtitle="Each write creates a new, immutable version" />
          <div className="space-y-2">
            {ds.versions.map((x) => (
              <button key={x.id} onClick={() => setVersion(x.version)}
                className={cn("w-full text-left p-2.5 rounded-lg border transition",
                  x.version === v ? "border-orbit-purple/50 bg-orbit-purple/10" : "border-orbit-border hover:bg-white/[0.03]")}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-white font-semibold">v{x.version}</span>
                  <span className="text-orbit-muted">{dateTime(x.created_at)}</span>
                </div>
                <div className="text-[11px] text-orbit-muted mt-0.5">{compact(x.row_count)} rows · {bytes(x.size_bytes)}</div>
                <div className="text-[11px] text-orbit-subtle mt-0.5 truncate" title={changes(x.schema_changes)}>{changes(x.schema_changes)}</div>
                <div className="text-[11px] mt-0.5">
                  {x.pipeline_run_id ? <Link href={`/runs/${x.pipeline_run_id}`} className="text-orbit-purpleLight hover:underline" onClick={(e) => e.stopPropagation()}>written by a pipeline run</Link>
                    : x.source_import_id ? <Link href={`/imports/${x.source_import_id}`} className="text-orbit-purpleLight hover:underline" onClick={(e) => e.stopPropagation()}>loaded from a file import</Link> : null}
                </div>
              </button>
            ))}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title={`Data preview — version ${v}`}
          subtitle={preview ? `${preview.rows.length} of ${compact(preview.total_rows)} rows, read from ${preview.location}` : undefined}
          right={modeled ? <Segmented value={technical ? "keys" : "names"} onChange={(x) => setTechnical(x === "keys")} options={[{ value: "names", label: "Business names" }, { value: "keys", label: "Technical keys" }]} /> : undefined} />
        {pErr ? <ErrorState error={pErr} /> : !preview ? <LoadingState /> : <DataTable columns={preview.columns} rows={preview.rows} maxHeight="460px" />}
      </Card>
      </>}
    </PageContainer>
  );
}
