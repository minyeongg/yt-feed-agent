// 카테고리 탭 바 + 영상 카드 요약 배지 (docs/05-구현가이드.md Phase 4, step 17-18).
//
// 유튜브는 SPA라 페이지 전환이 풀 리로드가 아니다 — `yt-navigate-finish`
// 이벤트(유튜브 자체 라우터가 쏜다)로 감지하고, 그걸로도 못 잡는 DOM
// 재작성(무한 스크롤, 폴리머 재렌더링)은 MutationObserver로 보완한다.
//
// **핵심 원칙(FR-X8): 주입 지점을 못 찾으면 조용히 포기한다.** 이 파일의
// 모든 진입점은 try/catch로 감싼다 — 유튜브 페이지 자체를 깨뜨리면 안
// 된다. 서버가 꺼져 있어도 마찬가지로 조용히 아무것도 안 한다(FR-X7의
// "페이지를 안 건드린다" 쪽 절반 — 배너는 팝업의 몫이다).

import { getCategories, getFeed, hasToken, type Category, type VideoCard } from "../api";
import {
  extractVideoId,
  findCardAppendTarget,
  findFeedHeaderAnchor,
  findGridContainer,
  findVideoCards,
  INJECTED_MARK,
  SUBSCRIPTIONS_PATH,
} from "./selectors";

const TAB_BAR_ID = "ytfa-category-tabs";
const ALL_STATES = "new,seen,watched,skipped,not_interested";
const FEED_FETCH_LIMIT = 1000; // 무한스크롤로 카드가 수백 개까지 늘어나는 걸 실측 확인 — 300은 너무 작았다
const LOG = "[yt-feed-agent]";

let videoIndex = new Map<string, VideoCard>();
let categories: Category[] = [];
let activeCategoryId: string | null = null; // null = 전체
let allowedVideoIds: Set<string> | null = null; // null = 필터 없음(전체 허용)
let gridObserver: MutationObserver | null = null;

function isSubscriptionsPage(): boolean {
  return location.pathname === SUBSCRIPTIONS_PATH;
}

/** `fn`이 non-null을 반환할 때까지 폴링한다. 유튜브는 SPA라 페이지 전환
 * 직후에도 폴리머 컴포넌트가 비동기로 렌더링되는 중일 수 있다 —
 * `yt-navigate-finish`가 왔어도 그리드가 아직 없을 수 있다는 뜻이다.
 * 한 번 확인하고 바로 포기하면 이 타이밍 경쟁에서 자주 진다. */
function waitFor<T>(fn: () => T | null, timeoutMs = 8000, intervalMs = 250): Promise<T | null> {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    const tick = () => {
      const result = fn();
      if (result) {
        resolve(result);
        return;
      }
      if (Date.now() >= deadline) {
        resolve(null);
        return;
      }
      setTimeout(tick, intervalMs);
    };
    tick();
  });
}

function injectStyles(): void {
  if (document.getElementById("ytfa-styles")) return;
  const style = document.createElement("style");
  style.id = "ytfa-styles";
  style.textContent = `
    #${TAB_BAR_ID} { display: flex; gap: 8px; flex-wrap: wrap; padding: 12px 24px; }
    #${TAB_BAR_ID} button {
      border: 1px solid var(--yt-spec-10-percent-layer, #ccc); background: transparent;
      border-radius: 16px; padding: 6px 14px; font-size: 13px; cursor: pointer;
      color: inherit; font-family: inherit;
    }
    #${TAB_BAR_ID} button.ytfa-active { background: #1a73e8; border-color: #1a73e8; color: #fff; }
    .ytfa-badge {
      margin-top: 4px; font-size: 12px; line-height: 1.3; color: var(--yt-spec-text-secondary, #606060);
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .ytfa-badge-tag {
      display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 8px;
      background: #e8f0fe; color: #1a73e8; margin-right: 4px;
    }
  `;
  document.head.appendChild(style);
}

async function buildVideoIndex(): Promise<void> {
  const feed = await getFeed({ state: ALL_STATES, includeShorts: true, limit: FEED_FETCH_LIMIT });
  videoIndex = new Map(feed.items.map((item) => [item.id, item]));
}

