import type { NodeTypeMetadata, GraphDef, PreviewResponse, DeployRequest, AppConfig } from "./types";

const BASE = "/api";

export async function fetchAppConfig(): Promise<AppConfig> {
  const res = await fetch(`${BASE}/config`);
  if (!res.ok) throw new Error("Failed to fetch app config");
  return res.json();
}

export async function fetchNodeTypes(): Promise<NodeTypeMetadata[]> {
  const res = await fetch(`${BASE}/nodes`);
  if (!res.ok) throw new Error("Failed to fetch node types");
  return res.json();
}

export async function validateGraph(graph: GraphDef) {
  const res = await fetch(`${BASE}/graph/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(graph),
  });
  return res.json() as Promise<{ valid: boolean; errors: string[] }>;
}

export async function previewGraph(
  graph: GraphDef,
  inputMessage: string,
  threadId?: string | null,
  resumeValue?: string | null,
): Promise<PreviewResponse> {
  const res = await fetch(`${BASE}/graph/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      graph,
      input_message: inputMessage,
      thread_id: threadId ?? null,
      resume_value: resumeValue ?? null,
    }),
  });
  return res.json();
}

export async function loadGraphFromRun(runId: string): Promise<{ success: boolean; graph?: GraphDef; error?: string }> {
  const res = await fetch(`${BASE}/graph/load-from-run?run_id=${encodeURIComponent(runId)}`);
  return res.json();
}

export interface AIChatResponse {
  message: string;
  graph: GraphDef | null;
  error: string | null;
}

export async function sendAIChatMessage(
  messages: Array<{ role: string; content: string }>,
  currentGraph: GraphDef | null,
): Promise<AIChatResponse> {
  const res = await fetch(`${BASE}/ai-chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages,
      current_graph: currentGraph,
    }),
  });
  if (!res.ok) throw new Error(`AI Chat request failed: ${res.status}`);
  return res.json();
}

export interface DeploySubmitResponse {
  run_id: number;
  model_name: string;
  endpoint_name: string;
}

export interface DeployStatusResponse {
  status: "running" | "success" | "failed";
  lifecycle?: string;
  // On success:
  run_id?: string;
  model_version?: string;
  endpoint_url?: string;
  // On failure:
  error?: string;
}

export async function submitDeploy(req: DeployRequest): Promise<DeploySubmitResponse> {
  const res = await fetch(`${BASE}/graph/deploy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Deploy failed: ${res.status}`);
  }
  return res.json();
}

export async function pollDeployStatus(runId: number): Promise<DeployStatusResponse> {
  const res = await fetch(`${BASE}/graph/deploy/status?run_id=${runId}`);
  if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
  return res.json();
}
