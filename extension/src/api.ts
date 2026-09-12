// 로컬 서버 클라이언트 (docs/05-구현가이드.md Phase 4, step 16; docs/03 §2).
//
// 인증 토큰은 chrome.storage.local에 저장한다(서버 실행 시 config/token에
// 생성되는 값을 사용자가 최초 1회 팝업에서 붙여넣는다 — 자동 페어링
// 플로우는 문서에 없어서 만들지 않았다). 서버가 꺼져 있어도 유튜브 페이지
// 자체는 절대 안 깨져야 한다(FR-X7) — 그래서 모든 호출 실패는 예외를
// 던지되, 호출부(content script)가 조용히 무시할 수 있는 형태로 던진다.

const BASE_URL = "http://127.0.0.1:8787";
const TOKEN_KEY = "ytfaToken";

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
