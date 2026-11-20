"use client";

import { ArrowLeft, Download, GitBranch, RotateCcw, Square } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import React, { useState } from "react";
import useSWR from "swr";
import { RunLogs } from "@/components/data/RunParts";
import { Badge, Button, Card, CardHeader, ErrorState, InlineAlert, LoadingState, PageContainer, StatusPill } from "@/components/ui";
import { API_URL, ApiError, api, session } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { compact, dateTime, duration } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { DatasetRef, RunArtifact, RunDetail } from "@/lib/types";

function DatasetList({ items }: { items: DatasetRef[] }) {
  if (!items.length) return <div className="text-xs text-orbit-subtle">—</div>;
  return (
    <ul className="space-y-1.5">
      {items.map((d) => (
        <li key={d.dataset_id + d.version} className="flex justify-between text-xs">
          <Link href={`/datasets/${d.dataset_id}`} className="font-mono text-white hover:text-orbit-purpleLight">{d.qualified_name} v{d.version}</Link>
          <span className="font-mono text-orbit-muted">{compact(d.rows)} rows</span>
        </li>
      ))}
    </ul>
  );
}

const TRIGGER_LABEL: Record<string, string> = {
  manual: "manual", schedule: "schedule", new_data: "new data", after_pipeline: "after another pipeline", api: "API trigger",
  retry: "automatic retry", backfill: "backfill",
};

function Artifacts({ runId, items }: { runId: string; items: RunArtifact[] }) {
  const notify = useToast();
  const download = async (i: number, name: string) => {
    try {
      const res = await fetch(`${API_URL}/api/v1/pipelines/runs/${runId}/artifacts/${i}/download`, { headers: { Authorization: `Bearer ${session.token()}` } });
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    }
  };
  return (
    <div className="space-y-2">
      {items.map((a, i) => {
        const det = (a.details || {}) as Record<string, unknown>;
        return (
          <div key={a.node_id + i} className="p-3 rounded-lg bg-orbit-bg border border-orbit-border">
            <div className="flex items-center gap-2 text-xs">
              <StatusPill status={a.status} /><span className="text-white font-semibold">{a.label}</span>
              <span className="font-mono text-[10px] text-orbit-subtle">{a.type}</span>
              {a.type === "export_file" && a.status === "succeeded" && (
                <Button size="sm" icon={Download} className="ml-auto" onClick={() => download(i, String(det.filename))}>{String(det.filename)}</Button>
              )}
            </div>
            <div className="text-[11px] text-orbit-muted mt-1">{a.message}</div>
          </div>
        );
      })}
    </div>
  );
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const notify = useToast();
  const [busy, setBusy] = useState(false);
  const { data: run, error, mutate } = useSWR<RunDetail>(`/runs/${id}`, {
    refreshInterval: (d) => (!d || d.status === "queued" || d.status === "running" ? 1000 : 0),
  });

  const act = async (action: "retry" | "cancel") => {
    setBusy(true);
    try {
      const r = await api<{ id: string; status: string }>(`/runs/${id}/${action}`, { method: "POST" });
      if (action === "retry") {
        notify(`New attempt started (${r.status})`);
        router.push(`/runs/${r.id}`);
      } else {
        notify("Cancellation requested");
        mutate();
      }
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!run) return <LoadingState />;
  const active = run.status === "queued" || run.status === "running";
  const steps = run.graph_snapshot.nodes;

  return (
    <PageContainer>
      <Link href="/runs" className="inline-flex items-center gap-1 text-xs text-orbit-muted hover:text-white">
        <ArrowLeft className="w-3.5 h-3.5" /> All runs
      </Link>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-orbit-purple" />
              <Link href={`/pipelines/${run.pipeline_id}`} className="text-base font-bold text-white hover:text-orbit-purpleLight">{run.pipeline_name}</Link>
              <Badge>v{run.pipeline_version}</Badge>
              <StatusPill status={run.status} />
              {run.attempt > 1 && <Badge tone="amber">attempt {run.attempt}</Badge>}
              <Badge tone="cyan">{TRIGGER_LABEL[run.trigger || "manual"] || run.trigger}</Badge>
            </div>
            <div className="text-xs text-orbit-muted mt-1">
              Queued {dateTime(run.queued_at)} · started {dateTime(run.started_at)} · finished {dateTime(run.finished_at)} · duration {duration(run.duration_ms)}
            </div>
            <div className="text-[11px] text-orbit-subtle font-mono mt-1">
              run {run.id}{run.correlation_id ? ` · correlation ${run.correlation_id}` : ""}
              {run.retry_of_id && <> · retry of <Link href={`/runs/${run.retry_of_id}`} className="underline">{run.retry_of_id.slice(0, 8)}</Link></>}
            </div>
            {run.params && Object.keys(run.params).length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">{Object.entries(run.params).map(([k, v]) => <Badge key={k} className="font-mono text-[10px]">{k} = {String(v)}</Badge>)}</div>
            )}
          </div>
          {can("pipelines.run") && (
            <div className="flex gap-2">
              {active && <Button variant="danger" icon={Square} loading={busy} disabled={run.cancel_requested} onClick={() => act("cancel")}>
                {run.cancel_requested ? "Cancelling…" : "Cancel run"}</Button>}
              {(run.status === "failed" || run.status === "cancelled") && (
                <Button variant="primary" icon={RotateCcw} loading={busy} onClick={() => act("retry")}>Retry (same version)</Button>
              )}
            </div>
          )}
        </div>
        {run.error_message && <InlineAlert tone="red" className="mt-3">{run.error_class}: {run.error_message}</InlineAlert>}
      </Card>

      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader title="Steps" subtitle="Executed in dependency order by DuckDB" />
          <table className="orbit-table w-full">
            <thead><tr><th>Step</th><th>Type</th><th>Status</th><th className="text-right">Rows</th><th>Details</th></tr></thead>
            <tbody>
              {steps.map((n) => {
                const st = run.node_status[n.id] || { status: "pending" };
                return (
                  <tr key={n.id}>
                    <td className="text-white">{n.label}</td>
                    <td className="font-mono text-[11px] text-orbit-muted">{n.type}</td>
                    <td><StatusPill status={st.status} /></td>
                    <td className="text-right font-mono">{compact(st.rows)}</td>
                    <td className="text-[11px] text-orbit-muted max-w-sm">
                      {st.message}
                      {st.quality?.filter((q) => !q.passed).map((q) => (
                        <div key={q.name} className="text-orbit-amber">{q.name}: {q.error || `${q.failed}/${q.total} rows failed`} ({q.on_failure})</div>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Read" subtitle={`${compact(run.records_in)} rows in`} />
            <DatasetList items={run.inputs} />
          </Card>
          <Card>
            <CardHeader title="Written" subtitle={`${compact(run.records_out)} rows out`} />
            <DatasetList items={run.outputs} />
          </Card>
        </div>
      </div>

      {(run.artifacts?.length ?? 0) > 0 && (
        <Card>
          <CardHeader title="Deliveries" subtitle="Files, APIs and e-mails sent after the datasets were written" />
          <Artifacts runId={run.id} items={run.artifacts!} />
        </Card>
      )}

      <Card>
        <CardHeader title="Logs" subtitle={active ? "Live — refreshed every second" : `${run.logs.length} line(s)`} />
        <RunLogs logs={run.logs} className="max-h-[420px]" />
      </Card>
    </PageContainer>
  );
}
