"use client";

import {
  Background, Controls, ReactFlow, ReactFlowProvider, addEdge, useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { CalendarClock, Check, ChevronLeft, Copy, Eye, History, Play, Plus, Search, ShieldCheck, Trash2, Upload, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { RunLogs } from "@/components/data/RunParts";
import { NodeConfig } from "@/components/pipeline/NodeConfig";
import { AutomationDrawer, RunParamsModalBody } from "@/components/pipeline/Automation";
import { NODE_META, TERMINAL_TYPES, defaultConfig, nodeTypes, type Category, type NodeType, type PNodeData } from "@/components/pipeline/nodes";
import { Button, ConfirmModal, DataTable, ErrorState, InlineAlert, LoadingState, Modal, StatusPill, cn } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ago, duration } from "@/lib/format";
import { useToast } from "@/lib/toast";
import type { AutomationInfo, ColumnSchema, Dataset, RunDetail } from "@/lib/types";

interface SpecNode { id: string; type: NodeType; label: string; position: { x: number; y: number }; config: Record<string, unknown> }
interface Graph { nodes: SpecNode[]; edges: { id: string; source: string; target: string; source_handle?: string | null }[] }
interface PipelineDetail {
  id: string; name: string; description: string | null; status: string; graph: Graph; current_version: number;
  last_run_at: string | null; last_run_status: string | null; updated_at: string; next_run_at?: string | null;
}
interface Issue { node_id: string | null; field: string | null; message: string; severity: "error" | "warning" }
interface Validation { valid: boolean; issues: Issue[]; node_schemas: Record<string, ColumnSchema[]> }
interface PreviewResult { columns: string[]; rows: Record<string, unknown>[]; note?: string; rows_in_sample?: number }

const LIBRARY: { category: Category; items: NodeType[] }[] = (["SOURCES", "TRANSFORM", "QUALITY", "DESTINATIONS"] as Category[])
  .map((category) => ({ category, items: (Object.keys(NODE_META) as NodeType[]).filter((t) => NODE_META[t].category === category) }));

const toFlow = (g: Graph): { nodes: Node<PNodeData>[]; edges: Edge[] } => ({
  nodes: g.nodes.map((n) => ({ id: n.id, type: "pipeline", position: n.position, data: { type: n.type, label: n.label || NODE_META[n.type].label, config: n.config } })),
  edges: g.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.source_handle ? { sourceHandle: e.source_handle } : {}) })),
});
const toSpec = (nodes: Node<PNodeData>[], edges: Edge[]): Graph => ({
  nodes: nodes.map((n) => ({ id: n.id, type: n.data.type, label: n.data.label, position: { x: Math.round(n.position.x), y: Math.round(n.position.y) }, config: n.data.config })),
  edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, ...(e.sourceHandle ? { source_handle: e.sourceHandle } : {}) })),
});

