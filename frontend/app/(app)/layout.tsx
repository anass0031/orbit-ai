"use client";

import { usePathname, useRouter } from "next/navigation";
import React, { useEffect } from "react";
import { Sidebar } from "@/components/layout/Sidebar";
import { EmptyState, LoadingState } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { navFor } from "@/lib/nav";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, loading, can } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !me) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [loading, me, router, pathname]);

  if (loading || !me) {
    return <div className="min-h-screen flex items-center justify-center"><LoadingState label="Loading…" /></div>;
  }

  const meta = navFor(pathname);
  const forbidden = meta?.permission && !can(meta.permission);

  return (
    <div className="min-h-screen bg-orbit-bg text-white flex">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        {meta && (
          <header className="px-6 py-4 border-b border-orbit-border bg-orbit-panel/60">
            <h1 className="text-lg font-bold text-white">{meta.title}</h1>
            <p className="text-xs text-orbit-muted mt-0.5">{meta.subtitle}</p>
          </header>
        )}
        <main className="flex-1 min-h-0">
          {forbidden ? (
            <EmptyState
              className="mt-20"
              title="You don't have access to this page"
              description={`Your role (${me.role_label}) does not include '${meta?.permission}'. Ask an administrator if you need it.`}
            />
          ) : (
            children
          )}
        </main>
      </div>
    </div>
  );
}
