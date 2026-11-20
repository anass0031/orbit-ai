"use client";

import { ArrowLeft, Database, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import React, { useEffect, useState } from "react";
import useSWR from "swr";
import { SchemaTable } from "@/components/data/SchemaTable";
import {
  Badge, Button, Card, CardHeader, DataTable, ErrorState, Field, InlineAlert, LoadingState, PageContainer, StatusPill,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { bytes, compact, dateTime } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { Dataset, DatasetVersion, FileImport } from "@/lib/types";

function OptionsCard({ imp, onDone }: { imp: FileImport; onDone: (i: FileImport) => void }) {
  const notify = useToast();
  const [delimiter, setDelimiter] = useState(imp.options.delimiter === "\t" ? "tab" : imp.options.delimiter || "");
  const [hasHeader, setHasHeader] = useState(imp.options.has_header ?? true);
  const [sheet, setSheet] = useState(imp.options.sheet_name || imp.sheets[0] || "");
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const body = imp.file_format === "excel"
        ? { sheet_name: sheet, has_header: hasHeader }
        : imp.sheets.length > 1 ? { sheet_name: sheet } : imp.file_format === "csv"
          ? { delimiter: delimiter === "tab" ? "\t" : delimiter || null, has_header: hasHeader }
          : {};
      const next = await api<FileImport>(`/imports/${imp.id}/discover`, { method: "POST", body });
      onDone(next);
      notify(next.status === "failed" ? `Discovery failed: ${next.error_message}` : "Schema discovered again", next.status === "failed" ? "error" : "success");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  if (["json", "xml", "yaml"].includes(imp.file_format) && imp.sheets.length < 2) return null;
  return (
    <Card>
      <CardHeader title="Reading options" subtitle="Change them and run discovery again if the result does not look right." />
      <div className="grid sm:grid-cols-3 gap-3 items-end">
        {imp.file_format === "csv" && (
          <Field label="Delimiter">
            <select className="orbit-input" value={delimiter} onChange={(e) => setDelimiter(e.target.value)}>
              <option value="">Auto-detect</option>
              <option value=",">Comma ( , )</option>
              <option value=";">Semicolon ( ; )</option>
              <option value="tab">Tab</option>
              <option value="|">Pipe ( | )</option>
            </select>
          </Field>
        )}
        {imp.sheets.length > 0 && (
          <Field label={imp.file_format === "excel" ? "Sheet" : "Entity"}>
            <select className="orbit-input" value={sheet} onChange={(e) => setSheet(e.target.value)}>
              {imp.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
        )}
        <label className="flex items-center gap-2 text-xs text-white py-2">
          <input type="checkbox" checked={hasHeader} onChange={(e) => setHasHeader(e.target.checked)} />
          First row contains column names
        </label>
        <Button icon={RefreshCw} loading={busy} onClick={run}>Run discovery again</Button>
      </div>
    </Card>
  );
}

function IngestCard({ imp, onDone }: { imp: FileImport; onDone: () => void }) {
  const notify = useToast();
  const [name, setName] = useState(imp.suggested_dataset_name || "");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: existing } = useSWR<Dataset[]>("/datasets?layer=bronze");
  const target = existing?.find((d) => d.name === name);

  useEffect(() => setName(imp.suggested_dataset_name || ""), [imp.suggested_dataset_name]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ dataset: Dataset; version: DatasetVersion }>(`/imports/${imp.id}/ingest`, {
        method: "POST", body: { dataset_name: name, description: description || null },
      });
      notify(`${compact(r.version.row_count)} rows written to ${r.dataset.qualified_name} (version ${r.version.version})`);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.fieldErrors.dataset_name || e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="border-orbit-purple/40">
      <CardHeader title="Load into the Bronze layer"
        subtitle="Writes the typed data (+ _ingested_at, _source_file, _import_id) as a Parquet file in MinIO." />
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Dataset name" hint={target
          ? `bronze.${name} already exists (v${target.current_version}) — this load creates version ${target.current_version + 1}.`
          : "Lowercase letters, digits and underscores; starts with a letter."}>
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-orbit-subtle font-mono">bronze.</span>
            <input className="orbit-input font-mono" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} />
          </div>
        </Field>
        <Field label="Description (optional)">
          <input className="orbit-input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </div>
      {error && <InlineAlert tone="red" className="mt-3">{error}</InlineAlert>}
      <div className="flex justify-end mt-4">
        <Button variant="primary" icon={Database} loading={busy} disabled={!name} onClick={submit}>Load into Bronze</Button>
      </div>
    </Card>
  );
}

export default function ImportDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: imp, error, mutate } = useSWR<FileImport>(`/imports/${id}`);

  if (error) return <PageContainer><ErrorState error={error} onRetry={() => mutate()} /></PageContainer>;
  if (!imp) return <LoadingState />;
  const columns = imp.schema.map((c) => c.name);

  return (
    <PageContainer>
      <Link href="/imports" className="inline-flex items-center gap-1 text-xs text-orbit-muted hover:text-white">
        <ArrowLeft className="w-3.5 h-3.5" /> All imports
      </Link>

      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-white">{imp.filename}</h2>
              <Badge>{imp.file_format.toUpperCase()}</Badge>
              <StatusPill status={imp.status} />
            </div>
            <div className="text-xs text-orbit-muted mt-1">
              {bytes(imp.size_bytes)} · uploaded {dateTime(imp.created_at)} · {imp.schema.length} columns · {compact(imp.row_count)} rows
              {imp.quarantined_count > 0 && <> · <span className="text-orbit-amber">{imp.quarantined_count} rejected record(s)</span></>}
            </div>
            <div className="text-[11px] text-orbit-subtle font-mono mt-1 break-all">raw file: {imp.raw_location} · sha256 {imp.checksum_sha256.slice(0, 16)}…</div>
          </div>
          {imp.status === "ingested" && imp.dataset_id && (
            <Link href={`/datasets/${imp.dataset_id}`}><Button variant="primary" icon={Database}>Open the Bronze dataset</Button></Link>
          )}
        </div>
      </Card>

      {imp.status === "failed" && <InlineAlert tone="red">Schema discovery failed: {imp.error_message}</InlineAlert>}
      {can("imports.write") && imp.status !== "ingested" && <OptionsCard imp={imp} onDone={(i) => mutate(i, false)} />}

      {imp.status !== "failed" && (
        <>
          {imp.warnings.length > 0 && (
            <Card>
              <CardHeader title={`Warnings (${imp.warnings.length})`} subtitle="Nothing was changed or dropped silently — review these before loading." />
              <ul className="space-y-1.5">
                {imp.warnings.map((w, i) => <li key={i} className="text-xs text-orbit-amber">• {w}</li>)}
              </ul>
            </Card>
          )}
          <Card>
            <CardHeader title="Discovered schema"
              subtitle={imp.inferred_primary_key.length ? `Likely primary key: ${imp.inferred_primary_key.join(", ")}` : "No obvious primary key"} />
            <SchemaTable schema={imp.schema} primaryKey={imp.inferred_primary_key} />
          </Card>
          <Card>
            <CardHeader title="Sample" subtitle={`First ${imp.sample_rows.length} rows, with the inferred types applied`} />
            <DataTable columns={columns} rows={imp.sample_rows} maxHeight="420px" />
          </Card>
          {can("imports.write") && imp.status === "discovered" && <IngestCard imp={imp} onDone={() => mutate()} />}
        </>
      )}
    </PageContainer>
  );
}
