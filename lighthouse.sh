#!/usr/bin/env bash
# 灯塔 lighthouse —— 把本地项目开成一扇窗，让网页 AI 看见（且只在你能看见的范围内）。
#
# 用法: bash lighthouse.sh <命令>
#   new <id> <项目路径> [--title 名字] [--include "a/**,b.md"] [--exclude "x/**"] [--public]
#                        登记一扇新窗（自动分配端口 + 随机路径）
#   start                按注册表生成服务定义并拉起（macOS launchd / Linux systemd user）
#   stop | restart       停 / 重启
#   status               总览：服务状态 + 端口 + 窗口范围 + 写开关
#   list                 列出窗口 id
#   url <id> [--public]  打印窗口地址（本机 / 公网）
#   publish              生成隧道配置 + 重启隧道 + 公网健康检查（会拦 visibility=local 的窗口）
#   write <id> on [分钟] | off | status     写开关（默认全只读）
#   test [id]            一键验收（起临时实例跑三套测试，不需要公网）
#   doctor               体检：解释器 / mcp 依赖 / cloudflared / 配置
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${LIGHTHOUSE_PY:-python3}"
export LIGHTHOUSE_PY="$PY"
export LIGHTHOUSE_CLI="bash $HERE/lighthouse.sh"
CORE="$HERE/core"
REGISTRY="$HERE/windows.json"

ids() { "$PY" "$CORE/render_services.py" list 2>/dev/null; }
plat() { uname -s; }

service_name() { [ "$(plat)" = "Darwin" ] && echo "com.lighthouse.window-$1" || echo "lighthouse-window-$1"; }

case "${1:-help}" in
  new)
    shift
    exec "$PY" "$CORE/add_window.py" "$@"
    ;;

  list)
    ids
    ;;

  url)
    id="${2:?用法: lighthouse.sh url <窗口id> [--public]}"
    "$PY" - "$REGISTRY" "$id" "${3:-}" <<'PYEOF'
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(sys.argv[1]).parent / "core"))
import config as C
reg = C.windows(include_disabled=True).get(sys.argv[2])
if not reg:
    print(f"没有这个窗口: {sys.argv[2]}"); raise SystemExit(1)
pub = sys.argv[3] == "--public"
print(f"本机 : http://127.0.0.1:{reg['port']}{reg['path']}")
print(f"公网 : https://{C.load()['hostname']}{reg['path']}" + ("" if pub else "   （记得先 publish，且窗口要标 --public）"))
PYEOF
    ;;

  start)
    "$PY" "$CORE/render_services.py" >/dev/null || exit 1
    # 端口占用告警：撞端口会导致服务一直重启失败
    "$PY" - "$REGISTRY" <<'PYEOF'
import json, socket, sys
for wid, w in json.load(open(sys.argv[1]))["windows"].items():
    if not w.get("enabled", True):
        continue
    port = w.get("port")
    with socket.socket() as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"  ⚠️ 端口 {port} 已被占用（窗口 {wid}）——请改 windows.json 里的 port，或先停掉占用的程序")
PYEOF
    if [ "$(plat)" = "Darwin" ]; then
      for id in $(ids); do
        if launchctl print "gui/$(id -u)/$(service_name "$id")" >/dev/null 2>&1; then
          echo "已在运行: $id"
        else
          launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$(service_name "$id").plist" && echo "已启动: $id"
        fi
      done
    elif command -v systemctl >/dev/null 2>&1; then
      systemctl --user daemon-reload
      for id in $(ids); do systemctl --user enable --now "$(service_name "$id")" && echo "已启动: $id"; done
    else
      echo "本平台没有自动守护，请手动前台运行："
      for id in $(ids); do echo "  WINDOW_ID=$id $PY $CORE/server.py"; done
    fi
    ;;

  stop)
    if [ "$(plat)" = "Darwin" ]; then
      for id in $(ids); do
        launchctl print "gui/$(id -u)/$(service_name "$id")" >/dev/null 2>&1 \
          && launchctl bootout "gui/$(id -u)/$(service_name "$id")" && echo "已停止: $id" || echo "未运行: $id"
      done
    elif command -v systemctl >/dev/null 2>&1; then
      for id in $(ids); do systemctl --user stop "$(service_name "$id")" && echo "已停止: $id"; done
    fi
    ;;

  restart) "$0" stop; sleep 1; "$0" start ;;

  status)
    "$PY" - "$HERE" <<'PYEOF'
import json, socket, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "core"))
import config as C
import platform

wins = C.windows(include_disabled=True)
cfg = C.load()
sw = {}
try:
    sw = json.loads((C.state_dir() / "state" / "window-write.json").read_text(encoding="utf-8"))
except Exception:
    pass

