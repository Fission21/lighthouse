#!/usr/bin/env bash
# 灯塔 · 一键验收：起一个临时窗口 → 跑六套测试 → 收拾干净
#
# 用法: bash tests/run_all_tests.sh [窗口id] [端口]
#   默认拿注册表里的第一个窗口（示例仓库里是 demo），在 127.0.0.1 上起一份临时实例，
#   六套测试跑完后自动关掉临时实例、清掉测试件。不需要公网、不需要隧道。
#   第 6 套（受控资料库）自带隔离环境与自己的服务，同样不碰 windows.json 与 ~/.lighthouse。
#   第 4 套（提权）与第 5 套（加固）自带隔离环境，不碰 windows.json 与 ~/.lighthouse。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="${LIGHTHOUSE_PY:-python3}"
WIN="${1:-demo}"
PORT="${2:-8971}"
WORK="${TMPDIR:-/tmp}/lighthouse-tests"
mkdir -p "$WORK"

if ! command -v "$PY" >/dev/null 2>&1; then echo "找不到 python3（可用 LIGHTHOUSE_PY= 指定解释器）"; exit 1; fi
"$PY" -c "import mcp" 2>/dev/null || { echo "缺少 mcp 依赖：pip install mcp"; exit 1; }

# 端口占用就往后找
while lsof -ti :"$PORT" >/dev/null 2>&1 || nc -z 127.0.0.1 "$PORT" 2>/dev/null; do PORT=$((PORT + 1)); done

WROOT="$("$PY" - "$ROOT" "$WIN" <<'PYEOF'
import sys
from pathlib import Path
root, wid = Path(sys.argv[1]), sys.argv[2]
sys.path.insert(0, str(root / "core"))
import config as C
reg = C.windows(include_disabled=True)
if wid not in reg:
    print("__NO_WINDOW__"); raise SystemExit(0)
print(C.window_root(reg[wid]))
PYEOF
)"
if [ "$WROOT" = "__NO_WINDOW__" ]; then echo "❌ 注册表 windows.json 里没有窗口: $WIN"; exit 1; fi

WPATH="$("$PY" -c "import json,sys;print(json.load(open('$ROOT/windows.json'))['windows']['$WIN']['path'] + '-t')")"
URL="http://127.0.0.1:$PORT$WPATH"

echo "════════ 灯塔·一键验收 ════════"
echo "窗口: $WIN   根目录: $WROOT"
echo "临时实例: $URL"
echo

cleanup() {
  [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null
  rm -f "$WROOT/link-passwd.txt" "$WROOT/link-env.txt" 2>/dev/null
}
trap cleanup EXIT

# 造两个符号链接测试件（指向系统敏感文件；测「链接能不能绕过范围闸」）
ln -sf /etc/passwd "$WROOT/link-passwd.txt" 2>/dev/null
ln -sf "$HOME/.lighthouse/state/window-write.json" "$WROOT/link-env.txt" 2>/dev/null

# 起临时实例
WINDOW_ID="$WIN" WINDOW_PORT="$PORT" WINDOW_PATH="$WPATH" WINDOW_REGISTRY="$ROOT/windows.json" \
  "$PY" "$ROOT/core/server.py" >"$WORK/server.log" 2>&1 &
SRV=$!

for i in $(seq 1 30); do
  code=$(curl -s --noproxy '*' --max-time 2 -o /dev/null -w '%{http_code}' -X POST "$URL" \
        -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' 2>/dev/null)
  [ "$code" = "200" ] && break
  sleep 1
done
if [ "${code:-}" != "200" ]; then
  echo "❌ 临时实例没起来，日志：$WORK/server.log"; tail -5 "$WORK/server.log"; exit 1
fi
echo "实例已就绪，开始测试……"; echo

pass=0; fail=0
run() {
  name="$1"; shift
  out="$WORK/$name.log"
  if "$@" >"$out" 2>&1; then
    echo "✅ $name   $("$PY" - "$out" <<'PYEOF'
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"(\d+/\d+ 通过|\d+ 项通过[^\n]*)", t) or re.search(r"(\d+/\d+)", t)
print(m.group(1) if m else "PASS")
PYEOF
)"
    pass=$((pass + 1))
  else
    echo "❌ $name   见 $out"
    tail -3 "$out" | sed 's/^/     /'
    fail=$((fail + 1))
  fi
}

run "通用冒烟(13项)"  "$PY" "$HERE/smoke_window.py"  "$URL"
run "只读审计(45项)"  "$PY" "$HERE/audit_readonly.py" "$URL" "$WROOT"
run "写开关(25项)"    "$PY" "$HERE/test_write.py"    "$URL" "$WIN" "$WROOT"
run "提权(56项)"      "$PY" "$HERE/test_elevate.py"
run "加固(56项)"      "$PY" "$HERE/test_hardening.py"
run "受控资料库(281项)" "$PY" "$HERE/test_kb.py"

echo
echo "════════ 结果：$pass 套通过 / $fail 套失败 ════════"
[ "$fail" -eq 0 ] || exit 1
