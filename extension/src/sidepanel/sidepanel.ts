// 사이드패널 — 에이전트 대화 (docs/05-구현가이드.md Phase 6, step 25; docs/01 FR-X4).
//
// SSE 이벤트 5종(run_started/text/tool_call/tool_result/run_end)을
// 받아서 토큰 단위로 화면에 흘려 넣는다. `thread_id`는
// chrome.storage.local에 저장해뒀다가 재사용한다 — 패널을 닫았다 열어도
// 서버(step 24 체크포인터) 쪽 대화가 이어지게 하기 위해서다.

import {
  checkHealth,
  clearThreadId,
  getThreadId,
  hasToken,
  setThreadId,
  setToken,
  streamChat,
  type ChatEvent,
} from "../api";

const bannerEl = document.getElementById("banner")!;
const tokenSetupEl = document.getElementById("token-setup")!;
const messagesEl = document.getElementById("messages")!;
const tokenInput = document.getElementById("token-input") as HTMLInputElement;
const tokenSaveBtn = document.getElementById("token-save")!;
const chatInput = document.getElementById("chat-input") as HTMLTextAreaElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const resetBtn = document.getElementById("reset-btn")!;

let sending = false;

function showBanner(message: string): void {
  bannerEl.textContent = message;
  bannerEl.classList.add("show");
}

function hideBanner(): void {
  bannerEl.classList.remove("show");
}

function appendMessage(role: "user" | "assistant"): HTMLElement {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function appendToolNote(text: string): void {
  const el = document.createElement("div");
  el.className = "tool-note";
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function sendMessage(message: string): Promise<void> {
  if (sending) return;
  sending = true;
  sendBtn.disabled = true;

  appendMessage("user").textContent = message;
  const assistantEl = appendMessage("assistant");
  let assistantText = "";

  try {
    const threadId = await getThreadId();
    for await (const event of streamChat(message, threadId)) {
      handleEvent(event, assistantEl, (t) => {
        assistantText += t;
        assistantEl.textContent = assistantText;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      });
    }
  } catch (err) {
    appendToolNote(`오류: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    sending = false;
    sendBtn.disabled = false;
  }
}

function handleEvent(event: ChatEvent, _assistantEl: HTMLElement, onText: (delta: string) => void): void {
  switch (event.event) {
    case "run_started": {
      const threadId = event.data.thread_id as string | undefined;
      if (threadId) void setThreadId(threadId);
      break;
    }
    case "text": {
      const delta = event.data.delta as string | undefined;
      if (delta) onText(delta);
      break;
    }
    case "tool_call": {
      appendToolNote(`🔧 ${event.data.tool as string} 호출 중...`);
      break;
    }
    case "tool_result": {
      // tool_call 노트로 충분 — 결과는 최종 답변에 반영된다.
      break;
    }
    case "run_end": {
      // 스트림 종료. 특별히 할 일 없음(연결은 이미 끝남).
      break;
    }
  }
}

async function render(): Promise<void> {
  hideBanner();
  tokenSetupEl.classList.remove("show");

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
    tokenSetupEl.classList.add("show");
  }
}

tokenSaveBtn.addEventListener("click", () => {
  const value = tokenInput.value.trim();
  if (!value) return;
  void setToken(value).then(() => {
    tokenInput.value = "";
    void render();
  });
});

sendBtn.addEventListener("click", () => {
  const value = chatInput.value.trim();
  if (!value) return;
  chatInput.value = "";
  void sendMessage(value);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendBtn.click();
  }
});

resetBtn.addEventListener("click", () => {
  void clearThreadId();
  messagesEl.innerHTML = "";
  appendToolNote("새 대화를 시작합니다.");
});

void render();
