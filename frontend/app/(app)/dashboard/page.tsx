"use client";

import { AlertTriangle, CalendarClock, CheckCircle2, Database, GitBranch, Layers, ShieldCheck } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { ScoreBar, ScoreRing } from "@/components/data/RunParts";
import { RunsChart } from "@/components/data/RunsChart";
import { Badge, Card, CardHeader, ErrorState, Kpi, LoadingState, PageContainer, StatusPill, cn } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ago, bytes, compact, duration, pct } from "@/lib/format";
import type { Dashboard, Health } from "@/lib/types";

const LAYERS = [
  { key: "bronze", label: "Bronze", text: "raw imported files", tone: "text-orbit-amber", bar: "bg-orbit-amber" },
  { key: "silver", label: "Silver", text: "cleaned & conformed", tone: "text-orbit-nav", bar: "bg-orbit-nav" },
  { key: "gold", label: "Gold", text: "business-ready", tone: "text-yellow-300", bar: "bg-yellow-300" },
] as const;
const DIMENSIONS = ["completeness", "validity", "uniqueness", "consistency", "freshness"];
const COMPONENT_LABELS: Record<string, string> = { database: "PostgreSQL", object_storage: "MinIO", job_queue: "Redis / worker queue" };

export default function DashboardPage() {
  const { me } = useAuth();
  const { data: d, error, mutate } = useSWR<Dashboard>("/dashboard", { refreshInterval: 15000 });
  const { data: health } = useSWR<Health>("/health", { refreshInterval: 30000 });

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!d) return <LoadingState />;
  const totalRows = Object.values(d.layers).reduce((s, l) => s + l.rows, 0);
  const att = d.attention;
  const alerts = att.recurring_failures.length + att.stuck_runs.length + att.low_quality_datasets.length;

  return (
    <PageContainer>
      <div className="text-sm text-orbit-muted">
        Welcome, <span className="text-white font-semibold">{me?.user.full_name}</span> — signed in as <Badge tone="purple">{me?.role_label}</Badge>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <Kpi label="Datasets" icon={Database} value={compact(Object.values(d.layers).reduce((s, l) => s + l.datasets, 0))}
          sub={`${compact(totalRows)} rows in the lakehouse`} />
        <Kpi label="Pipelines" icon={GitBranch} value={compact(d.pipelines.total)}
          sub={`${d.pipelines.published} published · ${d.pipelines.failing} failing`} subTone={d.pipelines.failing ? "red" : "muted"} />
        <Kpi label="Run success (7 days)" value={pct(d.runs.success_rate)} subTone={d.runs.success_rate !== null && d.runs.success_rate < 90 ? "red" : "green"}
          sub={`${d.runs.counts.succeeded} ok · ${d.runs.counts.failed} failed · avg ${duration(d.runs.avg_duration_ms)}`} />
        <Kpi label="Quality score" icon={ShieldCheck} value={d.quality.score === null ? "—" : `${d.quality.score}`}
          sub={`${d.quality.datasets_scored} dataset(s) checked`} />
        <Kpi label="Open anomalies" icon={AlertTriangle} value={compact(d.quality.open_issues)} subTone={d.quality.open_by_severity.high ? "red" : "muted"}
          sub={`${d.quality.open_by_severity.high ?? 0} high · ${d.quality.open_by_severity.medium ?? 0} medium · ${d.quality.open_by_severity.low ?? 0} low`} />
      </div>

      {!!d.automation?.automated && (
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><CalendarClock className="w-4 h-4 text-orbit-purple" />Automated pipelines</span>}
            subtitle={`${d.automation.automated} automated · ${d.automation.scheduled} on a schedule`}
            right={<Link href="/calendar" className="text-xs text-orbit-purpleLight hover:underline">Calendar</Link>} />
          <div className="space-y-2">
            {d.automation.next.map((x) => (
              <Link key={x.id} href={`/pipelines/${x.id}`} className="block text-xs hover:bg-white/[0.03] rounded p-1">
                <div className="flex items-center justify-between gap-2"><span className="text-white truncate">{x.name}</span>{x.last_run_status && <StatusPill status={x.last_run_status} />}</div>
                <div className="text-[11px] text-orbit-muted">{x.triggers.join(" · ")}{x.next_run_at ? ` · next ${new Date(x.next_run_at).toISOString().slice(0, 16).replace("T", " ")} UTC` : ""}</div>
              </Link>
            ))}
          </div>
        </Card>
      )}

      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader title="Pipeline runs per day" subtitle="Last 14 days"
            right={<Link href="/runs" className="text-xs text-orbit-purpleLight hover:underline">Run monitoring</Link>} />
          <RunsChart data={d.daily} />
        </Card>
        <Card>
          <CardHeader title="Data quality" right={<Link href="/quality" className="text-xs text-orbit-purpleLight hover:underline">Details</Link>} />
          <div className="flex items-center gap-5">
            <ScoreRing score={d.quality.score} size={84} label="global" />
            <div className="flex-1 space-y-2">
              {DIMENSIONS.map((k) => <ScoreBar key={k} label={k} score={d.quality.dimensions[k]} />)}
            </div>
          </div>
        </Card>
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card>
          <CardHeader title={<span className="flex items-center gap-2"><Layers className="w-4 h-4 text-orbit-purple" />Medallion layers</span>}
            right={<Link href="/datasets" className="text-xs text-orbit-purpleLight hover:underline">Catalog</Link>} />
          <div className="space-y-3">
            {LAYERS.map((l) => {
              const v = d.layers[l.key];
              return (
                <div key={l.key}>
                  <div className="flex items-baseline justify-between text-xs">
                    <span><span className={cn("font-semibold", l.tone)}>{l.label}</span> <span className="text-orbit-subtle">— {l.text}</span></span>
                    <span className="font-mono text-orbit-muted">{v.datasets} ds · {compact(v.rows)} rows · {bytes(v.bytes)}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-orbit-bg mt-1 overflow-hidden">
                    <div className={cn("h-full rounded-full", l.bar)} style={{ width: `${totalRows ? (100 * v.rows) / totalRows : 0}%` }} />
                  </div>
                </div>
              );
            })}
            <div className="text-[11px] text-orbit-subtle pt-1">{d.imports.total} file(s) imported · {d.imports.by_status.ingested ?? 0} loaded into Bronze</div>
          </div>
        </Card>

        <Card>
          <CardHeader title="Needs attention" />
          {alerts === 0 ? (
            <div className="flex items-center gap-2 text-xs text-orbit-green"><CheckCircle2 className="w-4 h-4" />Nothing to report.</div>
          ) : (
            <ul className="space-y-2 text-xs">
              {att.recurring_failures.map((r) => (
                <li key={r.pipeline_id}><Link href={`/runs/${r.last_run_id}`} className="text-white hover:text-orbit-purpleLight">{r.name}</Link>
                  <span className="text-orbit-redLight"> failed {r.streak}× in a row</span></li>
              ))}
              {att.stuck_runs.map((r) => (
                <li key={r.id}><Link href={`/runs/${r.id}`} className="text-white hover:text-orbit-purpleLight">{r.pipeline_name}</Link>
                  <span className="text-orbit-amber"> {r.reason}</span></li>
              ))}
              {att.low_quality_datasets.map((x) => (
                <li key={x.id}><Link href={`/quality?tab=rules&dataset=${x.id}`} className="font-mono text-white hover:text-orbit-purpleLight">{x.qualified_name}</Link>
                  <span className="text-orbit-amber"> quality score {x.quality_score} (&lt; {att.low_score_threshold})</span></li>
              ))}
            </ul>
          )}
          {health && (
            <div className="mt-4 pt-3 border-t border-orbit-border space-y-1.5">
              {Object.entries(health.components).map(([k, c]) => (
                <div key={k} className="flex items-center justify-between text-[11px]" title={c.detail}>
                  <span className="text-orbit-muted">{COMPONENT_LABELS[k] || k}</span><StatusPill status={c.status} />
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title="Latest anomalies" right={<Link href="/quality?tab=issues" className="text-xs text-orbit-purpleLight hover:underline">All</Link>} />
          {d.recent_issues.length === 0 ? <div className="text-xs text-orbit-muted">No open anomaly.</div> : (
            <ul className="space-y-2">
              {d.recent_issues.map((i) => (
                <li key={i.id} className="flex items-start justify-between gap-2 text-xs">
                  <span className="text-white">{i.title}</span>
                  <Badge tone={i.severity === "high" ? "red" : i.severity === "medium" ? "amber" : "gray"}>{i.severity}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader title="Latest runs" right={<Link href="/runs" className="text-xs text-orbit-purpleLight hover:underline">All runs</Link>} />
        {d.recent_runs.length === 0 ? <div className="text-xs text-orbit-muted">No pipeline has run yet. <Link href="/pipelines" className="text-orbit-purpleLight">Create a pipeline</Link>.</div> : (
          <table className="orbit-table w-full">
            <thead><tr><th>Pipeline</th><th>Status</th><th>Started</th><th>Duration</th><th className="text-right">Rows written</th><th>Outputs</th></tr></thead>
            <tbody>
              {d.recent_runs.map((r) => (
                <tr key={r.id}>
                  <td><Link href={`/runs/${r.id}`} className="text-white hover:text-orbit-purpleLight">{r.pipeline_name}</Link> <span className="text-orbit-subtle font-mono text-[11px]">v{r.pipeline_version}</span></td>
                  <td><StatusPill status={r.status} /></td>
                  <td className="text-orbit-muted">{ago(r.started_at || r.queued_at)}</td>
                  <td className="font-mono">{duration(r.duration_ms)}</td>
                  <td className="text-right font-mono">{compact(r.records_out)}</td>
                  <td className="text-[11px] font-mono text-orbit-muted">{r.outputs.map((o) => o.qualified_name).join(", ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </PageContainer>
  );
}
