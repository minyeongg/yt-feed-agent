// 백그라운드 서비스 워커 (docs/05-구현가이드.md Phase 4, step 16/19; docs/01 FR-B5).
//
// MV3 서비스 워커는 유휴 상태에서 꺼졌다 이벤트로 다시 깨어난다 —
// setInterval은 못 믿는다. chrome.alarms로 뱃지 갱신 주기를 맞춘다.

import { getFeedCounts, hasToken } from "./api";

const ALARM_NAME = "ytfa-badge-refresh";
const ALARM_PERIOD_MIN = 5;

async function refreshBadge(): Promise<void> {
  if (!(await hasToken())) {
    await chrome.action.setBadgeText({ text: "" });
    return;
  }

  try {
    const counts = await getFeedCounts("new");
    const text = counts.total > 0 ? String(Math.min(counts.total, 99)) : "";
    await chrome.action.setBadgeText({ text });
    await chrome.action.setBadgeBackgroundColor({ color: "#1a73e8" });
  } catch {
    // 서버가 꺼져 있거나 토큰이 틀림 — 뱃지를 조용히 비운다. 유튜브 페이지는
    // 이 파일과 무관하게 계속 동작해야 한다(FR-X7과 같은 원칙).
    await chrome.action.setBadgeText({ text: "" });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: ALARM_PERIOD_MIN });
  void refreshBadge();
});

chrome.runtime.onStartup.addListener(() => {
  void refreshBadge();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) void refreshBadge();
});

// 팝업에서 토큰을 새로 저장했을 때 뱃지를 바로 갱신하기 위한 훅.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && "ytfaToken" in changes) void refreshBadge();
});
