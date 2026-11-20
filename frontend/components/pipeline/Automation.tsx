"use client";
// Automation drawer of a pipeline: schedule, triggers, conditions, retry & time limit, notifications, SLA, parameters, backfill.
import { CalendarClock, Copy, KeyRound, Play, Save, Square } from "lucide-react";
import React, { useEffect, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Drawer, ErrorState, Field, InlineAlert, LoadingState, Segmented, cn } from "@/components/ui";
import { API_URL, ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, dateTime } from "@/lib/format";
import { ROLE_OPTIONS } from "@/lib/nav";
import { useToast } from "@/lib/toast";
import type { AutomationInfo, AutomationSettings, Dataset, ParamDef } from "@/lib/types";
import { Check, ListEditor, small } from "./fields";

type Tab = "schedule" | "triggers" | "rules" | "notify" | "params" | "backfill" | "events";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function AutomationDrawer({ pipelineId, open, onClose, onSaved }: { pipelineId: string; open: boolean; onClose: () => void; onSaved: () => void }) {
  const { can } = useAuth();
  const notify = useToast();
  const ro = !can("pipelines.write");
  const { data, error, mutate } = useSWR<AutomationInfo>(open ? `/pipelines/${pipelineId}/automation` : null, { refreshInterval: open ? 5000 : 0 });
  const { data: datasets } = useSWR<Dataset[]>(open ? "/datasets" : null);
  const { data: pipelines } = useSWR<{ id: string; name: string }[]>(open ? "/pipelines" : null);
  const [s, setS] = useState<AutomationSettings | null>(null);
  const [tab, setTab] = useState<Tab>("schedule");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [bf, setBf] = useState({ start: "", end: "", step: "day", continue_on_failure: false });

  useEffect(() => { if (data && (!s || !open)) setS(JSON.parse(JSON.stringify(data.settings))); }, [data, open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!open) { setS(null); setToken(null); setErrors({}); } }, [open]);

  const up = <K extends keyof AutomationSettings>(k: K, patch: Partial<AutomationSettings[K]> | AutomationSettings[K]) =>
    setS((x) => (x ? { ...x, [k]: typeof x[k] === "object" && x[k] !== null && !Array.isArray(x[k]) ? { ...(x[k] as object), ...(patch as object) } : patch } : x));

  const save = async () => {
    if (!s) return;
    setBusy(true);
    setErrors({});
    try {
      await api(`/pipelines/${pipelineId}/automation`, { method: "PUT", body: s });
      notify("Automation saved");
      mutate();
      onSaved();
    } catch (e) {
      if (e instanceof ApiError) setErrors(e.fieldErrors);
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const newToken = async () => {
    try {
      const r = await api<{ token: string }>(`/pipelines/${pipelineId}/trigger-token`, { method: "POST" });
      setToken(r.token);
      mutate();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    }
  };
  const revoke = async () => {
    await api(`/pipelines/${pipelineId}/trigger-token`, { method: "DELETE" }).catch(() => undefined);
    setToken(null);
    mutate();
  };
  const startBackfill = async () => {
    setBusy(true);
    try {
      await api(`/pipelines/${pipelineId}/backfill`, { method: "POST", body: { ...bf, params: {} } });
      notify("Backfill started");
      mutate();
    } catch (e) {
      if (e instanceof ApiError) setErrors(e.fieldErrors);
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const stopBackfill = async () => {
    await api(`/pipelines/${pipelineId}/backfill`, { method: "DELETE" }).catch(() => undefined);
    mutate();
  };

  const err = (k: string) => errors[k];
  const inputs = datasets || [];

  return (
    <Drawer open={open} onClose={onClose} width="w-[640px]" title="Automation"
      subtitle={data ? <span className="flex items-center gap-2"><CalendarClock className="w-3.5 h-3.5" />{data.schedule_text}{data.next_run_at && <> · next run {dateTime(data.next_run_at)}</>}</span> : undefined}>
      {error ? <ErrorState error={error} /> : !data || !s ? <LoadingState /> : (
        <div className="space-y-4">
          <Segmented<Tab> value={tab} onChange={setTab} options={[{ value: "schedule", label: "Schedule" }, { value: "triggers", label: "Triggers" },
            { value: "rules", label: "Conditions & retry" }, { value: "notify", label: "Alerts" }, { value: "params", label: "Parameters" },
            { value: "backfill", label: "Backfill" }, { value: "events", label: `Log (${data.events.length})` }]} />
          {data.retry && <InlineAlert tone="amber">Automatic retry (attempt {data.retry.attempt}) planned at {dateTime(data.retry.due_at)}.</InlineAlert>}
          {data.pending && <InlineAlert tone="cyan">A “{data.pending.trigger}” run waits for the active run to finish.</InlineAlert>}

          {tab === "schedule" && (
            <div className="space-y-3">
              <Check disabled={ro} label={<b>Run on a schedule</b>} value={s.schedule.enabled} onChange={(v) => up("schedule", { enabled: v })} />
              {s.schedule.enabled && (
                <>
                  <Segmented value={s.schedule.kind} onChange={(v) => up("schedule", { kind: v })} options={[{ value: "interval", label: "Every N min" },
                    { value: "daily", label: "Daily" }, { value: "weekly", label: "Weekly" }, { value: "monthly", label: "Monthly" }, { value: "cron", label: "Cron" }]} />
                  {s.schedule.kind === "interval" && <Field label="Every (minutes)" error={err("schedule.every_minutes")}><input type="number" className="orbit-input" disabled={ro} value={s.schedule.every_minutes} onChange={(e) => up("schedule", { every_minutes: Number(e.target.value) })} /></Field>}
                  {["daily", "weekly", "monthly"].includes(s.schedule.kind) && <Field label="At (UTC, HH:MM)" error={err("schedule.time")}><input className="orbit-input font-mono" disabled={ro} value={s.schedule.time} onChange={(e) => up("schedule", { time: e.target.value })} /></Field>}
                  {s.schedule.kind === "weekly" && (
                    <Field label="Days" error={err("schedule.weekdays")}>
                      <div className="flex gap-1.5">{DAYS.map((dname, i) => {
                        const on = s.schedule.weekdays.includes(i + 1);
                        return <button key={dname} disabled={ro} onClick={() => up("schedule", { weekdays: on ? s.schedule.weekdays.filter((x) => x !== i + 1) : [...s.schedule.weekdays, i + 1] })}
                          className={cn("px-2.5 py-1 rounded-md text-[11px] border", on ? "bg-orbit-purple/20 border-orbit-purple/40 text-white" : "border-orbit-border text-orbit-muted")}>{dname}</button>;
                      })}</div>
                    </Field>
                  )}
                  {s.schedule.kind === "monthly" && <Field label="Day of the month" error={err("schedule.day_of_month")} hint="31 = last day of shorter months"><input type="number" className="orbit-input" disabled={ro} value={s.schedule.day_of_month} onChange={(e) => up("schedule", { day_of_month: Number(e.target.value) })} /></Field>}
                  {s.schedule.kind === "cron" && <Field label="Cron expression (UTC)" error={err("schedule.cron")} hint="minute hour day month weekday — e.g. */15 8-18 * * 1-5 (every 15 min, 8:00–18:59, Mon–Fri)"><input className="orbit-input font-mono" disabled={ro} value={s.schedule.cron} onChange={(e) => up("schedule", { cron: e.target.value })} /></Field>}
                </>
              )}
              <p className="text-[11px] text-orbit-subtle">The scheduler checks every minute. Times are UTC. Only the latest published version runs.</p>
            </div>
          )}

          {tab === "triggers" && (
            <div className="space-y-4">
              <div className="space-y-2">
                <Check disabled={ro} label={<b>When new data arrives</b>} value={s.on_new_data.enabled} onChange={(v) => up("on_new_data", { enabled: v })} />
                {s.on_new_data.enabled && (
                  <>
                    <Field label="Watched datasets (none = the pipeline's dataset inputs)">
                      <div className="max-h-36 overflow-y-auto rounded-lg bg-orbit-bg border border-orbit-border p-2 space-y-1">
                        {inputs.map((ds) => <Check key={ds.id} disabled={ro} label={<span className="font-mono text-[11px]">{ds.qualified_name}</span>} value={s.on_new_data.dataset_ids.includes(ds.id)}
                          onChange={(v) => up("on_new_data", { dataset_ids: v ? [...s.on_new_data.dataset_ids, ds.id] : s.on_new_data.dataset_ids.filter((x) => x !== ds.id) })} />)}
                      </div>
                    </Field>
                    <Check disabled={ro} label="Also when a file is dropped in a File-drop folder of this pipeline" value={s.on_new_data.file_drop} onChange={(v) => up("on_new_data", { file_drop: v })} />
                  </>
                )}
              </div>
              <div className="space-y-2">
                <Check disabled={ro} label={<b>After other pipelines</b>} value={s.after_pipelines.enabled} onChange={(v) => up("after_pipelines", { enabled: v })} />
                {s.after_pipelines.enabled && (
                  <>
                    <Field label="Run after" error={err("after_pipelines.pipeline_ids")}>
                      <div className="max-h-36 overflow-y-auto rounded-lg bg-orbit-bg border border-orbit-border p-2 space-y-1">
                        {(pipelines || []).filter((p) => p.id !== pipelineId).map((p) => <Check key={p.id} disabled={ro} label={p.name} value={s.after_pipelines.pipeline_ids.includes(p.id)}
                          onChange={(v) => up("after_pipelines", { pipeline_ids: v ? [...s.after_pipelines.pipeline_ids, p.id] : s.after_pipelines.pipeline_ids.filter((x) => x !== p.id) })} />)}
                      </div>
                    </Field>
                    <Segmented value={s.after_pipelines.on} onChange={(v) => up("after_pipelines", { on: v })} options={[{ value: "success", label: "after success" }, { value: "any", label: "after success or failure" }]} />
                  </>
                )}
                {data.dependents.length > 0 && <div className="text-[11px] text-orbit-muted">Runs after this one: {data.dependents.map((x) => x.name).join(", ")}</div>}
              </div>
              <div className="space-y-2 p-3 rounded-lg border border-orbit-border">
                <div className="flex items-center justify-between"><b className="text-xs text-white">API trigger</b>{data.has_token ? <Badge tone="green" dot>enabled</Badge> : <Badge>off</Badge>}</div>
                <p className="text-[11px] text-orbit-muted">Another tool starts the pipeline with a POST and a secret token (no login).</p>
                {token && (
                  <InlineAlert tone="amber">
                    Copy the token now, it is not shown again:
                    <div className="font-mono text-[11px] text-white break-all mt-1">{token}</div>
                    <pre className="mt-2 text-[10px] whitespace-pre-wrap text-orbit-text">{`curl -X POST ${API_URL}${data.trigger_url} \\\n  -H "X-Orbit-Trigger-Token: ${token}" \\\n  -H "Content-Type: application/json" -d '{"params": {"run_date": "2026-01-31"}}'`}</pre>
                    <Button size="sm" icon={Copy} className="mt-1" onClick={() => { navigator.clipboard?.writeText(token); notify("Token copied"); }}>Copy</Button>
                  </InlineAlert>
                )}
                {!ro && <div className="flex gap-2"><Button size="sm" icon={KeyRound} onClick={newToken}>{data.has_token ? "Replace the token" : "Create a token"}</Button>{data.has_token && <Button size="sm" variant="ghost" onClick={revoke}>Revoke</Button>}</div>}
              </div>
            </div>
          )}

          {tab === "rules" && (
            <div className="space-y-3">
              <Check disabled={ro} label="Skip automatic runs when no input changed (and write nothing when incremental / file-drop sources bring no new rows)" value={s.conditions.skip_if_no_new_data} onChange={(v) => up("conditions", { skip_if_no_new_data: v })} />
              <Field label="Skip if an input's quality score is below" error={err("conditions.min_input_quality")}><input type="number" className="orbit-input" disabled={ro} placeholder="e.g. 80" value={s.conditions.min_input_quality ?? ""} onChange={(e) => up("conditions", { min_input_quality: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
              <Field label="If a run is already active" error={err("conditions.if_running")}><Segmented value={s.conditions.if_running} onChange={(v) => up("conditions", { if_running: v })} options={[{ value: "skip", label: "skip" }, { value: "queue", label: "start right after it" }]} /></Field>
              <div className="grid grid-cols-3 gap-2">
                <Field label="Automatic retries" error={err("retry.max_retries")}><input type="number" min={0} max={5} className="orbit-input" disabled={ro} value={s.retry.max_retries} onChange={(e) => up("retry", { max_retries: Number(e.target.value) })} /></Field>
                <Field label="First wait (s)" error={err("retry.backoff_seconds")}><input type="number" className="orbit-input" disabled={ro} value={s.retry.backoff_seconds} onChange={(e) => up("retry", { backoff_seconds: Number(e.target.value) })} /></Field>
                <Field label="× factor" error={err("retry.backoff_factor")}><input type="number" step="0.5" className="orbit-input" disabled={ro} value={s.retry.backoff_factor} onChange={(e) => up("retry", { backoff_factor: Number(e.target.value) })} /></Field>
              </div>
              <p className="text-[11px] text-orbit-subtle">e.g. 3 retries, 60 s × 2 → waits 60 s, 120 s, 240 s. Blocking quality checks are not retried.</p>
              <Field label="Time limit per run (minutes)" error={err("timeout_minutes")}><input type="number" className="orbit-input" disabled={ro} placeholder="no limit" value={s.timeout_minutes ?? ""} onChange={(e) => up("timeout_minutes", e.target.value === "" ? null : Number(e.target.value))} /></Field>
            </div>
          )}

          {tab === "notify" && (
            <div className="space-y-3">
              <Field label="Notify on">
                <div className="flex flex-wrap gap-3">{[["failure", "Failure"], ["retry", "Retry planned"], ["quality_warning", "Warnings"], ["success", "Success"], ["sla", "SLA missed"]].map(([k, label]) => (
                  <Check key={k} disabled={ro} label={label} value={s.notifications.on.includes(k)} onChange={(v) => up("notifications", { on: v ? [...s.notifications.on, k] : s.notifications.on.filter((x) => x !== k) })} />))}</div>
              </Field>
              <Check disabled={ro} label="In the app (bell)" value={s.notifications.in_app} onChange={(v) => up("notifications", { in_app: v })} />
              {s.notifications.in_app && <div className="flex flex-wrap gap-3 pl-5">{ROLE_OPTIONS.map((r) => <Check key={r.value} disabled={ro} label={r.label} value={s.notifications.roles.includes(r.value)} onChange={(v) => up("notifications", { roles: v ? [...s.notifications.roles, r.value] : s.notifications.roles.filter((x) => x !== r.value) })} />)}</div>}
              <Field label="E-mails" error={err("notifications.emails")} hint="Needs ORBIT_SMTP_HOST."><input className="orbit-input" disabled={ro} placeholder="ops@company.com, me@company.com" value={s.notifications.emails.join(", ")} onChange={(e) => up("notifications", { emails: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Field>
              <Field label="Slack / Teams webhook URL" error={err("notifications.webhook_url")}><input className="orbit-input font-mono" disabled={ro} placeholder="https://hooks.slack.com/services/…" value={s.notifications.webhook_url} onChange={(e) => up("notifications", { webhook_url: e.target.value })} /></Field>
              <div className="p-3 rounded-lg border border-orbit-border space-y-2">
                <Check disabled={ro} label={<b>SLA: alert if the output is late</b>} value={s.sla.enabled} onChange={(v) => up("sla", { enabled: v })} />
                {s.sla.enabled && (
                  <>
                    <Field label="Dataset (empty = the first output)" error={err("sla.dataset_id")}>
                      <select className="orbit-input" disabled={ro} value={s.sla.dataset_id ?? ""} onChange={(e) => up("sla", { dataset_id: e.target.value || null })}>
                        <option value="">first output of the pipeline</option>{inputs.map((ds) => <option key={ds.id} value={ds.id}>{ds.qualified_name}</option>)}
                      </select>
                    </Field>
                    <div className="grid grid-cols-2 gap-2">
                      <Field label="Updated every day before (UTC)" error={err("sla.deadline")}><input className="orbit-input font-mono" disabled={ro || !!s.sla.max_age_hours} value={s.sla.deadline} onChange={(e) => up("sla", { deadline: e.target.value })} /></Field>
                      <Field label="…or at most N hours old"><input type="number" className="orbit-input" disabled={ro} value={s.sla.max_age_hours ?? ""} onChange={(e) => up("sla", { max_age_hours: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
                    </div>
                    <p className="text-[11px] text-orbit-subtle">Add “SLA missed” to the events above to be alerted (once per late update).</p>
                  </>
                )}
              </div>
            </div>
          )}

          {tab === "params" && (
            <div className="space-y-3">
              <p className="text-[11px] text-orbit-muted">Use <span className="font-mono">{"{{name}}"}</span> in filters, SQL, derived columns, file names and e-mail subjects. Built-in: <span className="font-mono">run_date, start_date, end_date, run_id</span>. Values become typed SQL literals (never raw SQL).</p>
              <ListEditor<ParamDef> disabled={ro} items={s.params} empty={{ name: "", type: "string", default: "", description: "" }} addLabel="Add a parameter"
                onChange={(v) => up("params", v)} render={(p, upd, i) => (
                  <div className="grid grid-cols-[1fr_100px_1fr] gap-1.5">
                    <input className={cn(small, "font-mono", err(`params.${i}.name`) && "!border-orbit-red")} placeholder="name" disabled={ro} value={p.name} onChange={(e) => upd({ name: e.target.value })} />
                    <select className={small} disabled={ro} value={p.type} onChange={(e) => upd({ type: e.target.value as ParamDef["type"] })}>{["string", "integer", "number", "date", "boolean"].map((x) => <option key={x}>{x}</option>)}</select>
                    <input className={small} placeholder="default" disabled={ro} value={p.default == null ? "" : String(p.default)} onChange={(e) => upd({ default: e.target.value })} />
                  </div>
                )} />
            </div>
          )}

          {tab === "backfill" && (
            <div className="space-y-3">
              <p className="text-[11px] text-orbit-muted">Replays the pipeline over a past period: one run per day / week / month, one after the other, with <span className="font-mono">run_date</span>, <span className="font-mono">start_date</span> and <span className="font-mono">end_date</span> set to each period.</p>
              {data.backfill ? (
                <InlineAlert tone="cyan">
                  Backfill in progress: {data.backfill.done} done, {data.backfill.failed} failed, {data.backfill.queue.length} waiting{data.backfill.current ? ` · current ${data.backfill.current}` : ""}.
                  {can("pipelines.run") && <Button size="sm" variant="danger" icon={Square} className="ml-2" onClick={stopBackfill}>Cancel</Button>}
                </InlineAlert>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-2">
                    <Field label="From" error={err("start")}><input type="date" className="orbit-input" value={bf.start} onChange={(e) => setBf({ ...bf, start: e.target.value })} /></Field>
                    <Field label="To" error={err("end")}><input type="date" className="orbit-input" value={bf.end} onChange={(e) => setBf({ ...bf, end: e.target.value })} /></Field>
                    <Field label="One run per" error={err("step")}><select className="orbit-input" value={bf.step} onChange={(e) => setBf({ ...bf, step: e.target.value })}><option value="day">day</option><option value="week">week</option><option value="month">month</option></select></Field>
                  </div>
                  <Check disabled={false} label="Continue when a run fails" value={bf.continue_on_failure} onChange={(v) => setBf({ ...bf, continue_on_failure: v })} />
                  {can("pipelines.run") && <Button variant="primary" icon={Play} loading={busy} disabled={!bf.start || !bf.end} onClick={startBackfill}>Start the backfill</Button>}
                </>
              )}
            </div>
          )}

          {tab === "events" && (
            <div className="space-y-1">
              {data.events.length === 0 && <div className="text-[11px] text-orbit-subtle">Nothing yet.</div>}
              {data.events.map((e, i) => (
                <div key={i} className="flex gap-2 text-[11px] py-1 border-b border-orbit-border/50">
                  <span className="text-orbit-subtle w-24 shrink-0" title={dateTime(e.at)}>{ago(e.at)}</span>
                  <Badge tone={e.kind === "skipped" ? "amber" : e.kind === "started" ? "green" : e.kind === "retry" || e.kind === "sla" ? "red" : "gray"} className="text-[10px] shrink-0">{e.kind}</Badge>
                  <span className="text-orbit-text">{e.message}</span>
                </div>
              ))}
            </div>
          )}

          {!ro && !["backfill", "events"].includes(tab) && (
            <div className="flex justify-end gap-2 pt-2 border-t border-orbit-border">
              <Button variant="ghost" onClick={() => data && setS(JSON.parse(JSON.stringify(data.settings)))}>Reset</Button>
              <Button variant="primary" icon={Save} loading={busy} onClick={save}>Save automation</Button>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}

/** Values for the declared parameters before a manual run. */
export function RunParamsModalBody({ defs, values, onChange }: { defs: ParamDef[]; values: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const all: ParamDef[] = [{ name: "run_date", type: "date", default: "", description: "date of the run (today if empty)" }, ...defs];
  return (
    <div className="space-y-2">
      {all.map((p) => (
        <Field key={p.name} label={`${p.name} (${p.type})`} hint={p.description || (p.default != null && p.default !== "" ? `default: ${p.default}` : undefined)}>
          <input type={p.type === "date" ? "date" : p.type === "integer" || p.type === "number" ? "number" : "text"} className="orbit-input"
            value={values[p.name] ?? (p.default == null ? "" : String(p.default))} onChange={(e) => onChange({ ...values, [p.name]: e.target.value })} />
        </Field>
      ))}
    </div>
  );
}
