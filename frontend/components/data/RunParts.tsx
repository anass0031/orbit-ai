"use client";

import React from "react";
import { cn } from "@/components/ui";
import type { LogLine } from "@/lib/types";

const LEVEL_STYLE: Record<string, string> = {
  error: "text-orbit-redLight",
  warning: "text-orbit-amber",
  info: "text-orbit-text",
};

/** Run log lines, oldest first, in a scrollable monospace box. */
export function RunLogs({ logs, className }: { logs: LogLine[]; className?: string }) {
  return (
    <div className={cn("rounded-lg bg-orbit-bg border border-orbit-border p-3 font-mono text-[11px] overflow-auto max-h-80 space-y-0.5", className)}>
      {logs.length === 0 && <div className="text-orbit-subtle">No log lines yet.</div>}
      {logs.map((l, i) => (
        <div key={i} className="flex gap-3">
          <span className="text-orbit-subtle shrink-0">{l.ts.slice(11, 19)}</span>
          <span className={cn("shrink-0 w-14 uppercase", LEVEL_STYLE[l.level] || "text-orbit-muted")}>{l.level}</span>
          <span className={cn("break-all", LEVEL_STYLE[l.level] || "text-orbit-text")}>{l.msg}</span>
        </div>
      ))}
    </div>
  );
}

export function scoreTone(score: number | null | undefined): string {
  if (score === null || score === undefined) return "text-orbit-subtle";
  if (score >= 95) return "text-orbit-green";
  if (score >= 80) return "text-orbit-amber";
  return "text-orbit-redLight";
}

/** Quality score as a ring (0-100). Shows "—" when the dataset has never been checked. */
export function ScoreRing({ score, size = 72, label }: { score: number | null | undefined; size?: number; label?: string }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const value = score ?? 0;
  const color = score === null || score === undefined ? "#334155" : value >= 95 ? "#20C997" : value >= 80 ? "#F59E0B" : "#EF4444";
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Quality score ${score ?? "not computed"}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#1E293B" strokeWidth={6} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={6} strokeLinecap="round"
          strokeDasharray={`${(c * value) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fill="#F8FAFC" fontSize={size / 4.2} fontWeight={700}>
          {score === null || score === undefined ? "—" : Math.round(value)}
        </text>
      </svg>
      {label && <span className="text-[10px] uppercase tracking-wider text-orbit-subtle">{label}</span>}
    </div>
  );
}

/** Horizontal bar 0-100 for a dimension score. */
export function ScoreBar({ label, score }: { label: string; score: number | null | undefined }) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[11px]">
        <span className="text-orbit-muted capitalize">{label}</span>
        <span className={cn("font-mono", scoreTone(score))}>{score === null || score === undefined ? "—" : `${score}%`}</span>
      </div>
      <div className="h-1.5 rounded-full bg-orbit-bg overflow-hidden">
        <div className={cn("h-full rounded-full", score === null || score === undefined ? "" : score >= 95 ? "bg-orbit-green" : score >= 80 ? "bg-orbit-amber" : "bg-orbit-red")}
          style={{ width: `${score ?? 0}%` }} />
      </div>
    </div>
  );
}
