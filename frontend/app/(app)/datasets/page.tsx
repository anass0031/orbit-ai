"use client";

import { BookOpen, Database, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { scoreTone } from "@/components/data/RunParts";
import { Badge, Button, Card, EmptyState, ErrorState, FilterChips, LoadingState, PageContainer, cn } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ago, bytes, compact } from "@/lib/format";
import type { Dataset, SearchResult } from "@/lib/types";

const LAYER_TONE = { bronze: "amber", silver: "gray", gold: "cyan" } as const;

export default function DatasetsPage() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const [layer, setLayer] = useState("all");
  const { data: all, error, mutate } = useSWR<Dataset[]>(`/datasets${layer !== "all" ? `?layer=${layer}` : ""}`);
  // search everywhere: names, descriptions, tags, columns and column descriptions, plus glossary terms
  const { data: found } = useSWR<SearchResult>(q.trim().length >= 2 ? `/datasets/search/all?q=${encodeURIComponent(q.trim())}` : null);
  const byId = Object.fromEntries((all || []).map((d) => [d.id, d]));
  const data = !all ? undefined : found && q.trim().length >= 2 ? found.datasets.map((r) => byId[r.id]).filter(Boolean) : all;
  const why = Object.fromEntries((found?.datasets || []).map((r) => [r.id, r]));

  return (
    <PageContainer>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <FilterChips value={layer} onChange={setLayer} options={[
          { value: "all", label: "All layers" }, { value: "bronze", label: "Bronze" }, { value: "silver", label: "Silver" }, { value: "gold", label: "Gold" },
        ]} />
        <div className="flex items-center gap-2 w-72">
          <Search className="w-4 h-4 text-orbit-subtle" />
          <input className="orbit-input" placeholder="Search names, descriptions, tags, columns…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {found && found.terms.length > 0 && q.trim().length >= 2 && (
        <Card padded={false} className="p-3">
          <div className="orbit-label mb-1 flex items-center gap-1.5"><BookOpen className="w-3.5 h-3.5" />Glossary</div>
          {found.terms.map((t) => <div key={t.id} className="text-xs"><Link href="/glossary" className="text-white font-semibold hover:underline">{t.name}</Link> <span className="text-orbit-muted">— {t.definition.slice(0, 160)}</span></div>)}
        </Card>
      )}
      {error ? <ErrorState error={error} onRetry={() => mutate()} /> : !data ? <LoadingState /> : data.length === 0 ? (
        <Card>
          <EmptyState icon={Database} title="No dataset here yet"
            description="Bronze datasets come from file imports; Silver and Gold datasets are written by pipelines."
            action={can("imports.write") ? <Link href="/imports"><Button variant="primary">Import a file</Button></Link> : undefined} />
        </Card>
      ) : (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.map((d) => (
            <Link key={d.id} href={`/datasets/${d.id}`}>
              <Card className="h-full hover:bg-orbit-cardHover hover:border-orbit-purple/40 transition">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-sm text-white truncate">{d.qualified_name}</div>
                  <Badge tone={LAYER_TONE[d.layer]}>{d.layer}</Badge>
                </div>
                {why[d.id] && <div className="text-[10px] text-orbit-cyan mt-1">matched in {why[d.id].matched.join(", ")}{why[d.id].columns.length ? ` · ${why[d.id].columns.slice(0, 4).join(", ")}` : ""}</div>}
                {(d.tags?.length ?? 0) > 0 && <div className="flex flex-wrap gap-1 mt-1">{d.tags!.map((t) => <Badge key={t} tone="purple" className="text-[10px]">#{t}</Badge>)}</div>}
                {d.description && <div className="text-xs text-orbit-muted mt-1 line-clamp-2">{d.description}</div>}
                <div className="grid grid-cols-4 gap-2 mt-4 text-[11px]">
                  <div><div className="orbit-label">Rows</div><div className="text-white font-mono">{compact(d.row_count)}</div></div>
                  <div><div className="orbit-label">Columns</div><div className="text-white font-mono">{d.schema.filter((c) => !c.metadata).length}</div></div>
                  <div><div className="orbit-label">Version</div><div className="text-white font-mono">v{d.current_version}</div></div>
                  <div><div className="orbit-label">Quality</div><div className={cn("font-mono font-semibold", scoreTone(d.quality_score))}>{d.quality_score ?? "—"}</div></div>
                </div>
                <div className="text-[11px] text-orbit-subtle mt-3">{bytes(d.size_bytes)} Parquet · updated {ago(d.last_updated_at)}</div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
