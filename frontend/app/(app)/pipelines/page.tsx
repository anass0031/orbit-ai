"use client";

import { GitBranch, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import React, { useState } from "react";
import useSWR from "swr";
import { KeyPicker } from "@/components/data/Medallion";
import { Button, Card, EmptyState, ErrorState, Field, LoadingState, Modal, PageContainer, StatusPill } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import type { Dataset, Pipeline } from "@/lib/types";

export default function PipelinesPage() {
  const router = useRouter();
  const { can } = useAuth();
  const { data, error, mutate } = useSWR<Pipeline[]>("/pipelines");
  const { data: datasets } = useSWR<Dataset[]>("/datasets");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", start: "", keys: [] as string[] });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    setErrors({});
    try {
      const ds = datasets?.find((d) => d.id === form.start);
      if (ds?.layer === "bronze") {  // Bronze → Silver template: clean, deduplicate, check quality, write to Silver
        const t = await api<{ id: string }>("/pipelines/templates/bronze-to-silver", { method: "POST", body: { dataset_id: ds.id, keys: form.keys, name: form.name.trim() } });
        router.push(`/pipelines/${t.id}`);
        return;
      }
      // Starting from a Silver or Gold dataset pre-wires: input → Gold output.
      const graph = ds ? {
        nodes: [
          { id: "input", type: "dataset_input", label: ds.qualified_name, position: { x: 60, y: 160 }, config: { dataset_id: ds.id } },
          { id: "output", type: "output", label: "Gold output", position: { x: 560, y: 160 },
            config: { layer: "gold", name: `${ds.name}_summary` } },
        ],
        edges: [{ id: "e1", source: "input", target: "output" }],
      } : undefined;
      const p = await api<{ id: string }>("/pipelines", { method: "POST", body: { name: form.name.trim(), description: form.description || null, graph } });
      router.push(`/pipelines/${p.id}`);
    } catch (e) {
      if (e instanceof ApiError) setErrors({ ...e.fieldErrors, _: e.message });
    } finally {
      setBusy(false);
    }
  };

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  return (
    <PageContainer>
      <Card padded={false}>
        <div className="p-4 border-b border-orbit-border flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">Pipelines</h2>
            <p className="text-xs text-orbit-muted">Visual graphs executed by DuckDB. Each run uses an immutable published version and writes new Silver / Gold versions.</p>
          </div>
          {can("pipelines.write") && <Button variant="primary" icon={Plus} onClick={() => setOpen(true)}>New pipeline</Button>}
        </div>
        {!data ? <LoadingState /> : data.length === 0 ? (
          <EmptyState icon={GitBranch} title="No pipelines yet"
            description="Build a pipeline that cleans a Bronze dataset into Silver and aggregates it into Gold." />
        ) : (
          <table className="orbit-table w-full">
            <thead><tr><th>Pipeline</th><th>Status</th><th>Version</th><th>Last run</th><th>Next run</th><th>Updated</th></tr></thead>
            <tbody>
              {data.map((p) => (
                <tr key={p.id} className="cursor-pointer" onClick={() => router.push(`/pipelines/${p.id}`)}>
                  <td>
                    <span className="flex items-center gap-3">
                      <span className="w-8 h-8 rounded-lg bg-orbit-purple/15 text-orbit-purple flex items-center justify-center"><GitBranch className="w-4 h-4" /></span>
                      <span><span className="text-white font-semibold block">{p.name}</span><span className="text-[11px] text-orbit-muted">{p.description || "—"}</span></span>
                    </span>
                  </td>
                  <td><StatusPill status={p.status} /></td>
                  <td className="font-mono text-orbit-nav">{p.current_version ? `v${p.current_version}` : "—"}</td>
                  <td>{p.last_run_status
                    ? <span className="flex items-center gap-2"><StatusPill status={p.last_run_status} /><span className="text-orbit-muted">{ago(p.last_run_at)}</span></span>
                    : <span className="text-orbit-subtle">never</span>}</td>
                  <td className="text-orbit-nav text-xs">{p.next_run_at ? new Date(p.next_run_at).toISOString().slice(0, 16).replace("T", " ") + " UTC" : "—"}</td>
                  <td className="text-orbit-nav">{ago(p.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Modal open={open} onClose={() => setOpen(false)} title="New pipeline" subtitle="Opens in the visual editor."
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!form.name.trim() || (datasets?.find((d) => d.id === form.start)?.layer === "bronze" && !form.keys.length)} onClick={create}>Create & open editor</Button></>}>
        {errors._ && <div className="text-xs text-orbit-redLight">{errors._}</div>}
        <Field label="Name" error={errors.name}>
          <input className="orbit-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Orders medallion" />
        </Field>
        <Field label="Description (optional)">
          <input className="orbit-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </Field>
        <Field label="Start from a dataset (optional)" hint="A Bronze dataset creates the Bronze → Silver template; a Silver or Gold dataset pre-wires a Gold output.">
          <select className="orbit-input" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value, keys: [] })}>
            <option value="">Empty canvas</option>
            {datasets?.map((d) => <option key={d.id} value={d.id}>{d.qualified_name}</option>)}
          </select>
        </Field>
        {datasets?.find((d) => d.id === form.start)?.layer === "bronze" && (
          <Field label="Key column(s)" hint="The columns that identify one row."><KeyPicker ds={datasets.find((d) => d.id === form.start)!} value={form.keys} onChange={(keys) => setForm({ ...form, keys })} /></Field>
        )}
      </Modal>
    </PageContainer>
  );
}
