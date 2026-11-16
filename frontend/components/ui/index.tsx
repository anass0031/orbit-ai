"use client";
// Reusable UI kit (same visual language as the full ORBIT AI platform).
import { clsx } from "clsx";
import { AlertTriangle, Inbox, Loader2, X } from "lucide-react";
import React from "react";
import { ApiError } from "@/lib/api";
import type { Maturity, RunStatus } from "@/lib/types";

export const cn = (...a: Parameters<typeof clsx>) => clsx(...a);

/* ── Layout ─────────────────────────────────────────────────────────── */
export function PageContainer({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("p-6 space-y-5 max-w-[1440px] mx-auto", className)}>{children}</div>;
}

export function Card({
  children, className, onClick, padded = true,
}: { children: React.ReactNode; className?: string; onClick?: () => void; padded?: boolean }) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "rounded-xl bg-orbit-card border border-orbit-border",
        padded && "p-5",
        onClick && "cursor-pointer hover:bg-orbit-cardHover transition",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title, subtitle, right, className,
}: { title: React.ReactNode; subtitle?: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex items-start justify-between gap-3 mb-4", className)}>
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-white">{title}</h2>
        {subtitle && <p className="text-xs text-orbit-muted mt-0.5">{subtitle}</p>}
      </div>
      {right && <div className="shrink-0 flex items-center gap-2">{right}</div>}
    </div>
  );
}

export function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("orbit-label", className)}>{children}</div>;
}

/* ── Buttons ────────────────────────────────────────────────────────── */
type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
export function Button({
  variant = "secondary", size = "md", loading, icon: Icon, children, className, ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant; size?: "sm" | "md"; loading?: boolean; icon?: React.ComponentType<{ className?: string }>;
}) {
  const styles: Record<BtnVariant, string> = {
    primary: "bg-orbit-purple hover:bg-orbit-purpleHover text-white shadow-glow",
    secondary: "bg-orbit-card hover:bg-orbit-cardHover border border-orbit-border text-white",
    ghost: "text-orbit-muted hover:text-white hover:bg-white/[0.04]",
    danger: "bg-orbit-red/15 hover:bg-orbit-red/25 border border-orbit-red/40 text-orbit-redLight",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cn(
        "rounded-lg font-medium flex items-center justify-center gap-1.5 transition disabled:opacity-50 disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1.5 text-[11px]" : "px-3.5 py-2 text-xs",
        styles[variant],
        className,
      )}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : Icon ? <Icon className="w-3.5 h-3.5" /> : null}
      {children}
    </button>
  );
}

/* ── Badges & status ────────────────────────────────────────────────── */
type Tone = "green" | "purple" | "cyan" | "amber" | "red" | "gray";
const TONES: Record<Tone, string> = {
  green: "bg-orbit-green/15 text-orbit-green border-orbit-green/30",
  purple: "bg-orbit-purple/15 text-orbit-purpleLight border-orbit-purple/30",
  cyan: "bg-orbit-cyan/15 text-orbit-cyan border-orbit-cyan/30",
  amber: "bg-orbit-amber/15 text-orbit-amber border-orbit-amber/30",
  red: "bg-orbit-red/15 text-orbit-redLight border-orbit-red/30",
  gray: "bg-white/[0.04] text-orbit-nav border-orbit-border",
};

export function Badge({ tone = "gray", children, className, dot }: { tone?: Tone; children: React.ReactNode; className?: string; dot?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-0.5 rounded-full border whitespace-nowrap", TONES[tone], className)}>
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  succeeded: "green", completed: "green", active: "green", up: "green", passed: "green", committed: "green", healthy: "green", published: "green", resolved: "green",
  ingested: "green", discovered: "cyan",
  running: "purple", queued: "cyan", syncing: "purple", staged: "cyan", acknowledged: "cyan", registered: "cyan", staging: "cyan", production: "green",
  failed: "red", error: "red", down: "red", high: "red", open: "red",
  degraded: "amber", warning: "amber", medium: "amber", paused: "amber", draft: "amber", cancelled: "gray", disabled: "gray",
  low: "gray", planned: "gray", archived: "gray",
};

export function StatusPill({ status, className }: { status: RunStatus | string; className?: string }) {
  const tone = STATUS_TONE[status?.toLowerCase()] || "gray";
  const label = status ? status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, " ") : "Unknown";
  return (
    <Badge tone={tone} dot className={cn(status === "running" && "animate-pulse", className)}>
      {label}
    </Badge>
  );
}

