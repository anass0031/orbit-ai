"use client";

import { useRouter, useSearchParams } from "next/navigation";
import React, { Suspense, useState } from "react";
import { Button, Field, InlineAlert } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const DEMO_ACCOUNTS = [
  ["admin@orbit.local", "Administrator"],
  ["engineer@orbit.local", "Data Engineer"],
  ["analyst@orbit.local", "Analyst"],
];

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("admin@orbit.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setFieldErrors({});
    setBusy(true);
    try {
      await login(email.trim(), password);
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        setFieldErrors(err.fieldErrors);
      } else setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative min-h-screen flex items-center justify-center p-4 bg-orbit-bg overflow-hidden">
      <div aria-hidden className="absolute inset-0 bg-cover bg-center" style={{ backgroundImage: "url(/media/login-background.jpg)" }} />
      <video aria-hidden className="absolute inset-0 w-full h-full object-cover motion-reduce:hidden"
        src="/media/login-background.mp4" poster="/media/login-background.jpg" autoPlay muted loop playsInline preload="auto" />
      <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-orbit-bg/70 via-orbit-bg/40 to-orbit-bg/80" />
      <div className="relative w-full max-w-sm space-y-6">
        <div className="flex items-center gap-2.5 justify-center">
          <div className="w-9 h-9 rounded-lg bg-gradient-to-tr from-orbit-purple to-orbit-cyan flex items-center justify-center shadow-[0_0_15px_rgba(121,92,255,0.4)]">
            <div className="w-4 h-4 border-2 border-white rounded-sm rotate-45" />
          </div>
          <span className="font-bold tracking-wider text-xl text-white">ORBIT</span>
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-orbit-purple/20 text-orbit-purpleLight border border-orbit-purple/30">AI</span>
        </div>

        <form onSubmit={submit} className="p-6 rounded-2xl bg-orbit-card/85 backdrop-blur-md border border-orbit-border shadow-2xl space-y-4">
          <div>
            <h1 className="text-base font-bold text-white">Sign in</h1>
            <p className="text-xs text-orbit-muted mt-0.5">Data & AI platform</p>
          </div>
          {params.get("expired") && <InlineAlert>Your session expired. Please sign in again.</InlineAlert>}
          {error && <InlineAlert tone="red">{error}</InlineAlert>}
          <Field label="Email" error={fieldErrors.email}>
            <input className="orbit-input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
          </Field>
          <Field label="Password" error={fieldErrors.password}>
            <input className="orbit-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus />
          </Field>
          <Button variant="primary" type="submit" loading={busy} className="w-full">Sign in</Button>
        </form>

        <div className="p-4 rounded-xl bg-orbit-panel/80 backdrop-blur-md border border-orbit-border space-y-2">
          <div className="orbit-label">Demo accounts (password = ORBIT_DEMO_PASSWORD in .env)</div>
          <div className="grid grid-cols-1 gap-1">
            {DEMO_ACCOUNTS.map(([e, r]) => (
              <button key={e} type="button" onClick={() => setEmail(e)} className="flex justify-between text-[11px] px-2 py-1 rounded hover:bg-white/[0.04]">
                <span className="font-mono text-orbit-text">{e}</span>
                <span className="text-orbit-subtle">{r}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
