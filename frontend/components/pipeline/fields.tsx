"use client";
// Small form controls shared by the node configuration panels.
import { Plus, Trash2 } from "lucide-react";
import React from "react";
import { cn } from "@/components/ui";
import type { ColumnSchema } from "@/lib/types";

export const arr = (v: unknown): string[] => (Array.isArray(v) ? (v as string[]) : []);

export function ColumnSelect({ value, columns, onChange, disabled, allowStar, placeholder }: {
  value: string; columns: ColumnSchema[]; onChange: (v: string) => void; disabled: boolean; allowStar?: boolean; placeholder?: string;
}) {
  return (
    <select className="orbit-input" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowStar ? "* (all rows)" : placeholder || "Select column…"}</option>
      {columns.map((c) => <option key={c.name} value={c.name}>{c.name} · {c.type}</option>)}
      {value && !columns.some((c) => c.name === value) && <option value={value}>{value} (not upstream)</option>}
    </select>
  );
}

export function MultiColumns({ value, columns, onChange, disabled }: { value: string[]; columns: ColumnSchema[]; onChange: (v: string[]) => void; disabled: boolean }) {
  if (!columns.length) return <div className="text-[11px] text-orbit-subtle">Connect an input and validate to list columns.</div>;
  return (
    <div className="max-h-40 overflow-y-auto rounded-lg bg-orbit-bg border border-orbit-border p-2 space-y-1">
      {columns.map((c) => (
        <label key={c.name} className="flex items-center gap-2 text-[11px] font-mono text-white">
          <input type="checkbox" disabled={disabled} checked={value.includes(c.name)} onChange={(e) => onChange(e.target.checked ? [...value, c.name] : value.filter((x) => x !== c.name))} />
          {c.name} <span className="text-orbit-subtle">{c.type}</span>
        </label>
      ))}
    </div>
  );
}

/** Editable list of rows (add / remove), each rendered by `render`. */
export function ListEditor<T>({ items, onChange, empty, render, disabled, addLabel }: {
  items: T[]; onChange: (v: T[]) => void; empty: T; render: (item: T, update: (patch: Partial<T>) => void, i: number) => React.ReactNode;
  disabled: boolean; addLabel: string;
}) {
  return (
    <div className="space-y-1.5">
      {items.map((it, i) => (
        <div key={i} className={cn("p-1.5 rounded-lg border border-orbit-border space-y-1.5 relative")}>
          {render(it, (patch) => onChange(items.map((x, j) => (j === i ? { ...x, ...patch } : x))), i)}
          {!disabled && (
            <button onClick={() => onChange(items.filter((_x, j) => j !== i))} className="text-[11px] text-orbit-subtle hover:text-orbit-redLight flex items-center gap-1">
              <Trash2 className="w-3 h-3" />Remove
            </button>
          )}
        </div>
      ))}
      {!disabled && <button className="text-[11px] text-orbit-purple flex items-center gap-1" onClick={() => onChange([...items, { ...empty }])}><Plus className="w-3 h-3" />{addLabel}</button>}
    </div>
  );
}

export function Check({ label, value, onChange, disabled }: { label: React.ReactNode; value: boolean; onChange: (v: boolean) => void; disabled: boolean }) {
  return <label className="flex items-center gap-2 text-xs text-white"><input type="checkbox" disabled={disabled} checked={value} onChange={(e) => onChange(e.target.checked)} />{label}</label>;
}

export const small = "orbit-input !py-1 !text-[11px]";
