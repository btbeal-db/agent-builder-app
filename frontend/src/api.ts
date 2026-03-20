import type { NodeTypeMetadata, GraphDef, PreviewResponse, ExportResponse, DeployRequest, DeployResponse } from "./types";

const BASE = "/api";

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
  inputMessage: string
): Promise<PreviewResponse> {
  const res = await fetch(`${BASE}/graph/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph, input_message: inputMessage }),
  });
  return res.json();
}

export async function exportGraph(graph: GraphDef): Promise<ExportResponse> {
  const res = await fetch(`${BASE}/graph/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(graph),
  });
  return res.json();
}

export async function deployGraph(req: DeployRequest): Promise<DeployResponse> {
  const res = await fetch(`${BASE}/graph/deploy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return res.json();
}
