#!/usr/bin/env bash
# doQment — PostToolUse 훅: git commit 후 개발 히스토리 업데이트 리마인더
#
# Claude Code가 stdin으로 JSON을 전달:
#   { "tool_input": { "command": "..." }, "tool_response": { ... } }

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('command', ''))
except Exception:
    print('')
" 2>/dev/null)

if echo "$COMMAND" | grep -q "git commit"; then
  echo ""
  echo "📝 커밋 완료! docs/dev-history.md 에 이번 작업 내용을 기록해주세요."
  echo "   형식: 날짜 → 문제 인식 → 해결 방법 → 결과"
fi

exit 0
