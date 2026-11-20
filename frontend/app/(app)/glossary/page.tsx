"use client";

import { BookOpen, Pencil, Plus, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import React, { useState } from "react";
import useSWR from "swr";
import { ListEditor, small } from "@/components/pipeline/fields";
import { Badge, Button, Card, ConfirmModal, EmptyState, ErrorState, Field, LoadingState, Modal, PageContainer } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { Dataset, GlossaryTerm } from "@/lib/types";

type Form = { id?: string; name: string; definition: string; synonyms: string; links: { dataset: string; column?: string }[] };

export default function GlossaryPage() {
  const { can } = useAuth();
  const notify = useToast();
  const [q, setQ] = useState("");
  const { data, error, mutate } = useSWR<GlossaryTerm[]>(`/glossary${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  const { data: datasets } = useSWR<Dataset[]>("/datasets");
  const [form, setForm] = useState<Form | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<GlossaryTerm | null>(null);
  const edit = can("catalog.document");
  const dsByName = Object.fromEntries((datasets || []).map((d) => [d.qualified_name, d]));

  const save = async () => {
    if (!form) return;
    setBusy(true);
    setErrors({});
    try {
      const body = { name: form.name, definition: form.definition, synonyms: form.synonyms.split(",").map((s) => s.trim()).filter(Boolean),
        links: form.links.filter((l) => l.dataset).map((l) => ({ dataset: l.dataset, ...(l.column ? { column: l.column } : {}) })) };
      await api(form.id ? `/glossary/${form.id}` : "/glossary", { method: form.id ? "PUT" : "POST", body });
      notify("Term saved");
      setForm(null);
      mutate();
    } catch (e) {
      if (e instanceof ApiError) setErrors(e.fieldErrors);
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!confirm) return;
    await api(`/glossary/${confirm.id}`, { method: "DELETE" }).catch(() => undefined);
    setConfirm(null);
    mutate();
  };

  return (
    <PageContainer>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="relative w-80">
          <Search className="w-3.5 h-3.5 text-orbit-muted absolute left-3 top-1/2 -translate-y-1/2" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search terms and definitions" className="orbit-input !pl-8" />
        </div>
        {edit && <Button variant="primary" icon={Plus} onClick={() => setForm({ name: "", definition: "", synonyms: "", links: [] })}>New term</Button>}
      </div>
      {error ? <ErrorState error={error} /> : !data ? <LoadingState /> : data.length === 0 ? (
        <Card><EmptyState icon={BookOpen} title={q ? "No term matches" : "The glossary is empty"} description="Define business terms (e.g. “Active customer”, “Net revenue”) and link them to datasets: everyone shares one meaning." /></Card>
      ) : (
        <div className="grid md:grid-cols-2 gap-3">
          {data.map((t) => (
            <Card key={t.id}>
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-orbit-purple" /><span className="text-sm font-bold text-white">{t.name}</span>
                {edit && <span className="ml-auto flex gap-1">
                  <button onClick={() => setForm({ id: t.id, name: t.name, definition: t.definition, synonyms: t.synonyms.join(", "), links: t.links })} className="text-orbit-muted hover:text-white" aria-label="Edit"><Pencil className="w-3.5 h-3.5" /></button>
                  <button onClick={() => setConfirm(t)} className="text-orbit-muted hover:text-orbit-redLight" aria-label="Delete"><Trash2 className="w-3.5 h-3.5" /></button>
                </span>}
              </div>
              <p className="text-xs text-orbit-text mt-2 whitespace-pre-wrap">{t.definition}</p>
              {t.synonyms.length > 0 && <div className="text-[11px] text-orbit-muted mt-2">Also called: {t.synonyms.join(", ")}</div>}
              {t.links.length > 0 && <div className="flex flex-wrap gap-1.5 mt-2">{t.links.map((l) => {
                const d = dsByName[l.dataset];
                const label = `${l.dataset}${l.column ? `.${l.column}` : ""}`;
                return d ? <Link key={label} href={`/datasets/${d.id}`}><Badge tone="cyan" className="font-mono text-[10px]">{label}</Badge></Link> : <Badge key={label} className="font-mono text-[10px]">{label}</Badge>;
              })}</div>}
              <div className="text-[10px] text-orbit-subtle mt-2">updated {ago(t.updated_at)}</div>
            </Card>
          ))}
        </div>
      )}
      <Modal open={!!form} onClose={() => setForm(null)} width="max-w-2xl" title={form?.id ? "Edit term" : "New term"}
        footer={<><Button variant="ghost" onClick={() => setForm(null)}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save</Button></>}>
        {form && (
          <div className="space-y-3">
            <Field label="Term" error={errors.name}><input className="orbit-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Net revenue" /></Field>
            <Field label="Definition" error={errors.definition} hint="Say how it is computed."><textarea rows={4} className="orbit-input" value={form.definition} onChange={(e) => setForm({ ...form, definition: e.target.value })} /></Field>
            <Field label="Synonyms (comma separated)"><input className="orbit-input" value={form.synonyms} onChange={(e) => setForm({ ...form, synonyms: e.target.value })} placeholder="turnover, sales" /></Field>
            <Field label="Linked datasets / columns" error={errors.links}>
              <ListEditor disabled={false} items={form.links} empty={{ dataset: "", column: "" }} addLabel="Link a dataset" onChange={(v) => setForm({ ...form, links: v })}
                render={(l, up) => (
                  <div className="grid grid-cols-2 gap-1.5">
                    <select className={small} value={l.dataset} onChange={(e) => up({ dataset: e.target.value, column: "" })}>
                      <option value="">dataset…</option>{datasets?.map((d) => <option key={d.id} value={d.qualified_name}>{d.qualified_name}</option>)}
                    </select>
                    <select className={small} value={l.column || ""} onChange={(e) => up({ column: e.target.value })}>
                      <option value="">(whole dataset)</option>{(dsByName[l.dataset]?.schema || []).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                    </select>
                  </div>
                )} />
            </Field>
          </div>
        )}
      </Modal>
      <ConfirmModal open={!!confirm} onClose={() => setConfirm(null)} onConfirm={remove} danger confirmLabel="Delete" title="Delete this term?" message={<>“{confirm?.name}” is removed from the glossary.</>} />
    </PageContainer>
  );
}
