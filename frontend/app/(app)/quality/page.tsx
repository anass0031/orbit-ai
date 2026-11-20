"use client";

import { CheckCircle2, Play, Plus, ShieldCheck, Trash2, XCircle } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import React, { Suspense, useState } from "react";
import useSWR from "swr";
import { ScoreBar, ScoreRing, scoreTone } from "@/components/data/RunParts";
import {
  Badge, Button, Card, CardHeader, DataTable, EmptyState, ErrorState, Field, FilterChips, InlineAlert, LoadingState, Modal,
  PageContainer, Pagination, Segmented, StatusPill, cn,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { Dataset, Page, QualityIssue, QualityOverview, QualityRule, RuleType } from "@/lib/types";

const DIMENSIONS = ["completeness", "validity", "uniqueness", "consistency", "freshness"];
const RULE_TYPES: { value: RuleType; label: string; column: boolean; help: string }[] = [
  { value: "not_null", label: "Not null", column: true, help: "The column must never be empty." },
  { value: "unique", label: "Unique", column: true, help: "No two rows may share a value." },
  { value: "range", label: "Range", column: true, help: "Numeric values between a min and / or a max." },
  { value: "regex", label: "Pattern (regex)", column: true, help: "Text values must fully match a regular expression." },
  { value: "allowed_values", label: "Allowed values", column: true, help: "Values must belong to a list." },
  { value: "freshness", label: "Freshness", column: false, help: "Latest timestamp (or last update) not older than N hours." },
  { value: "row_count", label: "Row count", column: false, help: "Number of rows between a min and / or a max." },
  { value: "custom_sql", label: "Custom SQL", column: false, help: "Boolean expression every valid row must satisfy." },
];
const ON_FAILURE = {
  warn: "Warn — record the anomaly, write the data",
  quarantine: "Quarantine — set failing rows aside, write the rest",
  fail: "Block — write nothing, keep the previous version",
};

function sevTone(s: string) {
  return s === "high" ? "red" : s === "medium" ? "amber" : "gray";
}

// ── Overview ────────────────────────────────────────────────────────────
function OverviewTab({ onOpen }: { onOpen: (datasetId: string) => void }) {
  const { can } = useAuth();
  const notify = useToast();
  const { data, error, mutate } = useSWR<QualityOverview>("/quality/overview");
  const [running, setRunning] = useState<string | null>(null);

  const run = async (id: string, name: string) => {
    setRunning(id);
    try {
      const res = await api<{ passed: boolean }[]>(`/quality/datasets/${id}/run`, { method: "POST" });
      const failed = res.filter((r) => !r.passed).length;
      notify(`${name}: ${res.length - failed}/${res.length} rule(s) passed`, failed ? "error" : "success");
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    } finally {
      setRunning(null);
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!data) return <LoadingState />;
  return (
    <>
      <div className="grid lg:grid-cols-3 gap-4">
        <Card className="flex items-center gap-6">
          <ScoreRing score={data.score} size={96} />
          <div>
            <div className="text-sm font-semibold text-white">Global quality score</div>
            <div className="text-xs text-orbit-muted mt-1">Mean of the dataset scores. A dataset score is the mean latest score of its enabled rules.</div>
            <div className="text-xs mt-2"><span className="text-orbit-redLight font-semibold">{data.open_issues}</span> <span className="text-orbit-muted">open anomalies</span></div>
          </div>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Score by dimension" subtitle="Latest result of every enabled rule, grouped by what it measures" />
          <div className="grid sm:grid-cols-2 gap-x-6 gap-y-3">
            {DIMENSIONS.map((d) => <ScoreBar key={d} label={d} score={data.dimensions[d]} />)}
          </div>
        </Card>
      </div>
      <Card>
        <CardHeader title="Datasets" subtitle="Run the checks of a dataset on its current version at any time" />
        {data.datasets.length === 0 ? <EmptyState title="No datasets yet" description="Import a file to create a Bronze dataset." /> : (
          <table className="orbit-table w-full">
            <thead><tr><th>Dataset</th><th>Score</th><th>Rules</th><th>Open anomalies</th><th>Weakest dimension</th><th /></tr></thead>
            <tbody>
              {data.datasets.map((d) => {
                const weakest = Object.entries(d.dimensions).sort((a, b) => a[1] - b[1])[0];
                return (
                  <tr key={d.id}>
                    <td><Link href={`/datasets/${d.id}`} className="font-mono text-white hover:text-orbit-purpleLight">{d.qualified_name}</Link></td>
                    <td className={cn("font-mono font-semibold", scoreTone(d.quality_score))}>{d.quality_score ?? "—"}</td>
                    <td><button className="text-orbit-purpleLight hover:underline" onClick={() => onOpen(d.id)}>{d.rules}</button></td>
                    <td className={d.open_issues ? "text-orbit-redLight" : "text-orbit-muted"}>{d.open_issues}</td>
                    <td className="text-orbit-muted text-[11px]">{weakest ? `${weakest[0]} (${weakest[1]}%)` : "—"}</td>
                    <td className="text-right">
                      {can("quality.run") && d.rules > 0 && (
                        <Button size="sm" icon={Play} loading={running === d.id} onClick={() => run(d.id, d.qualified_name)}>Run checks</Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

// ── Rules ───────────────────────────────────────────────────────────────
function RuleModal({ open, onClose, datasets, initialDataset, onCreated }: {
  open: boolean; onClose: () => void; datasets: Dataset[]; initialDataset: string; onCreated: () => void;
}) {
  const notify = useToast();
  const [f, setF] = useState({ dataset_id: initialDataset, rule_type: "not_null" as RuleType, column: "", name: "", severity: "medium",
    on_failure: "warn", threshold: "100", min: "", max: "", pattern: "", values: "", hours: "24", expression: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ds = datasets.find((d) => d.id === (f.dataset_id || initialDataset));
  const type = RULE_TYPES.find((t) => t.value === f.rule_type)!;
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setError(null);
    const params: Record<string, unknown> =
      f.rule_type === "range" || f.rule_type === "row_count" ? { min: num(f.min), max: num(f.max) }
        : f.rule_type === "regex" ? { pattern: f.pattern }
          : f.rule_type === "allowed_values" ? { values: f.values.split(",").map((v) => v.trim()).filter(Boolean) }
            : f.rule_type === "freshness" ? { max_age_hours: num(f.hours) }
              : f.rule_type === "custom_sql" ? { expression: f.expression } : {};
    try {
      await api("/quality/rules", { method: "POST", body: {
        dataset_id: ds?.id, rule_type: f.rule_type, column: f.column || null, name: f.name || null, params,
        severity: f.severity, on_failure: f.on_failure, threshold: Number(f.threshold) / 100,
      } });
      notify("Rule created — run the checks to compute its score");
      onCreated();
      onClose();
    } catch (e) {
      if (e instanceof ApiError) {
        setErrors(e.fieldErrors);
        setError(e.message + (Array.isArray(e.details) ? ": " + (e.details as { message: string }[]).map((d) => d.message).join("; ") : ""));
      } else setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="New quality rule" width="max-w-xl"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!ds} onClick={submit}>Create rule</Button></>}>
      {error && <InlineAlert tone="red">{error}</InlineAlert>}
      <div className="grid grid-cols-2 gap-3">
        <Field label="Dataset">
          <select className="orbit-input" value={ds?.id || ""} onChange={set("dataset_id")}>
            <option value="">Select…</option>
            {datasets.map((d) => <option key={d.id} value={d.id}>{d.qualified_name}</option>)}
          </select>
        </Field>
        <Field label="Rule type" hint={type.help}>
          <select className="orbit-input" value={f.rule_type} onChange={set("rule_type")}>
            {RULE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </Field>
      </div>
      {(type.column || f.rule_type === "freshness") && (
        <Field label={f.rule_type === "freshness" ? "Timestamp column (optional — default: last update)" : "Column"} error={errors.column}>
          <select className="orbit-input" value={f.column} onChange={set("column")}>
            <option value="">{type.column ? "Select…" : "Dataset last update"}</option>
            {ds?.schema.map((c) => <option key={c.name} value={c.name}>{c.name} · {c.type}</option>)}
          </select>
        </Field>
      )}
      {(f.rule_type === "range" || f.rule_type === "row_count") && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Min"><input className="orbit-input" value={f.min} onChange={set("min")} /></Field>
          <Field label="Max"><input className="orbit-input" value={f.max} onChange={set("max")} /></Field>
        </div>
      )}
      {f.rule_type === "regex" && <Field label="Pattern" hint="e.g. [^@]+@[^@]+\.[a-z]+"><input className="orbit-input font-mono" value={f.pattern} onChange={set("pattern")} /></Field>}
      {f.rule_type === "allowed_values" && <Field label="Allowed values" hint="Comma separated"><input className="orbit-input" value={f.values} onChange={set("values")} /></Field>}
      {f.rule_type === "freshness" && <Field label="Maximum age (hours)"><input className="orbit-input" value={f.hours} onChange={set("hours")} /></Field>}
      {f.rule_type === "custom_sql" && (
        <Field label="Condition every valid row satisfies" hint="e.g. amount >= 0 AND quantity > 0">
          <input className="orbit-input font-mono" value={f.expression} onChange={set("expression")} />
        </Field>
      )}
      <div className="grid grid-cols-3 gap-3">
        <Field label="Severity">
          <select className="orbit-input" value={f.severity} onChange={set("severity")}>
            <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
          </select>
        </Field>
        <Field label="Pass threshold (%)" hint="Share of rows that must pass">
          <input className="orbit-input" value={f.threshold} onChange={set("threshold")} />
        </Field>
        <Field label="Name (optional)"><input className="orbit-input" value={f.name} onChange={set("name")} /></Field>
      </div>
      <Field label="When the rule fails during a write (pipeline or import)">
        <select className="orbit-input" value={f.on_failure} onChange={set("on_failure")}>
          {Object.entries(ON_FAILURE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
    </Modal>
  );
}

function RulesTab({ datasetId, setDatasetId }: { datasetId: string; setDatasetId: (v: string) => void }) {
  const { can } = useAuth();
  const notify = useToast();
  const { data: datasets } = useSWR<Dataset[]>("/datasets");
  const { data: rules, error, mutate } = useSWR<QualityRule[]>(`/quality/rules${datasetId ? `?dataset_id=${datasetId}` : ""}`);
  const [open, setOpen] = useState(false);
  const [evidence, setEvidence] = useState<QualityRule | null>(null);

  const patch = async (r: QualityRule, body: Partial<QualityRule>) => {
    try {
      await api(`/quality/rules/${r.id}`, { method: "PATCH", body });
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    }
  };
  const remove = async (r: QualityRule) => {
    if (!window.confirm(`Delete the rule "${r.name}"? Its results and anomalies are deleted too.`)) return;
    try {
      await api(`/quality/rules/${r.id}`, { method: "DELETE" });
      notify("Rule deleted");
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    }
  };

  return (
    <Card>
      <CardHeader title="Rules" subtitle="Checked on demand, and automatically before every write by a pipeline or a file import"
        right={<>
          <select className="orbit-input !w-56" value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
            <option value="">All datasets</option>
            {datasets?.map((d) => <option key={d.id} value={d.id}>{d.qualified_name}</option>)}
          </select>
          {can("quality.write") && <Button variant="primary" icon={Plus} onClick={() => setOpen(true)}>New rule</Button>}
        </>} />
      {error ? <ErrorState error={error} onRetry={() => mutate()} /> : !rules ? <LoadingState /> : rules.length === 0 ? (
        <EmptyState icon={ShieldCheck} title="No rules" description="Define what good data means: not null, unique, ranges, patterns, freshness…" />
      ) : (
        <table className="orbit-table w-full">
          <thead><tr><th>Rule</th><th>Dataset</th><th>Dimension</th><th>Severity</th><th>On failure</th><th>Last result</th><th /></tr></thead>
          <tbody>
            {rules.map((r) => {
              const lr = r.last_result;
              return (
                <tr key={r.id} className={r.enabled ? undefined : "opacity-50"}>
                  <td>
                    <div className="text-white">{r.name}</div>
                    <div className="text-[11px] font-mono text-orbit-subtle">{r.rule_type}{r.column ? `(${r.column})` : ""} {Object.keys(r.params).length ? JSON.stringify(r.params) : ""}</div>
                  </td>
                  <td className="font-mono text-[11px]">{r.dataset_name}</td>
                  <td className="capitalize text-orbit-muted">{r.dimension}</td>
                  <td><Badge tone={sevTone(r.severity)}>{r.severity}</Badge></td>
                  <td className="text-[11px] text-orbit-muted">{r.on_failure}</td>
                  <td>
                    {!lr ? <span className="text-orbit-subtle text-[11px]">never run</span> : (
                      <button className="flex items-center gap-1.5 text-left" onClick={() => setEvidence(r)}>
                        {lr.passed ? <CheckCircle2 className="w-3.5 h-3.5 text-orbit-green" /> : <XCircle className="w-3.5 h-3.5 text-orbit-redLight" />}
                        <span className={cn("font-mono text-[11px]", scoreTone(lr.score))}>{lr.score}%</span>
                        <span className="text-[11px] text-orbit-muted">{lr.failed_count ? `${lr.failed_count} failing · ` : ""}{ago(lr.executed_at)}</span>
                      </button>
                    )}
                  </td>
                  <td className="text-right whitespace-nowrap">
                    {can("quality.write") && (
                      <span className="inline-flex items-center gap-2">
                        <label className="flex items-center gap-1 text-[11px] text-orbit-muted">
                          <input type="checkbox" checked={r.enabled} onChange={(e) => patch(r, { enabled: e.target.checked })} />enabled
                        </label>
                        <button onClick={() => remove(r)} className="text-orbit-subtle hover:text-orbit-redLight" aria-label="Delete rule"><Trash2 className="w-3.5 h-3.5" /></button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {datasets && <RuleModal key={String(open) + datasetId} open={open} onClose={() => setOpen(false)} datasets={datasets} initialDataset={datasetId} onCreated={() => mutate()} />}
      <Modal open={!!evidence} onClose={() => setEvidence(null)} title={evidence?.name || ""} width="max-w-4xl"
        subtitle={evidence?.last_result ? `${evidence.last_result.failed_count} of ${evidence.last_result.total_count} failing · version ${evidence.last_result.dataset_version ?? "not written"} · ${evidence.last_result.trigger}` : undefined}>
        {evidence?.last_result && (
          <>
            {evidence.last_result.error_message && <InlineAlert tone="red">{evidence.last_result.error_message}</InlineAlert>}
            {evidence.last_result.check_sql && <pre className="text-[11px] font-mono bg-orbit-bg border border-orbit-border rounded-lg p-3 whitespace-pre-wrap text-orbit-muted">{evidence.last_result.check_sql}</pre>}
            {evidence.last_result.evidence.length > 0
              ? <DataTable columns={Object.keys(evidence.last_result.evidence[0])} rows={evidence.last_result.evidence} maxHeight="50vh" />
              : <div className="text-xs text-orbit-muted">No failing rows.</div>}
          </>
        )}
      </Modal>
    </Card>
  );
}

// ── Anomalies ───────────────────────────────────────────────────────────
function IssuesTab() {
  const { can } = useAuth();
  const notify = useToast();
  const [status, setStatus] = useState("active");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<QualityIssue | null>(null);
  const [note, setNote] = useState("");
  const { data, error, mutate } = useSWR<Page<QualityIssue>>(`/quality/issues?limit=25&offset=${offset}${status !== "all" ? `&status=${status}` : ""}`);

  const triage = async (i: QualityIssue, next: QualityIssue["status"]) => {
    try {
      await api(`/quality/issues/${i.id}`, { method: "PATCH", body: { status: next, note: note || null } });
      notify(`Anomaly ${next}`);
      setOpen(null);
      setNote("");
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    }
  };

  return (
    <Card>
      <CardHeader title="Anomalies" subtitle="Opened when a rule fails, updated while it keeps failing, resolved automatically when it passes again"
        right={<FilterChips value={status} onChange={(v) => { setStatus(v); setOffset(0); }} options={[
          { value: "active", label: "Active" }, { value: "open", label: "Open" }, { value: "acknowledged", label: "Acknowledged" },
          { value: "resolved", label: "Resolved" }, { value: "all", label: "All" },
        ]} />} />
      {error ? <ErrorState error={error} onRetry={() => mutate()} /> : !data ? <LoadingState /> : data.items.length === 0 ? (
        <EmptyState icon={CheckCircle2} title="No anomalies here" description="Failing rules will show up in this list." />
      ) : (
        <>
          <table className="orbit-table w-full">
            <thead><tr><th>Anomaly</th><th>Dataset</th><th>Severity</th><th>Status</th><th>Seen</th><th>Last seen</th></tr></thead>
            <tbody>
              {data.items.map((i) => (
                <tr key={i.id} className="cursor-pointer" onClick={() => { setOpen(i); setNote(""); }}>
                  <td><div className="text-white">{i.title}</div><div className="text-[11px] text-orbit-subtle capitalize">{i.dimension}{i.column ? ` · ${i.column}` : ""}</div></td>
                  <td className="font-mono text-[11px]">{i.dataset_name}</td>
                  <td><Badge tone={sevTone(i.severity)}>{i.severity}</Badge></td>
                  <td><StatusPill status={i.status} /></td>
                  <td className="font-mono">{i.occurrences}×</td>
                  <td className="text-orbit-muted">{ago(i.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pagination total={data.total} limit={25} offset={offset} onChange={setOffset} />
        </>
      )}
      <Modal open={!!open} onClose={() => setOpen(null)} title={open?.title || ""} width="max-w-4xl"
        subtitle={open ? `${open.dataset_name} · rule "${open.rule_name}" · detected ${ago(open.detected_at)} · seen ${open.occurrences} time(s)` : undefined}
        footer={open && can("quality.triage") ? <>
          {open.status !== "acknowledged" && open.status !== "resolved" && <Button onClick={() => triage(open, "acknowledged")}>Acknowledge</Button>}
          {open.status !== "resolved" ? <Button variant="primary" onClick={() => triage(open, "resolved")}>Mark resolved</Button>
            : <Button onClick={() => triage(open, "open")}>Reopen</Button>}
        </> : undefined}>
        {open && (
          <>
            {open.resolution_note && <InlineAlert tone="cyan">{open.resolution_note}</InlineAlert>}
            <div className="orbit-label">Sample of failing rows</div>
            {open.evidence.length ? <DataTable columns={Object.keys(open.evidence[0])} rows={open.evidence} maxHeight="45vh" />
              : <div className="text-xs text-orbit-muted">No row sample (dataset-level check).</div>}
            {can("quality.triage") && (
              <Field label="Note (optional)"><input className="orbit-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was done, who is on it…" /></Field>
            )}
          </>
        )}
      </Modal>
    </Card>
  );
}

function Quality() {
  const params = useSearchParams();
  const [tab, setTab] = useState<"overview" | "rules" | "issues">((params.get("tab") as "rules" | "issues") || "overview");
  const [datasetId, setDatasetId] = useState(params.get("dataset") || "");
  return (
    <PageContainer>
      <Segmented value={tab} onChange={setTab} options={[
        { value: "overview", label: "Scores" }, { value: "rules", label: "Rules" }, { value: "issues", label: "Anomalies" },
      ]} />
      {tab === "overview" && <OverviewTab onOpen={(id) => { setDatasetId(id); setTab("rules"); }} />}
      {tab === "rules" && <RulesTab datasetId={datasetId} setDatasetId={setDatasetId} />}
      {tab === "issues" && <IssuesTab />}
    </PageContainer>
  );
}

export default function QualityPage() {
  return <Suspense><Quality /></Suspense>;
}