/** Implemented / Simulated / Planned — required on anything whose realness could be misread. */
export function MaturityChip({ maturity, className }: { maturity: Maturity; className?: string }) {
  const map: Record<Maturity, { tone: Tone; label: string }> = {
    implemented: { tone: "green", label: "Implemented" },
    simulated: { tone: "amber", label: "Simulated" },
    planned: { tone: "gray", label: "Planned" },
  };
  const m = map[maturity];
  return <Badge tone={m.tone} className={cn("text-[10px] px-2", className)}>{m.label}</Badge>;
}

export function DemoChip({ label = "Demo data" }: { label?: string }) {
  return <Badge tone="amber" className="text-[10px] px-2">{label}</Badge>;
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn("w-full h-1.5 rounded-full bg-orbit-bg overflow-hidden", className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-orbit-purple to-orbit-cyan transition-all"
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

/* ── States ─────────────────────────────────────────────────────────── */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("w-4 h-4 animate-spin text-orbit-purple", className)} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-lg bg-white/[0.04]", className)} />;
}

export function LoadingState({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex items-center justify-center gap-2 py-10 text-xs text-orbit-muted", className)}>
      <Spinner /> {label}
    </div>
  );
}

export function EmptyState({
  title, description, action, icon: Icon = Inbox, className,
}: { title: string; description?: React.ReactNode; action?: React.ReactNode; icon?: React.ComponentType<{ className?: string }>; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center py-10 px-4", className)}>
      <div className="w-10 h-10 rounded-xl bg-orbit-purple/15 text-orbit-purple flex items-center justify-center mb-3">
        <Icon className="w-5 h-5" />
      </div>
      <div className="text-sm font-semibold text-white">{title}</div>
      {description && <div className="text-xs text-orbit-muted mt-1 max-w-md">{description}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
  const cid = error instanceof ApiError ? error.correlationId : undefined;
  return (
    <div className={cn("p-4 rounded-xl bg-orbit-red/10 border border-orbit-red/30 flex items-start gap-3", className)}>
      <AlertTriangle className="w-4 h-4 text-orbit-redLight shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-semibold text-white">Something went wrong</div>
        <div className="text-xs text-orbit-muted mt-0.5 break-words">{msg}</div>
        {cid && <div className="text-[10px] text-orbit-subtle font-mono mt-1">correlation id: {cid}</div>}
      </div>
      {onRetry && <Button size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function InlineAlert({ tone = "amber", children, className }: { tone?: Tone; children: React.ReactNode; className?: string }) {
  return <div className={cn("px-3 py-2 rounded-lg border text-[11px]", TONES[tone], className)}>{children}</div>;
}

/* ── Overlays ───────────────────────────────────────────────────────── */
export function Modal({
  open, onClose, title, subtitle, children, footer, width = "max-w-lg",
}: { open: boolean; onClose: () => void; title: string; subtitle?: string; children: React.ReactNode; footer?: React.ReactNode; width?: string }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onMouseDown={onClose}>
      <div
        onMouseDown={(e) => e.stopPropagation()}
        className={cn("w-full rounded-2xl bg-orbit-card border border-orbit-border shadow-2xl flex flex-col max-h-[90vh]", width)}
      >
        <div className="flex items-start justify-between border-b border-orbit-border p-5 pb-3">
          <div>
            <h3 className="text-sm font-bold text-white">{title}</h3>
            {subtitle && <p className="text-xs text-orbit-muted mt-0.5">{subtitle}</p>}
          </div>
          <button onClick={onClose} className="text-orbit-muted hover:text-white" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 overflow-y-auto space-y-4">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-orbit-border flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({
  open, onClose, title, subtitle, children, width = "w-[560px]",
}: { open: boolean; onClose: () => void; title: React.ReactNode; subtitle?: React.ReactNode; children: React.ReactNode; width?: string }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50" onMouseDown={onClose}>
      <div
        onMouseDown={(e) => e.stopPropagation()}
        className={cn("h-full max-w-full bg-orbit-panel border-l border-orbit-border shadow-2xl flex flex-col", width)}
      >
        <div className="flex items-start justify-between p-5 border-b border-orbit-border">
          <div className="min-w-0">
            <h3 className="text-sm font-bold text-white truncate">{title}</h3>
            {subtitle && <div className="text-xs text-orbit-muted mt-0.5">{subtitle}</div>}
          </div>
          <button onClick={onClose} className="text-orbit-muted hover:text-white" aria-label="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4">{children}</div>
      </div>
    </div>
  );
}

export function ConfirmModal({
  open, onClose, onConfirm, title, message, confirmLabel = "Confirm", danger, loading,
}: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; message: React.ReactNode; confirmLabel?: string; danger?: boolean; loading?: boolean }) {
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md"
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={loading}>{confirmLabel}</Button>
      </>}>
      <div className="text-xs text-orbit-muted">{message}</div>
    </Modal>
  );
}

