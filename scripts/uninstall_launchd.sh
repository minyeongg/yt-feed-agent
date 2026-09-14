#!/usr/bin/env bash
# install_launchd.sh로 등록한 상시 실행을 되돌린다 (Phase 9 step 41).

set -euo pipefail

LABEL="com.ytfa.server"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"

launchctl bootout "gui/${UID_NUM}/${LABEL}" 2>/dev/null || true
rm -f "$PLIST_PATH"

echo "제거 완료: ${LABEL} (더 이상 로그인 시 자동 실행되지 않습니다)"
