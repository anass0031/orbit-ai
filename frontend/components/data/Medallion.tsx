"use client";
// Medallion tools of the catalogue: Bronze → Silver template, lineage and star schema view.
import { ArrowRight, Boxes, GitFork, Wand2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, CardHeader, ErrorState, Field, LoadingState, Modal } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Dataset, DatasetLineage, DatasetModel } from "@/lib/types";

/** Business columns of a dataset (technical columns start with an underscore). */
export function KeyPicker({ ds, value, onChange }: { ds: Dataset; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="max-h-44 overflow-y-auto rounded-lg bg-orbit-bg border border-orbit-border p-2 space-y-1">
      {ds.schema.filter((c) => !c.name.startsWith("_")).map((c) => (
        <label key={c.name} className="flex items-center gap-2 text-[11px] font-mono text-white">
          <input type="checkbox" checked={value.includes(c.name)} onChange={(e) => onChange(e.target.checked ? [...value, c.name] : value.filter((x) => x !== c.name))} />
          {c.name} <span className="text-orbit-subtle">{c.type}</span>
        </label>
      ))}
    </div>
  );
}

export function BronzeToSilverButton({ ds }: { ds: Dataset }) {
  const router = useRouter();
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [keys, setKeys] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (ds.layer !== "bronze" || !can("pipelines.write")) return null;
  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      const p = await api<{ id: string }>("/pipelines/templates/bronze-to-silver", { method: "POST", body: { dataset_id: ds.id, keys } });
      router.push(`/pipelines/${p.id}`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <>
      <Button size="sm" variant="primary" icon={Wand2} onClick={() => setOpen(true)}>Clean to Silver</Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Bronze → Silver pipeline" subtitle="Standardise, correct types, remove duplicates, fill missing values, check quality, write to Silver. You can edit it afterwards."
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={!keys.length} onClick={create}>Create pipeline</Button></>}>
        <Field label="Key column(s)" hint="The columns that identify one row." error={err ?? undefined}><KeyPicker ds={ds} value={keys} onChange={setKeys} /></Field>
      </Modal>
    </>
  );
}

function DatasetLink({ id, name, version }: { id: string; name: string; version: number }) {
  return <Link href={`/datasets/${id}`} className="block font-mono text-xs text-white hover:text-orbit-purpleLight py-1">{name} <span className="text-orbit-subtle">v{version}</span></Link>;
}

export function LineagePanel({ ds, version }: { ds: Dataset; version: number }) {
  const { data, error } = useSWR<DatasetLineage>(`/datasets/${ds.id}/lineage?version=${version}`);
  if (error) return <ErrorState error={error} />;
  if (!data) return <LoadingState />;
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><GitFork className="w-4 h-4 text-orbit-purple" />Lineage</span>} subtitle={`Version ${data.version ?? "—"} of ${data.dataset}`} />
      <div className="grid md:grid-cols-[1fr_auto_1fr] gap-4 items-start">
        <div>
          <div className="orbit-label mb-1">Built from</div>
          {data.sources.map((s) => <DatasetLink key={s.dataset_id + s.version} id={s.dataset_id} name={s.qualified_name} version={s.version} />)}
          {data.pipeline_run_id && <Link href={`/runs/${data.pipeline_run_id}`} className="block text-xs text-orbit-purpleLight hover:underline py-1">Pipeline run</Link>}
          {data.source_import_id && <Link href={`/imports/${data.source_import_id}`} className="block text-xs text-orbit-purpleLight hover:underline py-1">File import</Link>}
          {!data.sources.length && !data.pipeline_run_id && !data.source_import_id && <div className="text-xs text-orbit-subtle">No recorded source.</div>}
        </div>
        <ArrowRight className="hidden md:block w-4 h-4 text-orbit-subtle mt-6" />
        <div>
          <div className="orbit-label mb-1">Used to build</div>
          {data.produced.map((p) => <DatasetLink key={p.dataset_id} id={p.dataset_id} name={p.qualified_name} version={p.version} />)}
          {!data.produced.length && <div className="text-xs text-orbit-subtle">No dataset is built from this one yet.</div>}
        </div>
      </div>
    </Card>
  );
}

export function ModelPanel({ ds }: { ds: Dataset }) {
  const { data, error } = useSWR<DatasetModel>(`/datasets/${ds.id}/model`);
  if (error) return <ErrorState error={error} />;
  if (!data) return <LoadingState />;
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Boxes className="w-4 h-4 text-orbit-cyan" />Data model</span>}
        subtitle={data.role ? "Place of this table in the star schema" : "This dataset has no role in the star schema"} right={data.role ? <Badge tone="cyan">{data.role}</Badge> : undefined} />
      {data.primary_key.length > 0 && <div className="text-xs text-orbit-muted mb-3">Primary key: <span className="font-mono text-white">{data.primary_key.join(", ")}</span></div>}
      {data.relationships.length > 0 && (
        <table className="orbit-table w-full">
          <thead><tr><th>Foreign key</th><th>Dimension</th><th>Dimension key</th></tr></thead>
          <tbody>{data.relationships.map((r) => (
            <tr key={r.column}><td className="font-mono">{r.column}</td>
              <td>{r.dimension_id ? <Link className="font-mono text-white hover:text-orbit-purpleLight" href={`/datasets/${r.dimension_id}`}>{r.dimension}</Link> : <span className="font-mono">{r.dimension}</span>}</td>
              <td className="font-mono">{r.key ?? "—"}</td></tr>
          ))}</tbody>
        </table>
      )}
      {data.used_by.length > 0 && (
        <div className="mt-2">
          <div className="orbit-label mb-1">Facts using this dimension</div>
          {data.used_by.map((f) => <Link key={f.fact_id + f.column} href={`/datasets/${f.fact_id}`} className="block font-mono text-xs text-white hover:text-orbit-purpleLight py-1">{f.fact} <span className="text-orbit-subtle">by {f.column}</span></Link>)}
        </div>
      )}
    </Card>
  );
}
