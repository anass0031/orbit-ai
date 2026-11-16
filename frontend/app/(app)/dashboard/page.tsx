"use client";

import { Database, FileUp, HardDrive, Users } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { Badge, Button, Card, CardHeader, ErrorState, Kpi, LoadingState, PageContainer, StatusPill } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ago, bytes, compact } from "@/lib/format";
import type { Health, ImportSummary, Overview, Page } from "@/lib/types";

const STEPS = [
  { n: 1, title: "Upload a file", text: "CSV, Excel (.xlsx) or JSON / JSON Lines. The original is kept unchanged in MinIO (landing zone)." },
  { n: 2, title: "Review the discovered schema", text: "Column names, types, empty values, likely key, warnings and a sample — before anything is loaded." },
  { n: 3, title: "Load into Bronze", text: "The data is written as a new, immutable Parquet version of a Bronze dataset and added to the catalog." },
];
const COMPONENT_LABELS: Record<string, string> = { database: "PostgreSQL (metadata)", object_storage: "MinIO (lakehouse)" };

export default function DashboardPage() {
  const { me, can } = useAuth();
  const { data: stats, error, mutate } = useSWR<Overview>("/overview");
  const { data: health } = useSWR<Health>("/health", { refreshInterval: 30000 });
  const { data: recent } = useSWR<Page<ImportSummary>>(can("imports.read") ? "/imports?limit=5" : null);

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!stats) return <LoadingState />;

  return (
    <PageContainer>
      <div className="text-sm text-orbit-muted">
        Welcome, <span className="text-white font-semibold">{me?.user.full_name}</span> — signed in as <Badge tone="purple">{me?.role_label}</Badge>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Kpi label="Files imported" value={compact(stats.imports_total)} icon={FileUp}
          sub={`${stats.imports_by_status.ingested ?? 0} loaded · ${stats.imports_by_status.failed ?? 0} failed`} />
        <Kpi label="Bronze datasets" value={compact(stats.bronze_datasets)} icon={Database} />
        <Kpi label="Rows in Bronze" value={compact(stats.bronze_rows)} icon={HardDrive} sub={bytes(stats.bronze_bytes) + " of Parquet"} />
        <Kpi label="Active users" value={compact(stats.users_active)} icon={Users} />
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader title="How file import works"
            right={can("imports.write") ? <Link href="/imports"><Button variant="primary" icon={FileUp}>Import a file</Button></Link> : undefined} />
          <ol className="space-y-3">
            {STEPS.map((s) => (
              <li key={s.n} className="flex gap-3">
                <span className="w-6 h-6 shrink-0 rounded-full bg-orbit-purple/20 text-orbit-purpleLight text-xs font-bold flex items-center justify-center">{s.n}</span>
                <div>
                  <div className="text-xs font-semibold text-white">{s.title}</div>
                  <div className="text-xs text-orbit-muted">{s.text}</div>
                </div>
              </li>
            ))}
          </ol>
        </Card>

        <Card>
          <CardHeader title="Platform components" subtitle="Live checks, refreshed every 30 s" />
          <div className="space-y-2.5">
            {health ? Object.entries(health.components).map(([k, c]) => (
              <div key={k} className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs text-white">{COMPONENT_LABELS[k] || k}</div>
                  <div className="text-[11px] text-orbit-subtle truncate" title={c.detail}>{c.detail}</div>
                </div>
                <StatusPill status={c.status} />
              </div>
            )) : <LoadingState className="py-4" />}
          </div>
        </Card>
      </div>

      {recent && recent.items.length > 0 && (
        <Card>
          <CardHeader title="Recent imports" right={<Link href="/imports" className="text-xs text-orbit-purpleLight hover:underline">See all</Link>} />
          <table className="orbit-table w-full">
            <thead><tr><th>File</th><th>Status</th><th className="text-right">Rows</th><th>Uploaded</th></tr></thead>
            <tbody>
              {recent.items.map((i) => (
                <tr key={i.id}>
                  <td><Link href={`/imports/${i.id}`} className="text-white hover:text-orbit-purpleLight">{i.filename}</Link></td>
                  <td><StatusPill status={i.status} /></td>
                  <td className="text-right font-mono">{compact(i.row_count)}</td>
                  <td className="text-orbit-muted">{ago(i.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </PageContainer>
  );
}
