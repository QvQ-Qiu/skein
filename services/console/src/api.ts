import { useConsoleStore } from "./store";

const TOKEN_KEY = "skein.token";

export function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function writeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface Ticket {
  id: string;
  code: string;
  title: string;
  status: string;
  thread_id: string | null;
  updated_at: number;
}

export interface Assignment {
  ticket_id: string;
  worker_id: string;
  state: string;
  lease_until: number | null;
  result: string | null;
  updated_at: number;
}

export interface Dispatch {
  id: string;
  ticket_id: string;
  node_id: string;
  worker_id: string | null;
  state: string;
  attempt: number;
  detail: unknown;
  updated_at: number;
}

export interface ThreadRecord {
  id: string;
  origin: string;
  origin_id: string;
  created_at: number;
}

export interface TicketDetail extends Ticket {
  dedup_key: string | null;
  created_at: number;
}

export interface TicketDetailResponse {
  ticket: TicketDetail;
  thread: ThreadRecord | null;
  assignments: Assignment[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
}

export interface Signal {
  kind: string;
  id: string;
  ticket_id?: string;
  node_id?: string;
  age_s?: number;
  job?: string;
  day?: string;
  detail?: string | null;
  effect_kind?: string;
  object?: string;
}

export interface NodeRecord {
  id: string;
  name: string;
  host: string;
  platforms: string;
  status: string;
  max_workers: number;
  last_seen: number;
}

export interface RunRecord {
  id: string;
  job: string;
  day: string;
  status: string;
  detail: string | null;
}

export interface PromptPointer {
  part: string;
  stable_revision: number;
  canary_revision: number | null;
  frozen_until: number | null;
  editable: boolean;
}

export interface Summary {
  events: { total: number; by_verdict: Record<string, number> };
  threads: number;
  tickets: { total: number; by_status: Record<string, number> };
  effects: { by_state: Record<string, number> };
  sessions: number;
  runs: { by_status: Record<string, number> };
  shadow: { by_action: Record<string, number> };
}

export interface ControlEvent {
  id: string;
  origin: string;
  origin_id: string;
  thread_id: string | null;
  payload: unknown;
  verdict: string;
  created_at: number;
}

export interface SessionRecord {
  id: string;
  ticket_id: string | null;
  node_id: string | null;
  pinned: number;
  created_at: number;
  updated_at: number;
  turn_count: number;
  last_turn_at?: number | null;
}

export interface TurnRecord {
  session_id: string;
  turn_index: number;
  sha256: string;
  bytes: number;
  head_sha: string | null;
  branch: string | null;
  created_at: number;
  text?: string;
}

export interface SessionTurnsResponse {
  session: SessionRecord;
  turns: TurnRecord[];
}

export interface ThreadDetailResponse {
  thread: ThreadRecord;
  events: ControlEvent[];
  tickets: Ticket[];
}

export interface AuditRecord {
  id: number;
  actor: string;
  action: string;
  detail: unknown;
  created_at: number;
}

export interface SourcePolicy {
  accept: boolean;
  require_mention: boolean;
  record_unmentioned: boolean;
  min_text_chars: number;
}

export interface AdmissionPolicies {
  default: SourcePolicy;
  sources: Record<string, SourcePolicy>;
}

export interface LogRecord {
  at: number;
  level: string;
  component: string;
  message: string;
}

export interface SubscriptionRecord {
  thread_id: string;
  ticket_id: string;
  state: string;
  created_at: number;
}

export interface Topology {
  nodes: NodeRecord[];
  tickets: Ticket[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
  subscriptions: SubscriptionRecord[];
}

export interface TicketList {
  tickets: Ticket[];
}

export interface SignalList {
  signals: Signal[];
}

export interface NodeList {
  nodes: NodeRecord[];
}

export interface RunList {
  runs: RunRecord[];
}

export interface PromptList {
  prompts: PromptPointer[];
}

export interface EventList {
  events: ControlEvent[];
}

export interface SessionList {
  sessions: SessionRecord[];
}

async function detailOf(response: Response): Promise<string> {
  const fallback = `HTTP ${response.status}`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail;
    }
  } catch {
    return fallback;
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("X-Skein-Token", readToken());

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(0, "网络错误：无法连接控制面");
  }