function subtitleFor(n: { type: NodeType; config: Record<string, unknown> }, datasets: Dataset[]): string {
  const c = n.config;
  switch (n.type) {
    case "dataset_input": return datasets.find((d) => d.id === c.dataset_id)?.qualified_name || "select a dataset";
    case "output": return c.name ? `${c.layer}.${c.name}` : "configure output";
    case "filter": return String(c.condition || "condition…").slice(0, 28);
    case "dedupe": return `keys: ${((c.keys as string[]) || []).join(", ") || "…"}`;
    case "quality_check": return `${((c.rules as unknown[]) || []).length} assertion(s)`;
    case "incremental_input": return `${datasets.find((d) => d.id === c.dataset_id)?.qualified_name || "dataset"} > ${c.watermark_column || "…"}`;
    case "file_drop_input": return c.folder ? `dropzone/${c.folder}/` : "choose a folder";
    case "date_dimension": return `${c.start || "…"} → ${c.end || "…"}`;
    case "branch": return String(c.condition || "condition…").slice(0, 28);
    case "derived_column": return ((c.columns as { name: string }[]) || []).map((x) => x.name).filter(Boolean).join(", ") || "new columns";
    case "export_file": return `${c.filename || "export"}.${c.format || "csv"}`;
    case "email_report": return String(c.to || "recipients…").slice(0, 28);
    case "rest_output": return String(c.url || c.path || "endpoint…").replace(/^https?:\/\//, "").slice(0, 28);
    case "sort_limit": return c.limit ? `top ${c.limit}` : "sort";
    case "scd2": return c.target_name ? `history of ${c.target_name}` : "history";
    default: return NODE_META[n.type]?.label || n.type;
  }
}

function Editor() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const notify = useToast();
  const flow = useReactFlow();
  const readOnly = !can("pipelines.write");
  const { data: pipeline, error, mutate } = useSWR<PipelineDetail>(`/pipelines/${id}`);
  const { data: datasets } = useSWR<Dataset[]>("/datasets");
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<PNodeData>>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [libSearch, setLibSearch] = useState("");
  const [validation, setValidation] = useState<Validation | null>(null);
  const [validating, setValidating] = useState(false);
  const [saveState, setSaveState] = useState<"saved" | "unsaved" | "saving">("saved");
  const [runId, setRunId] = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [busy, setBusy] = useState<"run" | "publish" | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [autoOpen, setAutoOpen] = useState(false);
  const [paramsOpen, setParamsOpen] = useState(false);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const { data: automation, mutate: mutateAutomation } = useSWR<AutomationInfo>(`/pipelines/${id}/automation`);
  const loaded = useRef(false);

  const { data: run } = useSWR<RunDetail>(runId ? `/runs/${runId}` : null, {
    refreshInterval: (d) => (!d || d.status === "queued" || d.status === "running" ? 1000 : 0),
  });

  useEffect(() => {
    if (pipeline && !loaded.current) {
      const f = toFlow(pipeline.graph);
      setNodes(f.nodes);
      setEdges(f.edges);
      loaded.current = true;
    }
  }, [pipeline, setNodes, setEdges]);

  const spec = useMemo(() => toSpec(nodes, edges), [nodes, edges]);
  const logicKey = useMemo(() => JSON.stringify({ n: spec.nodes.map((n) => [n.id, n.type, n.config]), e: spec.edges.map((e) => [e.source, e.target]) }), [spec]);
  const layoutKey = useMemo(() => JSON.stringify(spec), [spec]);

  const save = useCallback(async () => {
    await api(`/pipelines/${id}`, { method: "PUT", body: { graph: spec } });
    await mutate();
    setSaveState("saved");
  }, [id, spec, mutate]);

  // debounced autosave of the draft
  useEffect(() => {
    if (!loaded.current || readOnly) return;
    if (JSON.stringify(pipeline?.graph) === layoutKey) return;
    setSaveState("unsaved");
    const t = setTimeout(async () => {
      setSaveState("saving");
      try {
        await save();
      } catch (e) {
        setSaveState("unsaved");
        notify(e instanceof Error ? e.message : String(e), "error");
      }
    }, 1200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutKey]);

  // debounced validation (dry run in DuckDB on the backend) whenever the logic changes
  const validate = useCallback(async (silent = true) => {
    setValidating(true);
    try {
      const v = await api<Validation>(`/pipelines/validate?pipeline_id=${id}`, { method: "POST", body: spec });
      setValidation(v);
      if (!silent) {
        const n = v.issues.filter((i) => i.severity === "error").length;
        notify(v.valid ? "Valid: graph, expressions and schemas are consistent" : `${n} error(s) found`, v.valid ? "success" : "error");
      }
      return v;
    } catch (e) {
      if (!silent) notify(e instanceof Error ? e.message : String(e), "error");
      return null;
    } finally {
      setValidating(false);
    }
  }, [spec, notify, id]);
  useEffect(() => {
    if (!loaded.current) return;
    const t = setTimeout(() => validate(true), 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logicKey]);

  const displayNodes = useMemo(() => nodes.map((n) => {
    const st = run?.node_status?.[n.id];
    const issues = (validation?.issues || []).filter((i) => i.node_id === n.id && i.severity === "error").map((i) => i.message);
    return { ...n, data: { ...n.data, subtitle: subtitleFor(n.data, datasets || []), issues, runStatus: st?.status, runRows: st?.rows ?? null } };
  }), [nodes, run, validation, datasets]);
  const active = run?.status === "running" || run?.status === "queued";
  const displayEdges = useMemo(() => edges.map((e) => ({ ...e, animated: active, style: active ? { stroke: "#00D4FF" } : undefined })), [edges, active]);

  const onConnect = useCallback((c: Connection) => setEdges((es) => addEdge({ ...c, id: `e_${Date.now().toString(36)}` }, es)), [setEdges]);

  const addNode = (type: NodeType) => {
    const nid = `${type.split("_")[0]}_${Date.now().toString(36)}`;
    const sel = nodes.find((n) => n.id === selectedId);
    const pos = sel ? { x: sel.position.x + 240, y: sel.position.y } : flow.screenToFlowPosition({ x: window.innerWidth / 2 - 100, y: window.innerHeight / 2 - 60 });
    setNodes((ns) => [...ns, { id: nid, type: "pipeline", position: pos, data: { type, label: NODE_META[type].label, config: defaultConfig(type) } }]);
    const canFeed = sel && !TERMINAL_TYPES.includes(sel.data.type) && sel.data.type !== "output" && NODE_META[type].category !== "SOURCES";
    if (sel && canFeed) setEdges((es) => [...es, { id: `e_${Date.now().toString(36)}`, source: sel.id, target: nid, ...(sel.data.type === "branch" ? { sourceHandle: "true" } : {}) }]);
    setSelectedId(nid);
  };

  const selected = nodes.find((n) => n.id === selectedId);
  const updateSelected = (patch: Partial<PNodeData>) => setNodes((ns) => ns.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, ...patch } } : n)));
  const deleteSelected = () => {
    if (!selected) return;
    setNodes((ns) => ns.filter((n) => n.id !== selected.id));
    setEdges((es) => es.filter((e) => e.source !== selected.id && e.target !== selected.id));
    setSelectedId(null);
  };
  const duplicateSelected = () => {
    if (!selected) return;
    const nid = `${selected.data.type.split("_")[0]}_${Date.now().toString(36)}`;
    setNodes((ns) => [...ns, { ...selected, id: nid, selected: false, position: { x: selected.position.x + 40, y: selected.position.y + 60 },
      data: { ...selected.data, label: `${selected.data.label} (copy)`, config: JSON.parse(JSON.stringify(selected.data.config)) } }]);
    setSelectedId(nid);
  };

  const upstreamOf = (nid: string) => edges.filter((e) => e.target === nid).map((e) => {
    const n = nodes.find((x) => x.id === e.source);
    return { id: e.source, label: n?.data.label || e.source, schema: validation?.node_schemas?.[e.source] || [] };
  });
  const paramDefs = automation?.settings.params || [];

  const publish = async () => {
    setBusy("publish");
    try {
      await save();
      const v = await api<{ version: number }>(`/pipelines/${id}/publish`, { method: "POST" });
      notify(`Published as version ${v.version}`);
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
      validate(true);
    } finally {
      setBusy(null);
    }
  };

  const runPipeline = async (withParams?: Record<string, string>) => {
    if (!withParams && paramDefs.length) {
      setParamValues({});
      setParamsOpen(true);
      return;
    }
    setParamsOpen(false);
    setBusy("run");
    try {
      const v = await validate(true);
      if (!v?.valid) {
        notify("Fix the validation errors before running", "error");
        return;
      }
      await save();
      const params = Object.fromEntries(Object.entries(withParams || {}).filter(([, v]) => v !== ""));
      const res = await api<{ id: string }>(`/pipelines/${id}/run`, { method: "POST", body: { params } });
      setRunId(res.id);
      setShowLogs(true);
      notify("Run started");
      mutate();
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  };

  const previewNode = async () => {
    if (!selected) return;
    setPreviewLoading(true);
    try {
      setPreview(await api<PreviewResult>("/pipelines/preview", { method: "POST", body: { graph: spec, node_id: selected.id, limit: 50, pipeline_id: id } }));
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setPreviewLoading(false);
    }
  };

  const remove = async () => {
    try {
      await api(`/pipelines/${id}`, { method: "DELETE" });
      notify("Pipeline deleted");
      router.push("/pipelines");
    } catch (e) {
      notify(e instanceof ApiError ? e.message : String(e), "error");
    }
  };

  useEffect(() => {
    if (run && (run.status === "succeeded" || run.status === "failed")) {
      notify(run.status === "succeeded"
        ? `Run succeeded in ${duration(run.duration_ms)} · ${(run.records_out ?? 0).toLocaleString()} rows written`
        : `Run failed: ${run.error_message}`, run.status === "succeeded" ? "success" : "error");
      mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status]);

  if (error) return <div className="p-6"><ErrorState error={error} onRetry={() => mutate()} /></div>;
  if (!pipeline) return <LoadingState />;

  const errorsCount = validation?.issues.filter((i) => i.severity === "error").length ?? 0;
  const graphIssues = validation?.issues.filter((i) => !i.node_id) || [];
  const nodeErrors: Record<string, string> = Object.fromEntries((validation?.issues || []).filter((i) => i.node_id === selectedId && i.field).map((i) => [i.field!, i.message]));
  const stateLabel = active ? "Running" : pipeline.status === "published" ? `Published v${pipeline.current_version}` : "Draft";

  return (
    <div className="flex flex-col h-[calc(100vh-70px)] select-none">
      <div className="h-12 px-6 border-b border-orbit-border bg-orbit-bg flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Link href="/pipelines" className="text-orbit-muted hover:text-white"><ChevronLeft className="w-4 h-4" /></Link>
          <span className="text-sm font-semibold text-white truncate">{pipeline.name}</span>
          <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border",
            stateLabel === "Running" ? "bg-orbit-purple/20 text-orbit-purpleLight border-orbit-purple/40 animate-pulse"
              : stateLabel === "Draft" ? "bg-orbit-amber/15 text-orbit-amber border-orbit-amber/30" : "bg-orbit-green/15 text-orbit-green border-orbit-green/30")}>• {stateLabel}</span>
          <span className="text-xs text-orbit-subtle hidden lg:inline">
            {readOnly ? "Read-only (your role cannot edit)" : saveState === "saved" ? `Draft saved ${ago(pipeline.updated_at)}` : saveState === "saving" ? "Saving…" : "Unsaved changes"}
          </span>
          {validation && (
            <span className={cn("text-xs", validation.valid ? "text-orbit-green" : "text-orbit-redLight")}>
              {validating ? "validating…" : validation.valid ? "✓ valid" : `${errorsCount} error(s)`}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {run && (
            <button onClick={() => setShowLogs((s) => !s)} className="text-xs text-orbit-muted hover:text-white flex items-center gap-1.5">
              <History className="w-3.5 h-3.5" />Run {run.id.slice(0, 6)} <StatusPill status={run.status} />
            </button>
          )}
          <button onClick={() => setAutoOpen(true)} className="text-xs text-orbit-muted hover:text-white flex items-center gap-1.5" title="Schedule, triggers, retry, notifications, parameters">
            <CalendarClock className="w-3.5 h-3.5" />{automation ? automation.schedule_text : "Automation"}
            {automation?.next_run_at && <span className="text-orbit-subtle">· next {ago(automation.next_run_at)}</span>}
          </button>
          <Button size="sm" icon={CalendarClock} onClick={() => setAutoOpen(true)}>Automation</Button>
          <Button size="sm" icon={ShieldCheck} loading={validating} onClick={() => validate(false)}>Validate</Button>
          {!readOnly && <Button size="sm" icon={Upload} loading={busy === "publish"} onClick={publish}>Publish</Button>}
          {can("pipelines.run") && (
            <Button variant="primary" icon={Play} loading={busy === "run"} disabled={active} onClick={() => runPipeline()}>Run pipeline</Button>
          )}
          {!readOnly && (
            <button onClick={() => setConfirmDelete(true)} title="Delete pipeline" className="p-1.5 rounded text-orbit-subtle hover:text-orbit-redLight">
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {graphIssues.length > 0 && (
        <div className="px-6 py-1.5 border-b border-orbit-border bg-orbit-red/5 text-[11px] text-orbit-redLight flex flex-wrap gap-x-4">
          {graphIssues.map((i) => <span key={i.message}>• {i.message}</span>)}
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        <div className="w-[216px] shrink-0 bg-orbit-panel border-r border-orbit-border p-3.5 overflow-y-auto space-y-4">
          <div className="orbit-label">NODES</div>
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-orbit-subtle absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input value={libSearch} onChange={(e) => setLibSearch(e.target.value)} placeholder="Search nodes"
              className="w-full pl-8 pr-2.5 py-1.5 rounded-lg bg-orbit-card border border-orbit-border text-xs text-white placeholder-orbit-subtle focus:outline-none" />
          </div>
          {LIBRARY.map((sec) => (
            <div key={sec.category} className="space-y-1.5">
              <div className="orbit-label pt-1">{sec.category}</div>
              {sec.items.filter((t) => NODE_META[t].label.toLowerCase().includes(libSearch.toLowerCase())).map((t) => {
                const Icon = NODE_META[t].icon;
                return (
                  <button key={t} disabled={readOnly} onClick={() => addNode(t)} title={NODE_META[t].help}
                    className="w-full px-2.5 py-2 rounded-lg bg-orbit-card/60 hover:bg-orbit-card border border-transparent hover:border-orbit-border flex items-center justify-between text-xs text-[#E2E8F0] transition group disabled:opacity-40">
                    <span className="flex items-center gap-2"><Icon className="w-3.5 h-3.5 text-orbit-muted group-hover:text-orbit-purple" />{NODE_META[t].label}</span>
                    <Plus className="w-3 h-3 text-orbit-subtle group-hover:text-white" />
                  </button>
                );
              })}
            </div>
          ))}
          <p className="text-[10px] text-orbit-subtle leading-relaxed">
            Tip: select a node, then add another — they are connected automatically. Drag from a right port to a left port to connect manually. Delete key removes the selection.
          </p>
        </div>

        <div className="flex-1 relative etl-dot-grid">
          <ReactFlow
            nodes={displayNodes} edges={displayEdges} nodeTypes={nodeTypes}
            onNodesChange={readOnly ? undefined : onNodesChange} onEdgesChange={readOnly ? undefined : onEdgesChange} onConnect={readOnly ? undefined : onConnect}
            onNodeClick={(_, n) => setSelectedId(n.id)} onPaneClick={() => setSelectedId(null)}
            nodesDraggable={!readOnly} nodesConnectable={!readOnly} deleteKeyCode={readOnly ? null : ["Delete"]}
            fitView fitViewOptions={{ padding: 0.25, maxZoom: 1 }} proOptions={{ hideAttribution: true }} colorMode="dark" minZoom={0.3}
          >
            <Background color="transparent" />
            <Controls position="bottom-left" showInteractive={false} />
          </ReactFlow>
          {nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <div className="text-center text-xs text-orbit-muted">Add a <b className="text-white">Dataset input</b> from the library to start.</div>
            </div>
          )}
          {showLogs && run && (
            <div className="absolute bottom-0 left-0 right-0 max-h-[45%] bg-orbit-panel/95 backdrop-blur border-t border-orbit-border p-3 flex flex-col gap-2">
              <div className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-2 text-white">
                  Run {run.id.slice(0, 8)} · version {run.pipeline_version} · attempt {run.attempt} <StatusPill status={run.status} />
                  <span className="text-orbit-muted">{duration(run.duration_ms)}</span>
                </span>
                <span className="flex items-center gap-3">
                  <Link href={`/runs/${run.id}`} className="text-orbit-purpleLight hover:text-white">Open run details</Link>
                  <button onClick={() => setShowLogs(false)} className="text-orbit-muted hover:text-white" aria-label="Close"><X className="w-3.5 h-3.5" /></button>
                </span>
              </div>
              {run.error_message && <InlineAlert tone="red">{run.error_class}: {run.error_message}</InlineAlert>}
              <RunLogs logs={run.logs} className="!max-h-56" />
            </div>
          )}
        </div>

        {selected && (
          <div className="w-[300px] shrink-0 bg-orbit-panel border-l border-orbit-border p-4 overflow-y-auto space-y-4">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="orbit-label">SELECTED NODE · {NODE_META[selected.data.type].label}</div>
                <input value={selected.data.label} disabled={readOnly} onChange={(e) => updateSelected({ label: e.target.value })}
                  className="w-full text-sm font-bold text-white bg-transparent focus:outline-none border-b border-transparent focus:border-orbit-purple mt-0.5" />
              </div>
              {!readOnly && (
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={duplicateSelected} title="Duplicate node" className="p-1.5 rounded hover:bg-orbit-card text-orbit-muted hover:text-white"><Copy className="w-3.5 h-3.5" /></button>
                  <button onClick={deleteSelected} title="Delete node" className="p-1.5 rounded hover:bg-orbit-card text-orbit-muted hover:text-orbit-redLight"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              )}
            </div>
            <p className="text-[11px] text-orbit-subtle">{NODE_META[selected.data.type].help}</p>
            {(validation?.issues || []).filter((i) => i.node_id === selected.id).map((i) => (
              <InlineAlert key={i.message} tone={i.severity === "error" ? "red" : "amber"}>{i.message}</InlineAlert>
            ))}
            <NodeConfig data={selected.data} upstream={upstreamOf(selected.id)} datasets={datasets || []} readOnly={readOnly} errors={nodeErrors}
              onChange={(config) => updateSelected({ config })} />
            <div className="space-y-2">
              <div className="orbit-label">OUTPUT SCHEMA</div>
              {(validation?.node_schemas?.[selected.id] || []).length === 0 ? <div className="text-[11px] text-orbit-subtle">Available once the node validates.</div> : (
                <div className="space-y-1.5">
                  {validation!.node_schemas[selected.id].map((c) => (
                    <div key={c.name} className="flex items-center gap-2 text-xs font-mono">
                      <Check className="w-3.5 h-3.5 text-orbit-green shrink-0" /><span className="text-white truncate">{c.name}</span>
                      <span className="text-orbit-subtle truncate">{c.type.toLowerCase()}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {run?.node_status?.[selected.id]?.quality && (
              <div className="space-y-1.5">
                <div className="orbit-label">LAST RUN ASSERTIONS</div>
                {run.node_status[selected.id].quality!.map((q) => (
                  <div key={q.name} className="text-[11px] flex justify-between gap-2">
                    <span className="text-white truncate">{q.name}</span>
                    <span className={q.passed ? "text-orbit-green" : "text-orbit-redLight"}>{q.passed ? "passed" : `${q.failed}/${q.total} failed`}</span>
                  </div>
                ))}
              </div>
            )}
            <Button className="w-full" icon={Eye} loading={previewLoading} onClick={previewNode}>Preview data at this node</Button>
          </div>
        )}
      </div>

      <Modal open={!!preview} onClose={() => setPreview(null)} title={`Preview · ${selected?.data.label ?? ""}`}
        subtitle={preview ? `${preview.rows_in_sample?.toLocaleString()} row(s) at this step · ${preview.note}` : undefined} width="max-w-5xl">
        {preview && <DataTable columns={preview.columns} rows={preview.rows} maxHeight="60vh" />}
      </Modal>
      <AutomationDrawer pipelineId={id} open={autoOpen} onClose={() => setAutoOpen(false)} onSaved={() => { mutateAutomation(); mutate(); validate(true); }} />
      <Modal open={paramsOpen} onClose={() => setParamsOpen(false)} title="Run with parameters" subtitle="Empty fields use the default value."
        footer={<><Button variant="ghost" onClick={() => setParamsOpen(false)}>Cancel</Button><Button variant="primary" icon={Play} onClick={() => runPipeline(paramValues)}>Run</Button></>}>
        <RunParamsModalBody defs={paramDefs} values={paramValues} onChange={setParamValues} />
      </Modal>
      <ConfirmModal open={confirmDelete} onClose={() => setConfirmDelete(false)} onConfirm={remove} danger title="Delete this pipeline?"
        confirmLabel="Delete" message="The pipeline, its versions and its run history are deleted. Datasets it already wrote are kept." />
    </div>
  );
}

export default function Page() {
  return <ReactFlowProvider><Editor /></ReactFlowProvider>;
}