/* ── Forms ──────────────────────────────────────────────────────────── */
export function Field({
  label, error, hint, children, className,
}: { label: string; error?: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <label className={cn("block space-y-1.5", className)}>
      <span className="orbit-label">{label}</span>
      {children}
      {error ? <span className="block text-[11px] text-orbit-redLight">{error}</span> : hint ? <span className="block text-[11px] text-orbit-subtle">{hint}</span> : null}
    </label>
  );
}

export function Segmented<T extends string>({
  value, options, onChange,
}: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex w-fit items-center bg-orbit-bg p-1 rounded-lg border border-orbit-border">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "px-2.5 py-1 rounded-md text-xs font-medium transition",
            value === o.value ? "bg-orbit-card text-white shadow" : "text-orbit-subtle hover:text-white",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function FilterChips<T extends string>({
  value, options, onChange,
}: { value: T; options: { value: T; label: string; count?: number }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "px-3 py-1.5 rounded-lg text-xs font-medium border transition",
            value === o.value
              ? "bg-orbit-purple/20 text-white border-orbit-purple/40"
              : "bg-orbit-card text-orbit-muted border-orbit-border hover:text-white",
          )}
        >
          {o.label}
          {o.count !== undefined && <span className="ml-1.5 text-orbit-subtle">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ── KPI tile ───────────────────────────────────────────────────────── */
export function Kpi({
  label, value, sub, subTone = "muted", onClick, icon: Icon, right,
}: { label: string; value: React.ReactNode; sub?: React.ReactNode; subTone?: "green" | "red" | "muted"; onClick?: () => void; icon?: React.ComponentType<{ className?: string }>; right?: React.ReactNode }) {
  const tone = subTone === "green" ? "text-orbit-green" : subTone === "red" ? "text-orbit-redLight" : "text-orbit-muted";
  return (
    <Card onClick={onClick} padded={false} className="p-4 flex flex-col justify-between min-h-[108px]">
      <div className="flex items-center justify-between text-xs text-orbit-muted">
        <span className="flex items-center gap-1.5">{label}</span>
        {Icon && <Icon className="w-4 h-4 text-orbit-subtle" />}
      </div>
      <div className="flex items-end justify-between gap-2">
        <div>
          <div className="text-2xl font-bold text-white tracking-tight">{value}</div>
          {sub && <div className={cn("text-[11px] font-medium mt-1", tone)}>{sub}</div>}
        </div>
        {right}
      </div>
    </Card>
  );
}

/* ── Table ──────────────────────────────────────────────────────────── */
/** Display only: drop binary floating-point noise (4002005.7300000004 → 4002005.73); data is unchanged. */
function cellText(v: unknown): string {
  return typeof v === "number" && !Number.isInteger(v) ? String(Number(v.toPrecision(12))) : String(v);
}

export function DataTable({
  columns, rows, maxHeight, className,
}: { columns: string[]; rows: Record<string, unknown>[] | unknown[][]; maxHeight?: string; className?: string }) {
  return (
    <div className={cn("overflow-auto rounded-lg border border-orbit-border", className)} style={{ maxHeight }}>
      <table className="orbit-table w-full">
        <thead className="sticky top-0 bg-orbit-panel z-10">
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c, j) => {
                const v = Array.isArray(r) ? r[j] : (r as Record<string, unknown>)[c];
                return (
                  <td key={c} className="font-mono text-[11px] text-orbit-text whitespace-nowrap max-w-[280px] truncate">
                    {v === null || v === undefined ? <span className="text-orbit-subtle italic">null</span> : cellText(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <div className="flex items-center justify-between text-xs text-orbit-muted pt-3">
      <span>{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
      <div className="flex gap-1.5">
        <Button size="sm" disabled={page <= 1} onClick={() => onChange(offset - limit)}>Prev</Button>
        <span className="px-2 py-1.5">{page} / {pages}</span>
        <Button size="sm" disabled={page >= pages} onClick={() => onChange(offset + limit)}>Next</Button>
      </div>
    </div>
  );
}
