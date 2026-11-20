"use client";
// Charts of a Gold table: every axis shows the business names held by the dimensions, never a surrogate key.
import React, { useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import useSWR from "swr";
import { Card, CardHeader, DataTable, EmptyState, ErrorState, Field, LoadingState, cn } from "@/components/ui";
import { compact } from "@/lib/format";
import type { Dataset, GoldChart, GoldCharts } from "@/lib/types";

const MARK = "#795CFF";
const AXIS = { stroke: "#94A3B8", fontSize: 11, tickLine: false, axisLine: false } as const;
const TIP = { contentStyle: { background: "#151F32", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }, labelStyle: { color: "#F1F5F9" }, itemStyle: { color: "#CBD5E1" } };
const full = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
const short = (s: string) => (s.length > 22 ? `${s.slice(0, 21)}…` : s);

function ChartCard({ c }: { c: GoldChart }) {
  const [table, setTable] = useState(false);
  const height = c.type === "bar" ? Math.max(150, c.points.length * 30 + 30) : 240;
  return (
    <div className="p-4 rounded-lg bg-orbit-bg border border-orbit-border min-w-0">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-white">{c.title}</div>
          <div className="text-[11px] text-orbit-muted truncate">{c.subtitle ?? c.x}{c.groups > c.points.length ? ` · top ${c.points.length} of ${c.groups}` : ""}</div>
        </div>
        <button className="text-[11px] text-orbit-purpleLight hover:underline shrink-0" onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </div>
      {table ? <DataTable columns={[c.x, c.y]} rows={c.points.map((p) => ({ [c.x]: p.label, [c.y]: p.value }))} maxHeight={`${height}px`} /> : (
        <ResponsiveContainer width="100%" height={height}>
          {c.type === "line" ? (
            <LineChart data={c.points} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#1E293B" vertical={false} />
              <XAxis dataKey="label" {...AXIS} minTickGap={28} />
              <YAxis {...AXIS} width={52} tickFormatter={(v) => compact(v)} />
              <Tooltip {...TIP} formatter={(v: number) => [full(v), c.y]} />
              <Line dataKey="value" stroke={MARK} strokeWidth={2} dot={c.points.length <= 18 ? { r: 4, fill: MARK, strokeWidth: 0 } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
            </LineChart>
          ) : (
            <BarChart data={c.points} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }} barCategoryGap={6}>
              <CartesianGrid stroke="#1E293B" horizontal={false} />
              <XAxis type="number" {...AXIS} tickFormatter={(v) => compact(v)} />
              <YAxis type="category" dataKey="label" {...AXIS} width={140} interval={0} tickFormatter={short} />
              <Tooltip {...TIP} cursor={{ fill: "rgba(255,255,255,0.04)" }} formatter={(v: number) => [full(v), c.y]} />
              <Bar dataKey="value" fill={MARK} radius={[0, 4, 4, 0]} isAnimationActive={false} />
            </BarChart>
          )}
        </ResponsiveContainer>
      )}
    </div>
  );
}

export function BusinessCharts({ ds, version }: { ds: Dataset; version: number }) {
  const [measure, setMeasure] = useState<string | null>(null);
  const { data, error } = useSWR<GoldCharts>(`/datasets/${ds.id}/charts?version=${version}${measure ? `&measure=${encodeURIComponent(measure)}` : ""}`);
  if (error) return <ErrorState error={error} />;
  if (!data) return <LoadingState label="Reading the dimensions…" />;
  return (
    <Card>
      <CardHeader title="Business charts" subtitle="Grouped by the names held in the dimensions of this table"
        right={data.measures.length > 1 ? (
          <Field label="Measure" className="w-48">
            <select className="orbit-input" value={data.measure ?? ""} onChange={(e) => setMeasure(e.target.value)}>
              {data.measures.map((m) => <option key={m} value={m}>{m.replace(/_/g, " ")}</option>)}
            </select>
          </Field>
        ) : undefined} />
      <div className={cn("grid gap-3 mb-4 grid-cols-2", data.kpis.length >= 4 ? "lg:grid-cols-5" : "lg:grid-cols-4")}>
        {data.kpis.map((k) => (
          <div key={k.label} className="p-3 rounded-lg bg-orbit-bg border border-orbit-border">
            <div className="text-[11px] text-orbit-muted truncate" title={k.label}>{k.label}</div>
            <div className="text-xl font-bold text-white mt-0.5" title={full(k.value)}>{compact(k.value)}</div>
          </div>
        ))}
      </div>
      {data.charts.length ? <div className="grid xl:grid-cols-2 gap-3 items-start">{data.charts.map((c) => <ChartCard key={c.id} c={c} />)}</div>
        : <EmptyState title="Nothing to chart yet" description="This table has no descriptive column or relationship to group by." />}
    </Card>
  );
}
