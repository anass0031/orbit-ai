import { Database, LayoutGrid, Upload, Users } from "lucide-react";
import type React from "react";

export interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  subtitle: string;
  permission?: string; // hidden from the menu, and the page is blocked, when the role lacks it
}

export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutGrid, title: "Dashboard", subtitle: "Platform status and Bronze layer at a glance." },
  { href: "/imports", label: "File Import", icon: Upload, permission: "imports.read", title: "File Import", subtitle: "Upload CSV, Excel or JSON files, review the discovered schema, load them into Bronze." },
  { href: "/datasets", label: "Bronze Catalog", icon: Database, permission: "datasets.read", title: "Bronze Catalog", subtitle: "Raw datasets stored as versioned Parquet files in MinIO." },
  { href: "/admin", label: "Users & Audit", icon: Users, permission: "users.manage", title: "Administration", subtitle: "Users, roles and the audit trail." },
];

export function navFor(pathname: string): NavItem | undefined {
  return NAV.find((i) => pathname === i.href || pathname.startsWith(i.href + "/"));
}

export const ROLE_OPTIONS = [
  { value: "admin", label: "Administrator" },
  { value: "data_engineer", label: "Data Engineer" },
  { value: "analyst", label: "Analyst" },
] as const;
