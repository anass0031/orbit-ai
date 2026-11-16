"use client";

import { UserPlus } from "lucide-react";
import React, { useState } from "react";
import useSWR from "swr";
import {
  Badge, Button, Card, CardHeader, ErrorState, Field, InlineAlert, LoadingState, Modal, PageContainer, Pagination, Segmented,
} from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, dateTime } from "@/lib/format";
import { ROLE_OPTIONS } from "@/lib/nav";
import { useToast } from "@/lib/toast";
import type { AuditEvent, Page, Role, User } from "@/lib/types";

const ROLE_LABEL = Object.fromEntries(ROLE_OPTIONS.map((r) => [r.value, r.label]));

function NewUserModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const notify = useToast();
  const [form, setForm] = useState({ email: "", full_name: "", role: "analyst" as Role, password: "" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  const submit = async () => {
    setBusy(true);
    setErrors({});
    setError(null);
    try {
      await api("/users", { method: "POST", body: form });
      notify(`User ${form.email} created`);
      setForm({ email: "", full_name: "", role: "analyst", password: "" });
      onCreated();
      onClose();
    } catch (e) {
      if (e instanceof ApiError) {
        setErrors(e.fieldErrors);
        setError(e.message);
      } else setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="New user"
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit}>Create</Button></>}>
      {error && <InlineAlert tone="red">{error}</InlineAlert>}
      <Field label="Full name" error={errors.full_name}><input className="orbit-input" value={form.full_name} onChange={set("full_name")} /></Field>
      <Field label="Email" error={errors.email}><input className="orbit-input" value={form.email} onChange={set("email")} /></Field>
      <Field label="Role" error={errors.role}>
        <select className="orbit-input" value={form.role} onChange={set("role")}>
          {ROLE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
        </select>
      </Field>
      <Field label="Initial password" hint="At least 8 characters" error={errors.password}>
        <input className="orbit-input" type="password" value={form.password} onChange={set("password")} autoComplete="new-password" />
      </Field>
    </Modal>
  );
}

function UsersTab() {
  const { me } = useAuth();
  const notify = useToast();
  const { data, error, mutate } = useSWR<User[]>("/users");
  const [open, setOpen] = useState(false);

  const update = async (u: User, body: Partial<Pick<User, "role" | "is_active">>) => {
    try {
      await api(`/users/${u.id}`, { method: "PATCH", body });
      notify(`${u.email} updated`);
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    }
  };

  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!data) return <LoadingState />;
  return (
    <Card>
      <CardHeader title={`Users (${data.length})`} subtitle="A role change applies immediately, even to users already signed in."
        right={<Button variant="primary" icon={UserPlus} onClick={() => setOpen(true)}>New user</Button>} />
      <table className="orbit-table w-full">
        <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th /></tr></thead>
        <tbody>
          {data.map((u) => {
            const self = u.id === me?.user.id;
            return (
              <tr key={u.id}>
                <td className="text-white">{u.full_name}{self && <span className="text-orbit-subtle"> (you)</span>}</td>
                <td className="font-mono text-[11px]">{u.email}</td>
                <td>
                  <select className="orbit-input !w-40 !py-1" value={u.role} disabled={self} onChange={(e) => update(u, { role: e.target.value as Role })}>
                    {ROLE_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                  </select>
                </td>
                <td><Badge tone={u.is_active ? "green" : "gray"} dot>{u.is_active ? "Active" : "Disabled"}</Badge></td>
                <td className="text-orbit-muted">{ago(u.last_login_at)}</td>
                <td className="text-right">
                  {!self && (
                    <Button size="sm" variant={u.is_active ? "danger" : "secondary"} onClick={() => update(u, { is_active: !u.is_active })}>
                      {u.is_active ? "Disable" : "Enable"}
                    </Button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <NewUserModal open={open} onClose={() => setOpen(false)} onCreated={() => mutate()} />
    </Card>
  );
}

function AuditTab() {
  const [offset, setOffset] = useState(0);
  const { data, error, mutate } = useSWR<Page<AuditEvent>>(`/audit?limit=25&offset=${offset}`);
  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!data) return <LoadingState />;
  return (
    <Card>
      <CardHeader title="Audit log" subtitle="Sign-ins, user changes, uploads and Bronze writes" />
      <table className="orbit-table w-full">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Resource</th><th>Details</th></tr></thead>
        <tbody>
          {data.items.map((e) => (
            <tr key={e.id}>
              <td className="text-orbit-muted whitespace-nowrap">{dateTime(e.created_at)}</td>
              <td className="font-mono text-[11px]">{e.actor_email}</td>
              <td><Badge tone={e.action.includes("failed") ? "red" : "purple"}>{e.action}</Badge></td>
              <td className="text-[11px] text-orbit-muted">{e.resource_type}</td>
              <td className="font-mono text-[10px] text-orbit-subtle max-w-[380px] truncate" title={JSON.stringify(e.details)}>
                {Object.keys(e.details).length ? JSON.stringify(e.details) : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pagination total={data.total} limit={25} offset={offset} onChange={setOffset} />
    </Card>
  );
}

export default function AdminPage() {
  const [tab, setTab] = useState<"users" | "audit">("users");
  return (
    <PageContainer>
      <Segmented value={tab} onChange={setTab} options={[{ value: "users", label: "Users & roles" }, { value: "audit", label: "Audit log" }]} />
      {tab === "users" ? <UsersTab /> : <AuditTab />}
      <div className="text-[11px] text-orbit-subtle">
        Roles: {Object.values(ROLE_LABEL).join(" · ")}. Administrators manage users and read the audit log; Data Engineers import files
        into Bronze; Analysts browse the catalog and the import history.
      </div>
    </PageContainer>
  );
}