print(f"状态目录: {C.state_dir()}    域名: {cfg['hostname']}    tunnel_id: {cfg['tunnel_id'] or '未配置'}")
print(f"{'窗口':<12}{'服务':<10}{'端口':<12}{'范围':<28}{'写':<10}{'标题'}")
for wid, w in wins.items():
    on = w.get("enabled", True)
    if platform.system() == "Darwin":
        svc = "运行中" if subprocess.run(["launchctl", "print", f"gui/{__import__('os').getuid()}/com.lighthouse.window-{wid}"],
                                          capture_output=True).returncode == 0 else ("未启动" if on else "已禁用")
    else:
        svc = "?" if on else "已禁用"
    port = w.get("port")
    with socket.socket() as s:
        s.settimeout(0.3)
        listening = s.connect_ex(("127.0.0.1", port)) == 0
    state = f"{port}{'✓' if listening else '✗'}"
    inc = ",".join(w.get("include", []))[:26]
    s = sw.get(wid, {})
    wstate = "可写" if s.get("enabled") else "只读"
    print(f"{wid:<12}{svc:<10}{state:<12}{inc:<28}{wstate:<10}{w.get('title', '')}")
print()
print("图例：端口 ✓=在监听 ✗=没在监听（服务没起来或被占用时这样）；写：可写=开关开着，默认全只读")
PYEOF
    ;;

  publish)
    echo "== 对外发布前检查（visibility）=="
    blocked=$("$PY" - "$REGISTRY" <<'PYEOF'
import json, sys
wins = json.load(open(sys.argv[1]))["windows"]
for k, v in wins.items():
    if v.get("enabled", True) and v.get("visibility", "local") != "public":
        print(f"  ⚠️ {k}: visibility=local（未标 public）")
PYEOF
)
    echo "${blocked:-  ✅ 所有启用窗口都标了 public}"
    if [ -n "$blocked" ] && [ "${2:-}" != "--force" ]; then
      echo "已中止：确认要对外，就把 windows.json 里对应窗口的 visibility 改成 public；或加 --force。"
      exit 1
    fi
    echo "== 生成隧道配置 =="
    "$PY" "$CORE/render_ingress.py" --apply || exit 1
    echo "== 公网健康检查 =="
    "$PY" - "$HERE" <<'PYEOF'
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "core"))
import config as C
cfg, wins = C.load(), C.windows()
if cfg.get("tunnel_id"):
    for wid, w in wins.items():
        url = f"https://{cfg['hostname']}{w['path']}"
        r = subprocess.run(["curl", "-s", "--noproxy", "*", "--max-time", "15", "-o", "/dev/null",
                            "-w", "%{http_code}", "-X", "POST", url,
                            "-H", "Content-Type: application/json",
                            "-H", "Accept: application/json, text/event-stream",
                            "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                         "clientInfo": {"name": "probe", "version": "0"}}})],
                           capture_output=True, text=True)
        print(f"  {wid:12s} {url}  http={r.stdout.strip() or r.stderr.strip()[:40]}")
PYEOF
    echo
    echo "接下来：网页 AI（如 ChatGPT）→ 设置 → 连接器 →「创建应用」→ 填上面的 URL。"
    ;;

  write)
    shift
    if [ "${1:-status}" = "status" ]; then exec "$PY" "$CORE/switch.py" status "${@:2}"; fi
    id="${1:?用法: lighthouse.sh write <窗口id> on [分钟数] | off | status}"
    state="${2:-on}"
    mins="${3:-}"
    if [ -n "$mins" ]; then exec "$PY" "$CORE/switch.py" set "$id" "$state" "$mins"; fi
    exec "$PY" "$CORE/switch.py" set "$id" "$state"
    ;;

  test)
    shift
    exec bash "$HERE/tests/run_all_tests.sh" "$@"
    ;;

  doctor)
    echo "== 灯塔体检 =="
    printf "%-22s %s\n" "解释器" "$PY ($($PY -V 2>&1))"
    "$PY" -c "import mcp; print('  ✅ mcp 依赖 已装')" 2>/dev/null || echo "  ❌ mcp 依赖未装 → pip install mcp"
    if command -v cloudflared >/dev/null 2>&1; then
      printf "%-22s %s\n" "cloudflared" "$(cloudflared --version 2>/dev/null | head -1)"
    else
      printf "%-22s %s\n" "cloudflared" "未装（只用本机可不装；出公网需要）"
    fi
    "$PY" -c "
import sys
from pathlib import Path
sys.path.insert(0, str(Path('$HERE') / 'core'))
import config as C
cfg = C.load(); wins = C.windows()
print('状态目录              ', C.state_dir(), '（存在' if C.state_dir().exists() else '（尚未创建')
print('隧道主机名            ', cfg['hostname'], '（tunnel_id ' + (cfg['tunnel_id'] or '未配置 → 还发不了公网') + '）')
print('启用的窗口            ', list(wins) or '（无）')
for wid, w in wins.items():
    root = C.window_root(w)
    print(f\"  · {wid:10s} {w.get('title','')}  {root}  {'✅存在' if root.is_dir() else '❌目录不存在'}\")
"
    ;;

  *)
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
