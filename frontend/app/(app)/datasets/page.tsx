"use client";

import { Database, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageContainer } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { ago, bytes, compact } from "@/lib/format";
import type { Dataset } from "@/lib/types";

export default function DatasetsPage() {
  const { can } = useAuth();
  const [q, setQ] = useState("");
  const { data, error, mutate } = useSWR<Dataset[]>(`/datasets?layer=bronze${q ? `&q=${encodeURIComponent(q)}` : ""}`);

  return (
    <PageContainer>
      <div className="flex items-center gap-2 max-w-sm">
        <Search className="w-4 h-4 text-orbit-subtle" />
        <input className="orbit-input" placeholder="Search a dataset…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {error ? <ErrorState error={error} onRetry={() => mutate()} /> : !data ? <LoadingState /> : data.length === 0 ? (
        <Card>
          <EmptyState icon={Database} title="The Bronze layer is empty"
            description="Import a file and load it into Bronze to create the first dataset."
            action={can("imports.write") ? <Link href="/imports"><Button variant="primary">Import a file</Button></Link> : undefined} />
        </Card>
      ) : (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {data.map((d) => (
            <Link key={d.id} href={`/datasets/${d.id}`}>
              <Card className="h-full hover:bg-orbit-cardHover hover:border-orbit-purple/40 transition">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-sm text-white truncate">{d.qualified_name}</div>
                  <Badge tone="amber">Bronze</Badge>
                </div>
                {d.description && <div className="text-xs text-orbit-muted mt-1 line-clamp-2">{d.description}</div>}
                <div className="grid grid-cols-3 gap-2 mt-4 text-[11px]">
                  <div><div className="orbit-label">Rows</div><div className="text-white font-mono">{compact(d.row_count)}</div></div>
                  <div><div className="orbit-label">Columns</div><div className="text-white font-mono">{d.schema.filter((c) => !c.metadata).length}</div></div>
                  <div><div className="orbit-label">Version</div><div className="text-white font-mono">v{d.current_version}</div></div>
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
