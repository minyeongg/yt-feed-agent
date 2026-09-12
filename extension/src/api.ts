// 로컬 서버 클라이언트 (docs/05-구현가이드.md Phase 4, step 16; docs/03 §2).
//
// 인증 토큰은 chrome.storage.local에 저장한다(서버 실행 시 config/token에
// 생성되는 값을 사용자가 최초 1회 팝업에서 붙여넣는다 — 자동 페어링
// 플로우는 문서에 없어서 만들지 않았다). 서버가 꺼져 있어도 유튜브 페이지
// 자체는 절대 안 깨져야 한다(FR-X7) — 그래서 모든 호출 실패는 예외를
// 던지되, 호출부(content script)가 조용히 무시할 수 있는 형태로 던진다.

const BASE_URL = "http://127.0.0.1:8787";
const TOKEN_KEY = "ytfaToken";
const THREAD_ID_KEY = "ytfaThreadId";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface HealthResponse {
  status: string;
  version: string;
  db: string;
  worker: string;
  last_poll_at: string | null;
  embedder: string;
}

export interface Verdict {
  topics: string[];
  level: string;
  hands_on: number;
  one_liner: string;
}

export interface VideoCard {
  id: string;
  title: string;
  url: string;
  channel: { id: string; title: string; thumbnail_url: string };
  published_at: string;
  duration_sec: number | null;
  kind: "video" | "short" | "live" | "upcoming";
  thumbnail_url: string;
  summary: string | null;
  verdict: Verdict | null;
  state: "new" | "seen" | "watched" | "skipped" | "not_interested";
  categories: string[];
}

export interface FeedResponse {
  items: VideoCard[];
  next_cursor: string | null;
  total: number;
}

export interface FeedCountsResponse {
  total: number;
  by_category: Record<string, number>;
}

export interface Category {
  id: string;
  name: string;
  order: number;
  color: string | null;
  is_default: boolean;
  channel_count: number;
  new_video_count: number;
}

export interface SearchItem {
  video: {
    id: string;
    title: string;
    url: string;
    channel: { id: string; title: string };
    published_at: string;
    duration_sec: number | null;
    kind: string;
    thumbnail_url: string;
    summary: string | null;
    verdict: Verdict | null;
  };
  score: number;
  matched_by: string;
}

export interface SearchResponse {
  mode: string;
  query_used: string;
  items: SearchItem[];
  hint: string | null;
}

export interface BriefingPick {
  video: VideoCard | null;
  reason: string;
  score: number;
}

export interface BriefingResponse {
  generated_at: string;
  picks: BriefingPick[];
  remaining: number;
  cost_usd: number;
  cached: boolean;
}

export async function getToken(): Promise<string | null> {
  const result = await chrome.storage.local.get(TOKEN_KEY);
  return (result[TOKEN_KEY] as string | undefined) ?? null;
}

export async function setToken(token: string): Promise<void> {
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
}

export async function hasToken(): Promise<boolean> {
  return (await getToken()) !== null;
}

// 사이드패널 대화의 thread_id — 패널을 닫았다 열어도 같은 대화로
// 이어지도록 저장해둔다(step 24 체크포인터가 thread_id 기준).
export async function getThreadId(): Promise<string | null> {
  const result = await chrome.storage.local.get(THREAD_ID_KEY);
  return (result[THREAD_ID_KEY] as string | undefined) ?? null;
}

export async function setThreadId(threadId: string): Promise<void> {
  await chrome.storage.local.set({ [THREAD_ID_KEY]: threadId });
}

export async function clearThreadId(): Promise<void> {
  await chrome.storage.local.remove(THREAD_ID_KEY);
}

async function request<T>(path: string): Promise<T> {
  const token = await getToken();
  if (!token) {
    throw new ApiError("서버 토큰이 설정되지 않았습니다");
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { headers: { "X-YTFA-Token": token } });
  } catch {
    throw new ApiError("로컬 서버에 연결할 수 없습니다");
  }

  if (!res.ok) {
    throw new ApiError(`서버 오류 (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const token = await getToken();
  if (!token) {
    throw new ApiError("서버 토큰이 설정되지 않았습니다");
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "X-YTFA-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError("로컬 서버에 연결할 수 없습니다");
  }

  if (!res.ok) {
    throw new ApiError(`서버 오류 (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

export function checkHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

export function getFeedCounts(state = "new"): Promise<FeedCountsResponse> {
  return request<FeedCountsResponse>(`/feed/counts?state=${encodeURIComponent(state)}`);
}

export async function getCategories(): Promise<Category[]> {
  const res = await request<{ items: Category[] }>("/categories");
  return res.items;
}

export function getFeed(
  params: { category?: string; state?: string; includeShorts?: boolean; limit?: number } = {},
): Promise<FeedResponse> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.state) qs.set("state", params.state);
  if (params.includeShorts !== undefined) qs.set("include_shorts", String(params.includeShorts));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  return request<FeedResponse>(`/feed?${qs.toString()}`);
}

export function searchVideos(query: string, limit = 10): Promise<SearchResponse> {
  const qs = new URLSearchParams({ q: query, mode: "keyword", limit: String(limit) });
  return request<SearchResponse>(`/search?${qs.toString()}`);
}

export function getTodayBriefing(): Promise<BriefingResponse> {
  return request<BriefingResponse>("/briefing/today");
}

// --- 에이전트 대화 (SSE) — docs/05-구현가이드.md Phase 6 step 25 -------
//
// `EventSource`는 POST도 커스텀 헤더도 못 보내서(X-YTFA-Token 인증이
// 안 걸린다) 못 쓴다. `fetch()` + 스트림 리더로 SSE를 직접 파싱한다.

export type ChatEventName = "run_started" | "text" | "tool_call" | "tool_result" | "run_end";

export interface ChatEvent {
  event: ChatEventName | string;
  data: Record<string, unknown>;
}

function parseSSEBlock(raw: string): ChatEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event: eventName, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

export async function* streamChat(message: string, threadId: string | null): AsyncGenerator<ChatEvent> {
  const token = await getToken();
  if (!token) throw new ApiError("서버 토큰이 설정되지 않았습니다");

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}/agent/chat`, {
      method: "POST",
      headers: { "X-YTFA-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify({ message, thread_id: threadId }),
    });
  } catch {
    throw new ApiError("로컬 서버에 연결할 수 없습니다");
  }
  if (!res.ok || !res.body) {
    throw new ApiError(`서버 오류 (${res.status})`, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sepIndex: number;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      const event = parseSSEBlock(rawEvent);
      if (event) yield event;
    }
  }
}

// docs/05-구현가이드.md Phase 6 step 26 — 승인 다이얼로그의 응답.
export function resolveApproval(approvalId: string, decision: "allow" | "deny"): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>("/agent/approve", { approval_id: approvalId, decision });
}
