// 팝업 — 브리핑 + 검색 (docs/05-구현가이드.md Phase 4, step 16/19; docs/01 FR-X3).

import {
  checkHealth,
  getTodayBriefing,
  hasToken,
  searchVideos,
  setToken,
  type BriefingPick,
  type SearchItem,
  type SearchMode,
} from "../api";

const bannerEl = document.getElementById("banner")!;
const tokenSetupEl = document.getElementById("token-setup")!;
const appEl = document.getElementById("app")!;
const resultsSectionEl = document.getElementById("results-section")!;
const costHintEl = document.getElementById("cost-hint")!;
const tokenInput = document.getElementById("token-input") as HTMLInputElement;
const tokenSaveBtn = document.getElementById("token-save")!;
const searchInput = document.getElementById("search-input") as HTMLInputElement;
const searchModeEl = document.getElementById("search-mode") as HTMLSelectElement;
const searchBtn = document.getElementById("search-btn")!;

function showBanner(message: string): void {
  bannerEl.textContent = message;
  bannerEl.classList.add("show");
}

function hideBanner(): void {
  bannerEl.classList.remove("show");
}

function openVideo(url: string): void {
  void chrome.tabs.create({ url });
}

function renderEmpty(message: string): void {
  resultsSectionEl.innerHTML = `<div id="empty-state">${message}</div>`;
}

interface VideoItemLike {
  title: string;
  url: string;
  summary: string | null;
}

interface VideoItemExtras {
  // semantic/hybrid 검색 결과의 근거(Phase 8) — 있으면 요약 대신/추가로 보여준다.
  excerpt?: string | null;
  startSec?: number | null;
  deepLink?: string;
}

function formatTimestamp(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function videoItemHtml(video: VideoItemLike | null, subtitle: string, extras: VideoItemExtras = {}): string {
  if (!video) return "";
  const title = escapeHtml(video.title);
  const linkUrl = extras.deepLink ?? video.url;
  // start_sec이 있으면 자막의 그 구간이 근거라는 뜻(step 36) — 몇 분 몇 초
  // 지점인지 배지로 보여주고, 클릭하면 그 지점부터 재생된다(딥링크 자체가
  // youtu.be/xxx?t=N 형태라 별도 처리 없이 그대로 열면 됨).
  const timestampBadge =
    extras.startSec != null ? `<span class="video-timestamp">▶ ${formatTimestamp(extras.startSec)}</span>` : "";
  const excerptHtml = extras.excerpt ? `<div class="video-excerpt">"${escapeHtml(extras.excerpt)}"</div>` : "";
  return `
    <a class="video-item" data-url="${escapeHtml(linkUrl)}">
      <div class="video-title">${title}</div>
      ${video.summary ? `<div class="video-summary">${escapeHtml(video.summary)}</div>` : ""}
      ${excerptHtml}
      <div class="video-reason">${escapeHtml(subtitle)}</div>
      ${timestampBadge}
    </a>
  `;
}

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function bindVideoItemClicks(container: Element): void {
  for (const el of Array.from(container.querySelectorAll<HTMLElement>(".video-item"))) {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      const url = el.getAttribute("data-url");
      if (url) openVideo(url);
    });
  }
}

async function renderBriefing(): Promise<void> {
  renderEmpty("오늘의 추천을 불러오는 중...");
  try {
    const briefing = await getTodayBriefing();
    costHintEl.textContent = briefing.cached ? "" : `$${briefing.cost_usd.toFixed(4)}`;

    if (briefing.picks.length === 0) {
      renderEmpty("오늘 추천할 새 영상이 없습니다.");
      return;
    }

    const html =
      `<h2>오늘 볼 만한 것 ${briefing.picks.length}개</h2>` +
      briefing.picks.map((pick: BriefingPick) => videoItemHtml(pick.video, pick.reason)).join("");
    resultsSectionEl.innerHTML = html;
    bindVideoItemClicks(resultsSectionEl);
  } catch (err) {
    renderEmpty("브리핑을 불러오지 못했습니다.");
  }
}

async function runSearch(query: string): Promise<void> {
  if (!query.trim()) {
    await renderBriefing();
    return;
  }
  const mode = (searchModeEl.value as SearchMode) || "hybrid";
  renderEmpty("검색 중...");
  try {
    const result = await searchVideos(query, 10, mode);
    if (result.items.length === 0) {
      renderEmpty(result.hint ?? "검색 결과가 없습니다.");
      return;
    }
    const html =
      `<h2>검색 결과 (${escapeHtml(result.mode)})</h2>` +
      result.items
        .map((item: SearchItem) =>
          videoItemHtml(item.video, item.video.channel.title, {
            excerpt: item.excerpt,
            startSec: item.start_sec,
            deepLink: item.deep_link,
          }),
        )
        .join("");
    resultsSectionEl.innerHTML = html;
    bindVideoItemClicks(resultsSectionEl);
  } catch {
    renderEmpty("검색에 실패했습니다.");
  }
}

async function render(): Promise<void> {
  hideBanner();
  tokenSetupEl.classList.remove("show");
  appEl.classList.remove("show");

  if (!(await hasToken())) {
    tokenSetupEl.classList.add("show");
    return;
  }

  try {
    await checkHealth();
  } catch {
    showBanner(
      "로컬 서버에 연결할 수 없습니다. 터미널에서 서버를 실행하세요: " +
        "uv run uvicorn ytfa.main:app --host 127.0.0.1 --port 8787",
    );
    tokenSetupEl.classList.add("show"); // 토큰이 틀렸을 수도 있으니 재입력 가능하게 둔다
    return;
  }

  appEl.classList.add("show");
  await renderBriefing();
}

tokenSaveBtn.addEventListener("click", () => {
  const value = tokenInput.value.trim();
  if (!value) return;
  void setToken(value).then(() => {
    tokenInput.value = "";
    void render();
  });
});

searchBtn.addEventListener("click", () => void runSearch(searchInput.value));
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") void runSearch(searchInput.value);
});
// 검색어가 이미 있는 상태에서 방식만 바꾸면 그 자리에서 다시 검색한다.
searchModeEl.addEventListener("change", () => {
  if (searchInput.value.trim()) void runSearch(searchInput.value);
});

const openSidepanelBtn = document.getElementById("open-sidepanel-btn");
openSidepanelBtn?.addEventListener("click", () => {
  void chrome.windows.getCurrent().then((win) => {
    if (win.id !== undefined) void chrome.sidePanel.open({ windowId: win.id });
  });
});

void render();