/** 배지를 붙인다. 왜 못 붙였는지 한 단어로 반환한다(로깅용) — 성공하면 null. */
function applyBadgeToCard(card: Element): "already" | "no-id" | "not-in-index" | null {
  if (card.hasAttribute(INJECTED_MARK)) return "already";
  const videoId = extractVideoId(card);
  if (!videoId) return "no-id";
  const video = videoIndex.get(videoId);
  if (!video) return "not-in-index";

  const badge = document.createElement("div");
  badge.className = "ytfa-badge";

  const tags: string[] = [];
  if (video.kind === "short") tags.push("Shorts");
  if (video.state === "watched") tags.push("시청완료");
  else if (video.summary) tags.push("요약됨");

  badge.innerHTML =
    tags.map((t) => `<span class="ytfa-badge-tag">${t}</span>`).join("") + (video.summary ?? "");

  // 메타데이터 줄 뒤에 형제로 끼워넣지 않는다 — 그 부모가 높이 고정이라
  // 잘려서 안 보이는 걸 실측으로 확인했다. 카드 최상위의 자식으로 붙여서
  // 카드 전체 높이가 배지만큼 자연스럽게 늘어나게 한다.
  findCardAppendTarget(card).appendChild(badge);
  card.setAttribute(INJECTED_MARK, "1");
  return null;
}

function applyBadgesToAllCards(): void {
  const cards = findVideoCards();
  const skipReasons: Record<string, number> = {};
  let applied = 0;

  for (const card of cards) {
    const reason = applyBadgeToCard(card);
    if (reason === null) applied++;
    else skipReasons[reason] = (skipReasons[reason] ?? 0) + 1;
  }

  console.info(`${LOG} 배지 적용: 카드 ${cards.length}개 중 ${applied}개 성공, 실패 사유=${JSON.stringify(skipReasons)}`);
}

function applyCategoryFilter(): void {
  const cards = findVideoCards();
  let hidden = 0;
  let unresolved = 0;

  for (const card of cards) {
    const el = card as HTMLElement;
    if (allowedVideoIds === null) {
      el.style.removeProperty("display");
      continue;
    }
    const videoId = extractVideoId(card);
    if (videoId === null) unresolved++;
    // ID를 못 뽑으면(구조 변경 등) 숨기지 않는다 — 잘못 판단해서 숨기는
    // 것보다 안 걸러지는 게 안전하다(FR-X8 정신).
    const shouldShow = videoId === null || allowedVideoIds.has(videoId);
    if (!shouldShow) hidden++;
    if (shouldShow) el.style.removeProperty("display");
    // !important로 건다 — 유튜브 자체 스타일/재바인딩이 인라인 display를
    // 다시 덮어쓸 가능성을 최대한 눌러둔다.
    else el.style.setProperty("display", "none", "important");
  }

  // 실제로 브라우저가 인라인 스타일을 유지하고 있는지 직접 재조회해서
  // 확인한다 — 유튜브가 우리 style을 지워버리는 경우를 잡아내기 위해.
  const actuallyHidden = document.querySelectorAll("ytd-rich-item-renderer[style*='display: none']").length;

  console.info(
    `${LOG} 필터 적용: 카드 ${cards.length}개 중 ${hidden}개 숨김 시도, 실제 hidden=${actuallyHidden}, ID 추출 실패 ${unresolved}개` +
      (allowedVideoIds ? ` (허용 목록 ${allowedVideoIds.size}건)` : " (필터 없음=전체)"),
  );
}

async function selectCategory(categoryId: string | null): Promise<void> {
  activeCategoryId = categoryId;
  updateTabActiveState();

  if (categoryId === null) {
    allowedVideoIds = null;
  } else {
    try {
      const feed = await getFeed({ category: categoryId, state: ALL_STATES, includeShorts: true, limit: FEED_FETCH_LIMIT });
      allowedVideoIds = new Set(feed.items.map((item) => item.id));
      console.info(`${LOG} 카테고리 '${categoryId}' 서버 조회 결과 ${allowedVideoIds.size}건`);
    } catch (err) {
      console.warn(`${LOG} 카테고리 필터용 /feed 호출 실패 — 필터 없이 진행:`, err);
      allowedVideoIds = null; // 실패하면 필터를 걸지 않는다 — 카드가 갑자기 다 사라지는 것보다 낫다
    }
  }
  applyCategoryFilter();
}

