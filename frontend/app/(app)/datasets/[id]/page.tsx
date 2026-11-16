"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { SchemaTable } from "@/components/data/SchemaTable";
import { Badge, Card, CardHeader, DataTable, ErrorState, LoadingState, PageContainer, cn } from "@/components/ui";
import { bytes, compact, dateTime } from "@/lib/format";
import type { DatasetDetail, Preview } from "@/lib/types";

function changes(c: DatasetDetail["versions"][number]["schema_changes"]): string {
  const parts = [];
  if (c.added?.length) parts.push(`+${c.added.join(", ")}`);
  if (c.removed?.length) parts.push(`−${c.removed.join(", ")}`);
  if (c.changed?.length) parts.push(c.changed.map((x) => `${x.column}: ${x.from}→${x.to}`).join(", "));
  return parts.join(" · ") || "no schema change";
}

export default function DatasetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: ds, error, mutate } = useSWR<DatasetDetail>(`/datasets/${id}`);
  const [version, setVersion] = useState<number | null>(null);
  const v = version ?? ds?.current_version;
  const { data: preview, error: pErr } = useSWR<Preview>(ds ? `/datasets/${id}/preview?version=${v}` : null);

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!ds) return <LoadingState />;

  return (
    <PageContainer>
      <Link href="/datasets" className="inline-flex items-center gap-1 text-xs text-orbit-muted hover:text-white">
        <ArrowLeft className="w-3.5 h-3.5" /> Bronze catalog
      </Link>
      <Card>
        <div className="flex items-center gap-2">
          <h2 className="text-base font-bold text-white font-mono">{ds.qualified_name}</h2>
          <Badge tone="amber">Bronze</Badge>
          <Badge>v{ds.current_version}</Badge>
        </div>
        {ds.description && <p className="text-xs text-orbit-muted mt-1">{ds.description}</p>}
        <div className="text-xs text-orbit-muted mt-2">
          {compact(ds.row_count)} rows · {bytes(ds.size_bytes)} Parquet · updated {dateTime(ds.last_updated_at)}
        </div>
      </Card>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader title="Schema" subtitle="Greyed rows are technical columns added at ingestion" />
          <SchemaTable schema={ds.schema} />
        </Card>
        <Card>
          <CardHeader title="Versions" subtitle="Each load creates a new, immutable version" />
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
                <div className="text-[10px] text-orbit-subtle font-mono mt-0.5 truncate" title={x.location}>{x.location}</div>
              </button>
            ))}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title={`Data preview — version ${v}`}
          subtitle={preview ? `${preview.rows.length} of ${compact(preview.total_rows)} rows, read from ${preview.location}` : undefined} />
        {pErr ? <ErrorState error={pErr} /> : !preview ? <LoadingState /> : (
          <DataTable columns={preview.columns} rows={preview.rows} maxHeight="460px" />
        )}
      </Card>
    </PageContainer>
  );
}
