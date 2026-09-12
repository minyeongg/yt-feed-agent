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
  resolveApproval,
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

// docs/05-구현가이드.md Phase 6 step 26 — 승인 다이얼로그.
function appendApprovalDialog(approvalId: string, summary: string): void {
  const el = document.createElement("div");
  el.className = "approval-dialog";

  const summaryEl = document.createElement("div");
  summaryEl.className = "summary";
  summaryEl.textContent = `🛑 승인 필요: ${summary}`;
  el.appendChild(summaryEl);

  const actions = document.createElement("div");
  actions.className = "actions";

  const respond = (decision: "allow" | "deny", label: string) => {
    el.classList.add("resolved");
    summaryEl.textContent = `${decision === "allow" ? "✅" : "🚫"} ${summary} — ${label}`;
    void resolveApproval(approvalId, decision).catch((err) => {
      summaryEl.textContent += ` (전송 실패: ${err instanceof Error ? err.message : String(err)})`;
    });
  };

  const allowBtn = document.createElement("button");
  allowBtn.className = "allow-btn";
  allowBtn.textContent = "허용";
  allowBtn.addEventListener("click", () => respond("allow", "허용됨"));

  const denyBtn = document.createElement("button");
  denyBtn.className = "deny-btn";
  denyBtn.textContent = "거부";
  denyBtn.addEventListener("click", () => respond("deny", "거부됨"));

  actions.append(allowBtn, denyBtn);
  el.appendChild(actions);
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function sendMessage(message: string): Promise<void> {
  if (sending) return;
  sending = true;
  sendBtn.disabled = true;

  appendMessage("user").textContent = message;

  // 답변 말풍선을 미리 하나 만들어두고 계속 그 안에 이어붙이면, 중간에 낀
  // 툴 호출 노트가 항상 화면 맨 뒤로 밀린다(실제로 겪은 버그 — 텍스트가
  // 먼저 다 뜨고 🔧 노트가 끝에 몰려 나왔다). 그래서 말풍선을 그때그때
  // 새로 만든다 — tool_call이 오면 현재 말풍선을 "닫고", 다음 text부터는
  // 새 말풍선에 쓴다. DOM 삽입 순서가 곧 이벤트 순서가 되게 하는 게 핵심.
  let currentAssistantEl: HTMLElement | null = null;
  let currentText = "";

  function onText(delta: string): void {
    if (currentAssistantEl === null) {
      currentAssistantEl = appendMessage("assistant");
      currentText = "";
    }
    currentText += delta;
    currentAssistantEl.textContent = currentText;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function onToolCall(tool: string): void {
    appendToolNote(`🔧 ${tool} 호출 중...`);
    currentAssistantEl = null; // 다음 text는 새 말풍선에 — 시간 순서를 DOM 순서로 유지
  }

  function onApprovalRequired(approvalId: string, summary: string): void {
    appendApprovalDialog(approvalId, summary);
    currentAssistantEl = null; // 같은 이유로 다음 text는 새 말풍선에
  }

  try {
    const threadId = await getThreadId();
    for await (const event of streamChat(message, threadId)) {
      handleEvent(event, onText, onToolCall, onApprovalRequired);
    }
  } catch (err) {
    appendToolNote(`오류: ${err instanceof Error ? err.message : String(err)}`);
  } finally {
    sending = false;
    sendBtn.disabled = false;
  }
}

function handleEvent(
  event: ChatEvent,
  onText: (delta: string) => void,
  onToolCall: (tool: string) => void,
  onApprovalRequired: (approvalId: string, summary: string) => void,
): void {
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
      onToolCall(event.data.tool as string);
      break;
    }
    case "tool_result": {
      // tool_call 노트로 충분 — 결과는 최종 답변에 반영된다.
      break;
    }
    case "approval_required": {
      onApprovalRequired(event.data.approval_id as string, event.data.summary as string);
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
