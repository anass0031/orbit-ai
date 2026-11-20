"use client";

import { Bell, CheckCheck } from "lucide-react";
import Link from "next/link";
import React from "react";
import useSWR, { mutate as refresh } from "swr";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, PageContainer, cn } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, dateTime } from "@/lib/format";
import type { NotificationItem } from "@/lib/types";

const TONE = { info: "cyan", success: "green", warning: "amber", error: "red" } as const;

export default function NotificationsPage() {
  const { data, error, mutate } = useSWR<{ unread: number; items: NotificationItem[] }>("/notifications?limit=200", { refreshInterval: 15000 });
  const read = async (id: string) => {
    await api(`/notifications/${id}/read`, { method: "POST" }).catch(() => undefined);
    mutate();
    refresh("/notifications?unread=true&limit=1");
  };
  const readAll = async () => {
    await api("/notifications/read-all", { method: "POST" }).catch(() => undefined);
    mutate();
    refresh("/notifications?unread=true&limit=1");
  };
  return (
    <PageContainer>
      <div className="flex items-center justify-between">
        <div className="text-xs text-orbit-muted">{data ? `${data.unread} unread` : ""} · chosen per pipeline in Automation → Alerts</div>
        <Button size="sm" icon={CheckCheck} disabled={!data?.unread} onClick={readAll}>Mark all as read</Button>
      </div>
      <Card padded={false}>
        {error ? <ErrorState error={error} className="m-4" /> : !data ? <LoadingState /> : data.items.length === 0 ? (
          <EmptyState icon={Bell} title="No notifications" description="Pipeline failures, retries, warnings and SLA alerts appear here." />
        ) : (
          <div className="divide-y divide-orbit-border">
            {data.items.map((n) => (
              <div key={n.id} className={cn("px-4 py-3 flex items-start gap-3", !n.read && "bg-orbit-purple/5")}>
                <span className={cn("w-2 h-2 rounded-full mt-1.5 shrink-0", n.read ? "bg-transparent" : "bg-orbit-purple")} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2"><Badge tone={TONE[n.level]} className="text-[10px]">{n.level}</Badge><span className="text-sm font-semibold text-white truncate">{n.title}</span></div>
                  {n.message && <p className="text-xs text-orbit-text mt-1 whitespace-pre-wrap">{n.message}</p>}
                  <div className="text-[11px] text-orbit-subtle mt-1" title={dateTime(n.created_at)}>{ago(n.created_at)}
                    {n.link && <> · <Link href={n.link} onClick={() => read(n.id)} className="text-orbit-purpleLight hover:underline">open</Link></>}</div>
                </div>
                {!n.read && <button onClick={() => read(n.id)} className="text-[11px] text-orbit-muted hover:text-white">Mark read</button>}
              </div>
            ))}
          </div>
        )}
      </Card>
    </PageContainer>
  );
}
