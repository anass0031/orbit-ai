"use client";

import { Activity, AlertTriangle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { useState } from "react";
import useSWR from "swr";
import { RunsChart } from "@/components/data/RunsChart";
import {
  Card, CardHeader, EmptyState, ErrorState, FilterChips, Kpi, LoadingState, PageContainer, Pagination, StatusPill,
} from "@/components/ui";
import { ago, compact, duration, pct } from "@/lib/format";
import type { Attention, DayRuns, Page, RunItem, RunStats } from "@/lib/types";

const LIMIT = 25;

export default function RunsPage() {
  const router = useRouter();
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const query = `/runs?limit=${LIMIT}&offset=${offset}${status !== "all" ? `&status=${status}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  const { data, error, mutate } = useSWR<Page<RunItem>>(query, { refreshInterval: 3000 });
  const { data: stats } = useSWR<RunStats & { daily: DayRuns[]; attention: Attention }>("/runs/stats?days=14", { refreshInterval: 10000 });

  const att = stats?.attention;
  const alerts = att ? att.recurring_failures.length + att.stuck_runs.length : 0;

  return (
    <PageContainer>
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <Kpi label="Runs (14 days)" value={compact(stats.total)} sub={`${stats.counts.failed} failed · ${stats.counts.cancelled} cancelled`} />
          <Kpi label="Success rate" value={pct(stats.success_rate)} subTone={stats.success_rate !== null && stats.success_rate < 90 ? "red" : "green"}
            sub="succeeded / (succeeded + failed)" />
          <Kpi label="Average duration" value={duration(stats.avg_duration_ms)} sub={`p95 ${duration(stats.p95_duration_ms)}`} />
          <Kpi label="Rows written" value={compact(stats.records_written)} sub="by succeeded runs" />
          <Kpi label="Active now" value={`${stats.running_now} / ${stats.queued_now}`} sub="running / queued" />
        </div>
      )}

      {att && alerts > 0 && (
        <Card className="border-orbit-amber/40">
          <CardHeader title={<span className="flex items-center gap-2"><AlertTriangle className="w-4 h-4 text-orbit-amber" />Needs attention</span>} />
          <ul className="space-y-1.5 text-xs">
            {att.recurring_failures.map((r) => (
              <li key={r.pipeline_id}>
                <Link href={`/runs/${r.last_run_id}`} className="text-white hover:text-orbit-purpleLight">{r.name}</Link>
                <span className="text-orbit-redLight"> failed {r.streak} times in a row</span>
                <span className="text-orbit-muted"> — {r.last_error}</span>
              </li>
            ))}
            {att.stuck_runs.map((r) => (
              <li key={r.id}>
                <Link href={`/runs/${r.id}`} className="text-white hover:text-orbit-purpleLight">{r.pipeline_name}</Link>
                <span className="text-orbit-amber"> {r.reason}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {stats && (
        <Card>
          <CardHeader title="Runs per day" subtitle="Last 14 days, by outcome" />
          <RunsChart data={stats.daily} height={180} />
        </Card>
      )}

      <Card>
        <CardHeader title="Run history" subtitle="Refreshed every 3 seconds"
          right={<div className="flex items-center gap-2">
            <input className="orbit-input !w-48" placeholder="Pipeline name…" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
            <FilterChips value={status} onChange={(v) => { setStatus(v); setOffset(0); }} options={[
              { value: "all", label: "All" }, { value: "running", label: "Running" }, { value: "succeeded", label: "Succeeded" },
              { value: "failed", label: "Failed" }, { value: "cancelled", label: "Cancelled" },
            ]} />
          </div>} />
        {error ? <ErrorState error={error} onRetry={() => mutate()} /> : !data ? <LoadingState /> : data.items.length === 0 ? (
          <EmptyState icon={Activity} title="No runs" description="Runs appear here as soon as a pipeline is started." />
        ) : (
          <>
            <table className="orbit-table w-full">
              <thead><tr><th>Pipeline</th><th>Status</th><th>Version</th><th>Started</th><th>Duration</th><th className="text-right">Rows in → out</th><th>Outputs</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} className="cursor-pointer" onClick={() => router.push(`/runs/${r.id}`)}>
                    <td>
                      <span className="text-white font-medium">{r.pipeline_name}</span>
                      {r.attempt > 1 && <span className="text-[11px] text-orbit-subtle"> · attempt {r.attempt}</span>}
                      {r.status === "failed" && <div className="text-[11px] text-orbit-redLight truncate max-w-md">{r.error_message}</div>}
                    </td>
                    <td><StatusPill status={r.status} /></td>
                    <td className="font-mono text-orbit-nav">v{r.pipeline_version}</td>
                    <td className="text-orbit-muted">{ago(r.started_at || r.queued_at)}</td>
                    <td className="font-mono">{duration(r.duration_ms)}</td>
                    <td className="text-right font-mono">{compact(r.records_in)} → {compact(r.records_out)}</td>
                    <td className="text-[11px] font-mono text-orbit-muted">{r.outputs.map((o) => `${o.qualified_name} v${o.version}`).join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </PageContainer>
  );
}
