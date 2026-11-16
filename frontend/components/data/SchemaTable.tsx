"use client";

import { KeyRound } from "lucide-react";
import React from "react";
import { Badge } from "@/components/ui";
import type { ColumnSchema } from "@/lib/types";

const TYPE_TONE: Record<string, "purple" | "cyan" | "green" | "amber" | "gray"> = {
  integer: "cyan", double: "cyan", boolean: "green", date: "amber", timestamp: "amber", string: "purple",
};

function show(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && !Number.isInteger(v)) return String(Number(v.toPrecision(10)));
  return String(v);
}

/** Discovered schema: one row per column with its inferred type and profile. */
export function SchemaTable({ schema, primaryKey = [] }: { schema: ColumnSchema[]; primaryKey?: string[] }) {
  return (
    <div className="overflow-auto rounded-lg border border-orbit-border">
      <table className="orbit-table w-full">
        <thead className="bg-orbit-panel">
          <tr>
            <th>Column</th>
            <th>Source name</th>
            <th>Type</th>
            <th className="text-right">Nulls</th>
            <th className="text-right">Distinct</th>
            <th>Min</th>
            <th>Max</th>
            <th>Examples</th>
          </tr>
        </thead>
        <tbody>
          {schema.map((c) => (
            <tr key={c.name} className={c.metadata ? "opacity-60" : undefined}>
              <td className="font-mono text-[11px] text-white whitespace-nowrap">
                <span className="inline-flex items-center gap-1.5">
                  {c.name}
                  {primaryKey.includes(c.name) && (
                    <span title="Likely primary key (unique, never empty)"><KeyRound className="w-3 h-3 text-orbit-amber" /></span>
                  )}
                </span>
              </td>
              <td className="text-orbit-muted text-[11px]">
                {c.metadata ? <span className="italic">{c.description || "technical column"}</span> : c.source_name || "—"}
              </td>
              <td><Badge tone={TYPE_TONE[c.type] || "gray"}>{c.type}</Badge></td>
              <td className="text-right font-mono text-[11px]">
                {c.null_count === undefined ? "—" : `${c.null_count}${c.null_pct ? ` (${c.null_pct}%)` : ""}`}
              </td>
              <td className="text-right font-mono text-[11px]">{show(c.distinct_count)}</td>
              <td className="font-mono text-[11px] whitespace-nowrap">{show(c.min)}</td>
              <td className="font-mono text-[11px] whitespace-nowrap">{show(c.max)}</td>
              <td className="text-[11px] text-orbit-muted max-w-[260px] truncate" title={c.examples?.join(" · ")}>
                {c.examples?.length ? c.examples.join(" · ") : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
