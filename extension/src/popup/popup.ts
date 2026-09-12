// 팝업 — 브리핑 + 검색 (docs/05-구현가이드.md Phase 4, step 16/19; docs/01 FR-X3).

import {
  checkHealth,
  getTodayBriefing,
  hasToken,
  searchVideos,
  setToken,
  type BriefingPick,
  type SearchItem,
} from "../api";

const bannerEl = document.getElementById("banner")!;
const tokenSetupEl = document.getElementById("token-setup")!;
const appEl = document.getElementById("app")!;
const resultsSectionEl = document.getElementById("results-section")!;
const costHintEl = document.getElementById("cost-hint")!;
const tokenInput = document.getElementById("token-input") as HTMLInputElement;
const tokenSaveBtn = document.getElementById("token-save")!;
const searchInput = document.getElementById("search-input") as HTMLInputElement;
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

function videoItemHtml(video: VideoItemLike | null, subtitle: string): string {
  if (!video) return "";
  const title = escapeHtml(video.title);
  return `
    <a class="video-item" data-url="${escapeHtml(video.url)}">
      <div class="video-title">${title}</div>
      ${video.summary ? `<div class="video-summary">${escapeHtml(video.summary)}</div>` : ""}
      <div class="video-reason">${escapeHtml(subtitle)}</div>
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
  renderEmpty("검색 중...");
  try {
    const result = await searchVideos(query);
    if (result.items.length === 0) {
      renderEmpty(result.hint ?? "검색 결과가 없습니다.");
      return;
    }
    const html =
      `<h2>검색 결과</h2>` +
      result.items.map((item: SearchItem) => videoItemHtml(item.video, item.video.channel.title)).join("");
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

void render();
