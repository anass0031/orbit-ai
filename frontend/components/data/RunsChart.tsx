"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { DayRuns } from "@/lib/types";

const COLORS = { succeeded: "#20C997", failed: "#EF4444", cancelled: "#64748B" };

/** Runs per day, stacked by outcome. */
export function RunsChart({ data, height = 220 }: { data: DayRuns[]; height?: number }) {
  const rows = data.map((d) => ({ ...d, day: new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { day: "2-digit", month: "short" }) }));
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="#1E293B" vertical={false} />
          <XAxis dataKey="day" tick={{ fill: "#64748B", fontSize: 10 }} tickLine={false} axisLine={{ stroke: "#1E293B" }} interval="preserveStartEnd" />
          <YAxis allowDecimals={false} tick={{ fill: "#64748B", fontSize: 10 }} tickLine={false} axisLine={false} />
          <Tooltip cursor={{ fill: "rgba(255,255,255,0.03)" }}
            contentStyle={{ background: "#151F32", border: "1px solid #1E293B", borderRadius: 8, fontSize: 11 }}
            labelStyle={{ color: "#F8FAFC" }} />
          {(Object.keys(COLORS) as (keyof typeof COLORS)[]).map((k, i, all) => (
            <Bar key={k} dataKey={k} name={k} stackId="runs" fill={COLORS[k]} maxBarSize={28}
              radius={i === all.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
