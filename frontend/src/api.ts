import type {
  NodeTypeMetadata,
  GraphDef,
  PreviewEvent,
  DeployRequest,
  DeployEvent,
  SetupStatusResponse,
  SetupInfoResponse,
  SetupValidateResponse,
  DiscoveryResponse,
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

/** Stream a graph preview as an SSE feed. The callback fires for every event
 *  (token deltas + the terminal ``done`` / ``interrupt`` / ``error``). The
 *  promise resolves when the stream closes. */
export async function streamPreview(
  graph: GraphDef,
  inputMessage: string,
  threadId: string | null | undefined,
  resumeValue: string | null | undefined,
  pat: string | null | undefined,
  onEvent: (event: PreviewEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const body: Record<string, unknown> = {
    graph,
    input_message: inputMessage,
    thread_id: threadId ?? null,
    resume_value: resumeValue ?? null,
  };
  if (pat) body.pat = pat;

  const res = await fetch(`${BASE}/graph/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(`Preview failed: ${res.status} ${res.statusText}`);

  await consumeSSE(res, (data) => onEvent(JSON.parse(data) as PreviewEvent));
}

/** Read an SSE response body, invoking ``onData`` once per ``data:`` line. */
async function consumeSSE(res: Response, onData: (data: string) => void): Promise<void> {
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
        onData(trimmed.slice(6));
      } catch {
        // skip malformed events
      }
    }
  }
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
  if (!res.ok) throw new Error(`Deploy request failed: ${res.status} ${res.statusText}`);
  await consumeSSE(res, (data) => onEvent(JSON.parse(data) as DeployEvent));
}

// ── Models listing ─────────────────────────────────────────────────────────

export async function fetchModels(): Promise<import("./types").ModelsResponse> {
  const res = await fetch(`${BASE}/models`);
  if (!res.ok) throw new Error("Failed to fetch models");
  return res.json();
}

export async function fetchModelGraph(runId: string): Promise<import("./types").GraphDef> {
  const res = await fetch(`${BASE}/models/${runId}/graph`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: "Failed to load graph" }));
    throw new Error(detail.detail || "Failed to load graph");
  }
  return res.json();
}

// ── Resource discovery ────────────────────────────────────────────────────

const _discoveryCache = new Map<string, DiscoveryResponse>();

export function getDiscoveryCache(endpoint: string): DiscoveryResponse | null {
  return _discoveryCache.get(endpoint) ?? null;
}

export async function fetchDiscoveryOptions(endpoint: string): Promise<DiscoveryResponse> {
  const cached = _discoveryCache.get(endpoint);
  if (cached) return cached;
  try {
    const res = await fetch(endpoint);
    if (!res.ok) return { options: [], error: `HTTP ${res.status}` };
    const data: DiscoveryResponse = await res.json();
    if (!data.error) _discoveryCache.set(endpoint, data);
    return data;
  } catch {
    return { options: [], error: "Network error" };
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

export async function validateSetup(): Promise<SetupValidateResponse> {
  const res = await fetch(`${BASE}/setup/validate`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to validate setup");
  return res.json();
}

export async function autoSetup(): Promise<SetupValidateResponse> {
  const res = await fetch(`${BASE}/setup/auto-setup`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to auto-setup");
  return res.json();
}
