"use client";

import { Bell, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React from "react";
import useSWR from "swr";
import { cn } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { NAV } from "@/lib/nav";
import type { Health } from "@/lib/types";

const COMPONENT_LABELS: Record<string, string> = { database: "PostgreSQL", object_storage: "MinIO", job_queue: "Job queue" };

export function Sidebar() {
  const pathname = usePathname();
  const { me, can, logout } = useAuth();
  const { data: health } = useSWR<Health>("/health", { refreshInterval: 30000 });
  const { data: unread } = useSWR<{ unread: number }>("/notifications?unread=true&limit=1", { refreshInterval: 20000 });
  const initials = (me?.user.full_name || "?").split(" ").map((p) => p[0]).join("").slice(0, 2).toUpperCase();

  return (
    <aside className="w-[224px] shrink-0 bg-orbit-sidebar border-r border-orbit-border flex flex-col justify-between select-none h-screen sticky top-0">
      <div className="p-3.5 space-y-4 overflow-y-auto">
        <Link href="/dashboard" className="flex items-center gap-2.5 px-1.5 group">
          <div className="w-7 h-7 rounded-lg bg-gradient-to-tr from-orbit-purple to-orbit-cyan flex items-center justify-center shadow-[0_0_15px_rgba(121,92,255,0.4)]">
            <div className="w-3.5 h-3.5 border-2 border-white rounded-sm rotate-45 group-hover:rotate-90 transition-transform" />
          </div>
          <span className="font-bold tracking-wider text-[15px] text-white">ORBIT</span>
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-orbit-purple/20 text-orbit-purpleLight border border-orbit-purple/30">AI</span>
        </Link>
        <Link href="/notifications" className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-[12px] text-orbit-nav hover:text-white hover:bg-white/[0.04]" title="Notifications">
          <Bell className="w-4 h-4" />
          {unread?.unread ? <span className="px-1.5 rounded-full bg-orbit-red text-white text-[10px] font-semibold">{unread.unread}</span> : <span className="text-orbit-subtle">no new alert</span>}
        </Link>

        <nav className="space-y-1">
          {NAV.filter((i) => !i.permission || can(i.permission)).map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || pathname.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-all border",
                  active
                    ? "bg-gradient-to-r from-orbit-purple/25 to-orbit-purple/5 text-white border-orbit-purple/40 shadow-sm"
                    : "text-orbit-nav hover:text-white hover:bg-white/[0.04] border-transparent",
                )}
              >
                <Icon className={cn("w-4 h-4 shrink-0", active ? "text-orbit-purple" : "text-orbit-subtle")} />
                <span className="truncate">{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="p-3.5 space-y-3 border-t border-orbit-border bg-orbit-panel">
        <div className="p-3 rounded-xl bg-orbit-card border border-orbit-border space-y-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-orbit-purpleLight">Platform health</div>
          {health ? (
            Object.entries(health.components).map(([k, c]) => (
              <div key={k} className="flex items-center justify-between text-[11px]" title={c.detail}>
                <span className="text-orbit-muted">{COMPONENT_LABELS[k] || k}</span>
                <span className={cn("flex items-center gap-1.5", c.status === "up" ? "text-orbit-green" : "text-orbit-redLight")}>
                  <span className={cn("w-1.5 h-1.5 rounded-full", c.status === "up" ? "bg-orbit-green" : "bg-orbit-red")} />
                  {c.status}
                </span>
              </div>
            ))
          ) : (
            <div className="text-[11px] text-orbit-subtle">Checking…</div>
          )}
        </div>

        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-7 h-7 rounded-full bg-orbit-border border border-orbit-borderStrong text-xs font-semibold text-orbit-cyan flex items-center justify-center shrink-0">
              {initials}
            </div>
            <div className="min-w-0">
              <div className="text-xs font-semibold text-white leading-tight truncate">{me?.user.full_name}</div>
              <div className="text-[11px] text-orbit-subtle">{me?.role_label}</div>
            </div>
          </div>
          <button onClick={logout} className="text-orbit-subtle hover:text-white p-1" aria-label="Sign out" title="Sign out">
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
