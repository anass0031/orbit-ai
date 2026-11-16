"use client";

import { FileUp, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { useRef, useState } from "react";
import useSWR from "swr";
import {
  Badge, Button, Card, CardHeader, EmptyState, ErrorState, Field, FilterChips, InlineAlert, LoadingState,
  PageContainer, Pagination, StatusPill, cn,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, bytes, compact } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { FileImport, ImportSummary, Page } from "@/lib/types";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.xlsm,.json,.jsonl,.ndjson";
const LIMIT = 20;

function UploadCard() {
  const router = useRouter();
  const notify = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [delimiter, setDelimiter] = useState("");
  const [hasHeader, setHasHeader] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pick = (f: File | undefined | null) => {
    setError(null);
    if (f) setFile(f);
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    if (delimiter) form.append("delimiter", delimiter === "tab" ? "\t" : delimiter);
    form.append("has_header", String(hasHeader));
    try {
      const imp = await api<FileImport>("/imports", { method: "POST", body: form });
      if (imp.status === "failed") notify(`Schema discovery failed: ${imp.error_message}`, "error");
      else notify(`${imp.filename}: ${imp.schema.length} columns, ${imp.row_count} rows discovered`);
      router.push(`/imports/${imp.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const isCsv = !!file && /\.(csv|tsv|txt)$/i.test(file.name);

  return (
    <Card>
      <CardHeader title="Upload a file" subtitle="The file is stored unchanged in MinIO, then its schema is discovered automatically." />
      <div
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files?.[0]); }}
        className={cn(
          "cursor-pointer rounded-xl border-2 border-dashed p-8 flex flex-col items-center justify-center text-center transition",
          drag ? "border-orbit-purple bg-orbit-purple/10" : "border-orbit-borderStrong hover:border-orbit-purple/60 bg-orbit-bg/40",
        )}
      >
        <UploadCloud className="w-8 h-8 text-orbit-purple mb-2" />
        {file ? (
          <>
            <div className="text-sm font-semibold text-white">{file.name}</div>
            <div className="text-xs text-orbit-muted">{bytes(file.size)} · click to choose another file</div>
          </>
        ) : (
          <>
            <div className="text-sm font-semibold text-white">Drop a file here or click to browse</div>
            <div className="text-xs text-orbit-muted mt-1">CSV / TSV, Excel (.xlsx), JSON or JSON Lines</div>
          </>
        )}
        <input ref={input} type="file" accept={ACCEPT} className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      </div>

      {isCsv && (
        <div className="grid grid-cols-2 gap-3 mt-4">
          <Field label="Delimiter" hint="Detected automatically when left on Auto">
            <select className="orbit-input" value={delimiter} onChange={(e) => setDelimiter(e.target.value)}>
              <option value="">Auto-detect</option>
              <option value=",">Comma ( , )</option>
              <option value=";">Semicolon ( ; )</option>
              <option value="tab">Tab</option>
              <option value="|">Pipe ( | )</option>
            </select>
          </Field>
          <Field label="Header">
            <label className="flex items-center gap-2 text-xs text-white py-2">
              <input type="checkbox" checked={hasHeader} onChange={(e) => setHasHeader(e.target.checked)} />
              First row contains column names
            </label>
          </Field>
        </div>
      )}
      {error && <InlineAlert tone="red" className="mt-4">{error}</InlineAlert>}
      <div className="flex justify-end mt-4">
        <Button variant="primary" icon={FileUp} disabled={!file} loading={busy} onClick={submit}>Upload & discover schema</Button>
      </div>
    </Card>
  );
}

export default function ImportsPage() {
  const { can } = useAuth();
  const [status, setStatus] = useState("all");
  const [offset, setOffset] = useState(0);
  const query = `/imports?limit=${LIMIT}&offset=${offset}${status !== "all" ? `&status=${status}` : ""}`;
  const { data, error, mutate } = useSWR<Page<ImportSummary>>(query);

  return (
    <PageContainer>
      {can("imports.write") ? <UploadCard /> : (
        <InlineAlert tone="cyan">Your role can browse the import history. Uploading files is reserved to Data Engineers and Administrators.</InlineAlert>
      )}

      <Card>
        <CardHeader title="Import history"
          right={<FilterChips value={status} onChange={(v) => { setStatus(v); setOffset(0); }} options={[
            { value: "all", label: "All" }, { value: "discovered", label: "Discovered" },
            { value: "ingested", label: "In Bronze" }, { value: "failed", label: "Failed" },
          ]} />} />
        {error ? <ErrorState error={error} onRetry={() => mutate()} /> : !data ? <LoadingState /> : data.items.length === 0 ? (
          <EmptyState title="No imports yet" description="Uploaded files will appear here." />
        ) : (
          <>
            <table className="orbit-table w-full">
              <thead><tr><th>File</th><th>Format</th><th>Status</th><th className="text-right">Rows</th><th className="text-right">Size</th><th>Uploaded</th></tr></thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.id}>
                    <td>
                      <Link href={`/imports/${i.id}`} className="text-white hover:text-orbit-purpleLight font-medium">{i.filename}</Link>
                      {i.status === "failed" && <div className="text-[11px] text-orbit-redLight truncate max-w-md">{i.error_message}</div>}
                    </td>
                    <td><Badge>{i.file_format.toUpperCase()}</Badge></td>
                    <td><StatusPill status={i.status} /></td>
                    <td className="text-right font-mono">
                      {compact(i.row_count)}
                      {i.quarantined_count > 0 && <span className="text-orbit-amber" title="Rejected records"> +{i.quarantined_count}⚠</span>}
                    </td>
                    <td className="text-right font-mono text-orbit-muted">{bytes(i.size_bytes)}</td>
                    <td className="text-orbit-muted">{ago(i.created_at)}</td>
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
