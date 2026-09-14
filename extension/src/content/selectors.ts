// 유튜브 DOM 셀렉터 모음 (docs/05-구현가이드.md Phase 4, step 17; docs/02 ADR-13).
//
// 유튜브 DOM이 깨지면 고칠 곳을 여기 한 군데로 모은다. 구독 피드는
// `ytd-rich-grid-renderer` 안에 `ytd-rich-item-renderer` 카드들이 있는
// 구조를 오래 유지해왔지만, 확실하다고 보장할 수 없다 — 그래서 모든
// 셀렉터 사용처는 null을 정상 케이스로 다루고(inject.ts), 못 찾으면
// 조용히 포기한다(FR-X8).

export const SUBSCRIPTIONS_PATH = "/feed/subscriptions";

/** 탭 바를 꽂을 위치 — 구독 피드 상단, 그리드보다 먼저 나오는 컨테이너. */
export function findFeedHeaderAnchor(): Element | null {
  return document.querySelector("ytd-rich-grid-renderer #header") ?? document.querySelector("ytd-rich-grid-renderer");
}

/** 영상 카드들이 들어있는 그리드 컨테이너. 탭 필터링·재주입 관찰 대상. */
export function findGridContainer(): Element | null {
  return document.querySelector("ytd-rich-grid-renderer #contents");
}

/** 그리드 안의 개별 영상 카드. */
export function findVideoCards(): Element[] {
  return Array.from(document.querySelectorAll("ytd-rich-item-renderer"));
}

/** 카드 안에서 영상 ID를 뽑아낸다 — 썸네일/제목 링크의 href에서 파싱.
 *
 * `a#thumbnail`이 제일 흔하지만 id가 없거나 구조가 살짝 다른 카드도
 * 있어서(레이아웃 실험군 등), `/watch`가 들어간 링크 아무거나로
 * 한 단계 완화된 폴백을 둔다 — 여기서 실패하면 필터링 전체가 무력화되는
 * 만큼(모든 카드가 "ID 불명 → 표시"로 빠짐) 제일 관대하게 잡는다. */
export function extractVideoId(card: Element): string | null {
  const link =
    card.querySelector<HTMLAnchorElement>("a#thumbnail[href*='/watch']") ??
    card.querySelector<HTMLAnchorElement>("a#video-title-link[href*='/watch']") ??
    card.querySelector<HTMLAnchorElement>("a[href*='/watch?v=']");
  if (!link) return null;
  const url = new URL(link.getAttribute("href") ?? link.href, location.origin);
  return url.searchParams.get("v");
}

/** 배지를 자식으로 붙일 위치 — 카드 최상위 컨테이너.
 *
 * 처음엔 메타데이터 줄(`yt-content-metadata-view-model`) 바로 뒤에
 * 형제로 끼워넣었는데, 그 부모(`yt-lockup-metadata-view-model`)가 높이
 * 고정 + `overflow:hidden`이라 대부분 카드에서 배지가 잘려서 안
 * 보였다(실측 — 제목이 짧아 여유 있던 카드 하나만 보임). 그래서 높이가
 * 콘텐츠에 맞춰 늘어나는 카드 최상위(`yt-lockup-view-model`)의 자식으로
 * 붙이도록 바꿨는데, 그 수정 이후에도 실측에서 배지가 안 보였다.
 *
 * 의심되는 원인: `yt-lockup-view-model`이 `display:contents`면 그
 * 자체가 박스를 안 만들어서, 거기 붙인 자식(배지)이 실제로는
 * "조부모의 자식"인 것처럼 레이아웃된다 — 우리가 기대한 위치가 아니라
 * 조부모의 flex/grid 규칙에 휘둘리는 엉뚱한 자리에 놓이거나 가려진다.
 * 라이브 DOM을 직접 못 띄우는 환경이라 이 셀렉터 하나를 확정하는 대신,
 * `display:contents`인 조상을 만나면 실제 박스를 가진 조상이 나올 때까지
 * 거슬러 올라가는 런타임 방어로 바꿨다 — 문제의 정확한 계층을 몰라도
 * 되고, 유튜브가 마크업을 또 바꿔도 같은 종류의 문제엔 자동으로
 * 버틴다. 그래도 안 보이면 `getComputedStyle(document.querySelector('.ytfa-badge'))`로
 * 실제 display/height부터 확인할 것 — 이번에도 추측성 수정이라 라이브
 * 확인 전까진 "고쳤다"고 단정하지 않는다. */
export function findCardAppendTarget(card: Element): Element {
  let target: Element = card.querySelector("yt-lockup-view-model") ?? card;
  while (target !== card && getComputedStyle(target).display === "contents") {
    target = target.parentElement ?? card;
  }
  return target;
}

/** 이미 주입된 요소인지 표시하는 데이터 속성 — 재주입 시 중복 방지. */
export const INJECTED_MARK = "data-ytfa-injected";