  if (response.status === 401) {
    useConsoleStore().clearConnection();
    throw new ApiError(401, "请填写控制面令牌");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await detailOf(response));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function withQuery(path: string, query: URLSearchParams): string {
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

export function fetchSummary(): Promise<Summary> {
  return request<Summary>("/internal/summary");
}

export function fetchTickets(params?: { status?: string; limit?: number }): Promise<TicketList> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.limit != null) query.set("limit", String(params.limit));
  return request<TicketList>(withQuery("/internal/tickets", query));
}

export function fetchTicket(id: string): Promise<TicketDetailResponse> {
  return request<TicketDetailResponse>(`/internal/tickets/${encodeURIComponent(id)}`);
}

export function fetchSignals(): Promise<SignalList> {
  return request<SignalList>("/internal/signals");
}

export function fetchNodes(): Promise<NodeList> {
  return request<NodeList>("/internal/nodes");
}

export function fetchRuns(limit = 20): Promise<RunList> {
  const query = new URLSearchParams({ limit: String(limit) });
  return request<RunList>(withQuery("/internal/runs", query));
}

export function fetchPrompts(): Promise<PromptList> {
  return request<PromptList>("/internal/prompts");
}

export function fetchEvents(params?: {
  since?: number;
  limit?: number;
  verdict?: string;
  origin?: string;
}): Promise<EventList> {
  const query = new URLSearchParams();
  if (params?.since != null) query.set("since", String(params.since));
  if (params?.limit != null) query.set("limit", String(params.limit));
  if (params?.verdict) query.set("verdict", params.verdict);
  if (params?.origin) query.set("origin", params.origin);
  return request<EventList>(withQuery("/internal/events", query));
}

export function fetchSessions(params?: {
  node_id?: string;
  ticket_id?: string;
  limit?: number;
}): Promise<SessionList> {
  const query = new URLSearchParams();
  if (params?.node_id) query.set("node_id", params.node_id);
  if (params?.ticket_id) query.set("ticket_id", params.ticket_id);
  if (params?.limit != null) query.set("limit", String(params.limit));
  return request<SessionList>(withQuery("/internal/sessions", query));
}

export function fetchSessionTurns(
  id: string,
  includeText = false,
): Promise<SessionTurnsResponse> {
  const query = new URLSearchParams();
  if (includeText) query.set("include_text", "true");
  return request<SessionTurnsResponse>(
    withQuery(`/internal/sessions/${encodeURIComponent(id)}/turns`, query),
  );
}

export function fetchThread(id: string): Promise<ThreadDetailResponse> {
  return request<ThreadDetailResponse>(`/internal/threads/${encodeURIComponent(id)}`);
}

export function fetchAdmission(): Promise<AdmissionPolicies> {
  return request<AdmissionPolicies>("/internal/admission");
}

export function saveAdmission(body: AdmissionPolicies): Promise<AdmissionPolicies> {
  return request<AdmissionPolicies>("/internal/admission", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function fetchAudit(limit = 100): Promise<{ audit: AuditRecord[] }> {
  const query = new URLSearchParams({ limit: String(limit) });
  return request<{ audit: AuditRecord[] }>(withQuery("/internal/audit", query));
}

export function fetchLogs(params?: {
  limit?: number;
  component?: string;
  level?: string;
}): Promise<{ logs: LogRecord[] }> {
  const query = new URLSearchParams();
  if (params?.limit != null) query.set("limit", String(params.limit));
  if (params?.component) query.set("component", params.component);
  if (params?.level) query.set("level", params.level);
  return request<{ logs: LogRecord[] }>(withQuery("/internal/logs", query));
}

export function fetchTopology(): Promise<Topology> {
  return request<Topology>("/internal/topology");
}

export interface SessionUser {
  sub: string;
  name: string;
  email: string;
  internal: boolean;
  picture: string;
}

export interface SessionInfo {
  mode: "sso" | "token";
  user?: SessionUser;
}

export async function probeSession(): Promise<SessionInfo> {
  return request<SessionInfo>("/api/session");
}

export async function loginRequest(email: string, password: string): Promise<SessionInfo> {
  return request<SessionInfo>("/api/session/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function logoutRequest(): Promise<void> {
  await request<{ ok: boolean }>("/api/session/logout", { method: "POST" });
}
