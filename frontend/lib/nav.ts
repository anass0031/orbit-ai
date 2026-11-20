import { Activity, BookOpen, Boxes, CalendarClock, Database, GitBranch, LayoutGrid, ShieldCheck, Upload, Users } from "lucide-react";
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
  { href: "/dashboard", label: "Dashboard", icon: LayoutGrid, title: "Dashboard", subtitle: "Lakehouse layers, pipeline runs and data quality at a glance." },
  { href: "/imports", label: "File Import", icon: Upload, permission: "imports.read", title: "File Import", subtitle: "Upload CSV, Excel, JSON, XML, YAML or Parquet files, review the discovered schema, load them into Bronze." },
  { href: "/datasets", label: "Lakehouse Catalog", icon: Database, permission: "datasets.read", title: "Lakehouse Catalog", subtitle: "Bronze, Silver and Gold datasets stored as versioned Parquet files in MinIO." },
  { href: "/pipelines", label: "ETL Pipelines", icon: GitBranch, permission: "pipelines.read", title: "ETL Pipelines", subtitle: "Visual pipelines executed by DuckDB: Bronze → Silver → Gold." },
  { href: "/gold-models", label: "Gold Models", icon: Boxes, permission: "datasets.read", title: "Gold Models", subtitle: "Analyse Silver, review the proposed star and snowflake schemas, and generate the Gold layer." },
  { href: "/runs", label: "Run Monitoring", icon: Activity, permission: "runs.read", title: "Run Monitoring", subtitle: "Every pipeline run: status, steps, logs, retry and cancel." },
  { href: "/calendar", label: "Run Calendar", icon: CalendarClock, permission: "pipelines.read", title: "Run Calendar", subtitle: "Pipeline runs of the week and the runs planned by the schedules (UTC)." },
  { href: "/quality", label: "Data Quality", icon: ShieldCheck, permission: "quality.read", title: "Data Quality", subtitle: "Rules, scores and anomalies across the lakehouse." },
  { href: "/glossary", label: "Glossary", icon: BookOpen, permission: "glossary.read", title: "Business Glossary", subtitle: "Shared definitions of business terms, linked to datasets and columns." },
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