function updateTabActiveState(): void {
  const bar = document.getElementById(TAB_BAR_ID);
  if (!bar) return;
  for (const btn of Array.from(bar.querySelectorAll("button"))) {
    const id = btn.getAttribute("data-category-id");
    btn.classList.toggle("ytfa-active", (id || null) === activeCategoryId);
  }
}

async function buildTabBar(): Promise<void> {
  if (document.getElementById(TAB_BAR_ID)) return;

  const anchor = await waitFor(findFeedHeaderAnchor);
  if (!anchor) {
    // FR-X8: 못 찾으면 조용히 포기 — 페이지는 안 건드린다.
    console.info(`${LOG} 탭 바 주입 지점을 못 찾음 — 유튜브 DOM이 바뀌었을 수 있음. selectors.ts 확인 필요.`);
    return;
  }

  const bar = document.createElement("div");
  bar.id = TAB_BAR_ID;

  const allBtn = document.createElement("button");
  allBtn.textContent = "전체";
  allBtn.addEventListener("click", () => void selectCategory(null));
  bar.appendChild(allBtn);

  for (const cat of categories) {
    const btn = document.createElement("button");
    btn.setAttribute("data-category-id", cat.id);
    btn.textContent = cat.new_video_count > 0 ? `${cat.name} (${cat.new_video_count})` : cat.name;
    btn.addEventListener("click", () => void selectCategory(cat.id));
    bar.appendChild(btn);
  }

  anchor.insertAdjacentElement("afterend", bar);
  updateTabActiveState();
  console.info(`${LOG} 탭 바 주입 완료 (카테고리 ${categories.length}개)`);
}

async function observeGridForNewCards(): Promise<void> {
  const grid = await waitFor(findGridContainer);
  if (!grid) {
    console.info(`${LOG} 카드 그리드를 못 찾아 재주입 관찰을 못 켬`);
    return;
  }
  gridObserver?.disconnect();
  gridObserver = new MutationObserver(() => {
    applyBadgesToAllCards();
    applyCategoryFilter();
  });
  gridObserver.observe(grid, { childList: true });
}

async function runOnSubscriptionsPage(): Promise<void> {
  if (!(await hasToken())) {
    console.info(`${LOG} 토큰 미설정 — 확장 팝업에서 서버 토큰을 먼저 저장하세요`);
    return;
  }
  console.info(`${LOG} 구독 피드 감지, 주입 시작`);

  injectStyles();
  categories = await getCategories();
  await buildVideoIndex();
  console.info(`${LOG} 카테고리 ${categories.length}개 / 영상 ${videoIndex.size}건 로드`);

  await buildTabBar();

  const cards = findVideoCards();
  console.info(`${LOG} 카드 ${cards.length}개 발견, 배지 적용 시도`);
  applyBadgesToAllCards();

  await observeGridForNewCards();
}

async function handleNavigation(): Promise<void> {
  try {
    if (!isSubscriptionsPage()) {
      gridObserver?.disconnect();
      gridObserver = null;
      return;
    }
    await runOnSubscriptionsPage();
  } catch (err) {
    // FR-X8: 못 찾으면 조용히 포기 — 콘솔에만 남기고 페이지는 그대로 둔다.
    console.warn(`${LOG} 주입 중 예외 (페이지엔 영향 없음):`, err);
  }
}

console.info(`${LOG} content script 로드됨 (path=${location.pathname})`);
document.addEventListener("yt-navigate-finish", () => void handleNavigation());
void handleNavigation(); // 스크립트가 처음 로드될 때(새로고침 등)도 1회 실행 — yt-navigate-finish는 SPA 내부 전환에만 온다
