"use client";

import { CalendarClock, ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import React, { useMemo, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageContainer, StatusPill, cn } from "@/components/ui";
import { duration } from "@/lib/format";
import type { CalendarData } from "@/lib/types";

const DAY_MS = 86400000;
const utcDay = (d: Date) => new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
const key = (iso: string) => iso.slice(0, 10);
const hhmm = (iso: string) => new Date(iso).toISOString().slice(11, 16);

export default function CalendarPage() {
  const [offset, setOffset] = useState(0);
  const start = useMemo(() => new Date(utcDay(new Date()).getTime() - 3 * DAY_MS + offset * 7 * DAY_MS), [offset]);
  const end = new Date(start.getTime() + 7 * DAY_MS);
  const { data, error } = useSWR<CalendarData>(`/pipelines/calendar?start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(end.toISOString())}`,
    { refreshInterval: 30000 });
  const days = Array.from({ length: 7 }, (_, i) => new Date(start.getTime() + i * DAY_MS));
  const today = key(new Date().toISOString());

  return (
    <PageContainer>
      <div className="flex items-center justify-between">
        <div className="text-xs text-orbit-muted">Times in UTC · past runs come from the run history, upcoming runs from the schedules.</div>
        <div className="flex items-center gap-2">
          <Button size="sm" icon={ChevronLeft} onClick={() => setOffset((o) => o - 1)}>Previous week</Button>
          <Button size="sm" onClick={() => setOffset(0)}>Today</Button>
          <Button size="sm" onClick={() => setOffset((o) => o + 1)}>Next week <ChevronRight className="w-3.5 h-3.5" /></Button>
        </div>
      </div>
      {error ? <ErrorState error={error} /> : !data ? <LoadingState /> : data.runs.length === 0 && data.upcoming.length === 0 ? (
        <Card><EmptyState icon={CalendarClock} title="Nothing this week" description="Schedule a pipeline in its Automation panel, or run one: runs appear here." /></Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-7 gap-2">
          {days.map((d) => {
            const k = key(d.toISOString());
            const past = data.runs.filter((r) => key(r.queued_at) === k);
            const next = data.upcoming.filter((u) => key(u.at) === k);
            return (
              <Card key={k} padded={false} className={cn("p-2.5 min-h-[180px]", k === today && "border-orbit-purple/60")}>
                <div className="text-[11px] font-semibold text-white mb-2">{d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" })}{k === today && <Badge tone="purple" className="ml-1 text-[9px]">today</Badge>}</div>
                <div className="space-y-1">
                  {past.map((r) => (
                    <Link key={r.id} href={`/runs/${r.id}`} className="block p-1.5 rounded-md bg-orbit-bg border border-orbit-border hover:border-orbit-borderStrong">
                      <div className="flex items-center justify-between gap-1"><span className="text-[10px] font-mono text-orbit-muted">{hhmm(r.queued_at)}</span><StatusPill status={r.status} className="!text-[9px] !px-1.5" /></div>
                      <div className="text-[11px] text-white truncate">{r.pipeline}</div>
                      <div className="text-[10px] text-orbit-subtle">{r.trigger}{r.duration_ms ? ` · ${duration(r.duration_ms)}` : ""}{r.params?.run_date && r.trigger === "backfill" ? ` · ${String(r.params.run_date)}` : ""}</div>
                    </Link>
                  ))}
                  {next.slice(0, 30).map((u, i) => (
                    <Link key={u.pipeline_id + u.at + i} href={`/pipelines/${u.pipeline_id}`} className="block p-1.5 rounded-md border border-dashed border-orbit-purple/40 hover:bg-orbit-purple/5">
                      <div className="text-[10px] font-mono text-orbit-purpleLight">{hhmm(u.at)} · planned</div>
                      <div className="text-[11px] text-white truncate">{u.pipeline}</div>
                    </Link>
                  ))}
                  {next.length > 30 && <div className="text-[10px] text-orbit-subtle">+{next.length - 30} more planned</div>}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </PageContainer>
  );
}
