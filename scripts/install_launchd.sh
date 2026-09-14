#!/usr/bin/env bash
# 상시 실행 설정 (docs/05-구현가이드.md Phase 9 step 41).
#
# macOS launchd에 서버를 사용자 LaunchAgent로 등록한다 — 로그인 시
# 자동으로 뜨고(RunAtLoad), 죽으면 다시 살아난다(KeepAlive). 워커
# 스케줄러(RSS 폴링 등)는 main.py의 lifespan에 이미 내장돼 있어서
# (Phase 3 step 13) uvicorn 프로세스 하나만 띄우면 서버+워커가 같이
# 돈다 — 별도 launchd 항목이 두 개 필요 없다.
#
# 확인: 이 스크립트를 실행한 뒤 재부팅해도(또는 로그아웃 후 재로그인)
# 서버가 다시 자동으로 뜨고, 크롬 확장이 별도 조작 없이 바로 붙는다.
#
# 실행: bash scripts/install_launchd.sh
# 제거: bash scripts/uninstall_launchd.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.ytfa.server"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
UV_BIN="$(command -v uv || true)"
UID_NUM="$(id -u)"

if [ -z "$UV_BIN" ]; then
    echo "uv를 찾을 수 없습니다 — https://docs.astral.sh/uv/ 설치 후 다시 실행하세요." >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/data" "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${UV_BIN}</string>
        <string>run</string>
        <string>uvicorn</string>
        <string>ytfa.main:app</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8787</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/data/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/data/launchd.err.log</string>
</dict>
</plist>
EOF

# 이미 등록돼 있으면 내리고 최신 plist로 다시 올린다(재실행해도 안전).
launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "$PLIST_PATH"
launchctl enable "gui/${UID_NUM}/${LABEL}"

echo "설치 완료: ${PLIST_PATH}"
echo "상태 확인: launchctl print gui/${UID_NUM}/${LABEL}"
echo "헬스 체크: curl -H \"X-YTFA-Token: \$(cat ${PROJECT_DIR}/config/token)\" http://127.0.0.1:8787/health"
echo "로그: tail -f ${PROJECT_DIR}/data/launchd.out.log"
echo "제거: bash ${PROJECT_DIR}/scripts/uninstall_launchd.sh"
