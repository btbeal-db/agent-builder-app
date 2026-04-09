import type {
  NodeTypeMetadata,
  GraphDef,
  PreviewResponse,
  DeployRequest,
  DeployEvent,
  SetupStatusResponse,
  SetupInfoResponse,
  SetupGrantResponse,
  SetupValidateResponse,
} from "./types";

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

export async function deployGraphStream(
  req: DeployRequest,
  onEvent: (event: DeployEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/graph/deploy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!res.ok) {
    throw new Error(`Deploy request failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n\n");
    buffer = lines.pop()!;

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(trimmed.slice(6)) as DeployEvent);
      } catch {
        // skip malformed events
      }
    }
  }
}

// ── Setup (MLflow experiment one-time config) ──────────────────────────────

export async function getSetupStatus(): Promise<SetupStatusResponse> {
  const res = await fetch(`${BASE}/setup/status`);
  if (!res.ok) throw new Error("Failed to fetch setup status");
  return res.json();
}

export async function getSetupInfo(): Promise<SetupInfoResponse> {
  const res = await fetch(`${BASE}/setup/info`);
  if (!res.ok) throw new Error("Failed to fetch setup info");
  return res.json();
}

export async function grantSpAccess(experimentPath: string): Promise<SetupGrantResponse> {
  const res = await fetch(`${BASE}/setup/grant-access`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ experiment_path: experimentPath }),
  });
  if (!res.ok) throw new Error("Failed to grant SP access");
  return res.json();
}

export async function validateSetup(experimentPath: string): Promise<SetupValidateResponse> {
  const res = await fetch(`${BASE}/setup/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ experiment_path: experimentPath }),
  });
  if (!res.ok) throw new Error("Failed to validate setup");
  return res.json();
}
