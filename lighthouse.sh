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
#   write <id> on [分钟] | off | status    写开关（默认全只读）
#   elevate <id> [分钟] [--scope "src/**"]  预授权窗口：期间 agent 的范围申请在上限内自动批
#   auto-grant <id> on [--ceiling "src/**,docs/**"] | off | status
#                                           常驻提权策略：开启后上限内的申请立即生效（不用再跑命令）
#   approve <id> [--scope ...] [--minutes N] 批准 agent 的范围申请
#   deny <id>                               收回全部提权（额外范围/待批申请/预授权窗口）
#   scope <id>                              看当前授权状态（含常驻策略）
#   issue "标题" [--area 模块] [--sev 高|中|低] [--detail "现象"]
#                                           记一条问题到 docs/ISSUES.md（不改代码也能攒问题）
#   test [id]            一键验收（起临时实例跑五套测试，不需要公网）
#   doctor               体检：解释器 / mcp 依赖 / cloudflared / 配置
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${LIGHTHOUSE_PY:-python3}"
export LIGHTHOUSE_PY="$PY"
export LIGHTHOUSE_CLI="bash $HERE/lighthouse.sh"
CORE="$HERE/core"
# 注册表路径必须是**服务端读的同一个文件**（config.py 是唯一来源）。
# 这里曾硬编码 windows.json（仓库示例），于是 publish 的可见性检查读一份、
# 真正的发布读另一份 —— 已标 public 的窗口在检查阶段完全看不见。
REGISTRY="$("$PY" -c "import sys; sys.path.insert(0, '$CORE'); import config as C; print(C.REGISTRY_PATH)" 2>/dev/null)"
[ -n "$REGISTRY" ] || REGISTRY="$HERE/windows.json"

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
    # 端口占用告警：只在「该窗口的服务没在跑、端口却被占着」时才是真冲突。
    # （窗口自己在运行时当然占着自己的端口，那不是冲突 —— 以前会对每个已运行窗口都误报。）
    "$PY" - "$REGISTRY" <<'PYEOF'
import json, os, platform, socket, subprocess, sys

def service_running(wid: str) -> bool:
    if platform.system() == "Darwin":
        return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/com.lighthouse.window-{wid}"],
                              capture_output=True).returncode == 0
    return subprocess.run(["systemctl", "--user", "is-active", "--quiet", f"lighthouse-window-{wid}"],
                          capture_output=True).returncode == 0

for wid, w in json.load(open(sys.argv[1]))["windows"].items():
    if not w.get("enabled", True) or service_running(wid):
        continue
    port = w.get("port")
    with socket.socket() as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            print(f"  ⚠️ 端口 {port} 被别的程序占着（窗口 {wid} 尚未运行）——请改 windows.json 里的 port")
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

  restart) "$HERE/lighthouse.sh" stop; sleep 1; "$HERE/lighthouse.sh" start ;;

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

  elevate)
    # 预授权窗口：N 分钟内，agent 的范围申请在「上限」内自动批准
    shift
    id="${1:?用法: lighthouse.sh elevate <窗口id> [分钟数] [--scope \"src/**,*.py\"]}"; shift || true
    mins=30; scope=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --scope) scope="$2"; shift 2 ;;
        ''|*[!0-9]*) shift ;;
        *) mins="$1"; shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$mins" "$scope" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import scope as S
wid, mins, scope = sys.argv[2], sys.argv[3], sys.argv[4]
allowed = [x.strip() for x in scope.split(",") if x.strip()] if scope else []
arm = S.set_arm(wid, int(mins), allowed, note="CLI 预授权窗口")
print(f"✅ 已开预授权窗口: {wid} — {mins} 分钟，范围上限 {allowed or '不限（申请多少批多少，密钥/exclude 仍不可见）'}")
print("   期间 agent 调 request_access 会在上限内自动批准；到期自动失效。")
print(f"   想提前收回：bash lighthouse.sh deny {wid}")
PYEOF
    ;;

  approve)
    # 批准 agent 的申请（不给 --scope 就用它申请的那套范围）
    shift
    id="${1:?用法: lighthouse.sh approve <窗口id> [--scope \"src/**\"] [--minutes N]}"; shift || true
    minutes=""; scope=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --minutes) minutes="$2"; shift 2 ;;
        --scope) scope="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$minutes" "$scope" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import scope as S
wid, minutes, scope = sys.argv[2], sys.argv[3], sys.argv[4]
pend = S.get_pending(wid) or {}
include = [x.strip() for x in scope.split(",") if x.strip()] if scope else (pend.get("include") or [])
if not include:
    print(f"没有待批准的申请，也没给 --scope。用法：bash lighthouse.sh approve {wid} [--scope \"src/**\"] [--minutes 60]")
    raise SystemExit(1)
g = S.set_grant(wid, include, int(minutes) if minutes else None, note=(pend.get("reason") or "CLI 批准")[:200])
print(f"✅ 已批准 {wid}：额外可见 {g['include']}")
print("   有效期：" + ("无期限（用 `bash lighthouse.sh deny " + wid + "` 收回）" if not g["until"] else S._describe_until(g["until"])))
if pend:
    print("   （申请的缘因：" + (pend.get("reason") or "未填写") + "）")
PYEOF
    ;;

  deny)
    # 收回一切：已授予范围 + 待批申请 + 预授权窗口
    id="${2:?用法: lighthouse.sh deny <窗口id>}"
    "$PY" - "$CORE" "$id" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import scope as S
S.clear_grant(sys.argv[2])
print(f"🔒 已收回 {sys.argv[2]} 的全部提权（额外范围 / 待批申请 / 预授权窗口）——回到 windows.json 声明的范围")
PYEOF
    ;;

  scope)
    id="${2:?用法: lighthouse.sh scope <窗口id>}"
    "$PY" - "$CORE" "$id" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import scope as S, config as C
wid = sys.argv[2]
info = S.summary(wid)
auto, ceil = C.auto_grant_policy(C.windows(include_disabled=True).get(wid, {}))
info["auto_grant"] = auto
info["auto_grant_ceiling"] = (ceil or "不限（任何范围申请都会自动生效）") if auto else None
print(json.dumps({wid: info}, ensure_ascii=False, indent=2))

r = info.get("recent_calls") or {}
mins = r.get("window_minutes", 10)
if r.get("total"):
    last = r.get("last") or {}
    path = (last.get("args") or {}).get("path", "")
    verdict = "放行" if last.get("ok") else f"拒绝（{last.get('reason', '')}）"
    print(f"\n最近 {mins} 分钟：收到 {r['total']} 次调用（{r['allowed']} 放行 / {r['denied']} 拒绝）")
    print(f"  最近一次：{last.get('ts', '')[11:19]}  {last.get('tool', '')} {path}  → {verdict}")
else:
    print(f"\n最近 {mins} 分钟：没有收到任何调用")
    print("  （如果你刚让 AI 试过，说明请求根本没到本机 —— 多半是平台/网络拦的，与灯塔无关）")
PYEOF
    ;;

  issue)
    # 记一条问题到 docs/ISSUES.md 的「待修」区 —— 先把问题攒住，回头统一修
    shift
    title="${1:?用法: lighthouse.sh issue \"一句话标题\" [--area 模块] [--sev 高|中|低] [--detail \"现象/证据\"]}"; shift || true
    area=""; sev="中"; detail=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --area) area="$2"; shift 2 ;;
        --sev) sev="$2"; shift 2 ;;
        --detail) detail="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    "$PY" - "$HERE" "$title" "$area" "$sev" "$detail" <<'PYEOF'
import re, sys
from datetime import datetime
from pathlib import Path
root = Path(sys.argv[1])
title, area, sev, detail = sys.argv[2:6]
p = root / "docs" / "ISSUES.md"
if not p.exists():
    print(f"❌ 找不到 {p}")
    raise SystemExit(1)
s = p.read_text(encoding="utf-8")
nums = [int(m) for m in re.findall(r"^### #(\d+)", s, re.M)]
n = (max(nums) + 1) if nums else 1
entry = (f"### #{n} {title}\n\n"
         f"- **发现**：{datetime.now():%Y-%m-%d}（lighthouse.sh issue）\n"
         f"- **现象**：{detail or '（待补）'}\n"
         f"- **期望**：（待补）\n"
         f"- **证据**：（待补）\n"
         f"- **区域**：{area or '（待补）'}\n"
         f"- **严重度**：{sev}\n"
         f"- **状态**：待修\n\n")
mark = "<!-- NEW-ISSUES-HERE -->\n"
if mark not in s:
    print("❌ docs/ISSUES.md 缺少插入标记 <!-- NEW-ISSUES-HERE -->")
    raise SystemExit(1)
p.write_text(s.replace(mark, mark + "\n" + entry, 1), encoding="utf-8")
print(f"✅ 已记入 docs/ISSUES.md：#{n} {title}")
print(f"   严重度 {sev}｜区域 {area or '待补'}｜修完记得移到「已修」并补 commit 号")
PYEOF
    ;;

  auto-grant)
    # 常驻提权策略：on = 上限内的 agent 申请立即生效（不用再跑本地命令）；off = 回到必须用户批准
    shift
    id="${1:?用法: lighthouse.sh auto-grant <窗口id> on|off|status [--ceiling \"src/**,docs/**\"]}"; shift || true
    act="${1:-status}"; shift || true
    ceiling=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --ceiling) ceiling="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$act" "$ceiling" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import config as C
wid, act, ceiling = sys.argv[2], sys.argv[3], sys.argv[4]
path = C.REGISTRY_PATH
reg = json.loads(path.read_text(encoding="utf-8"))
wins = reg.get("windows", {})
if wid not in wins:
    print(f"没有这个窗口: {wid}（现有：{', '.join(wins)}）")
    raise SystemExit(1)
w = wins[wid]
auto, ceil = C.auto_grant_policy(w)

if act == "status":
    print(f"窗口 {wid} 的提权策略：")
    print(f"  申请即授予 : {'开' if auto else '关（申请只记 pending，等用户批准）'}")
    if auto:
        print(f"  常驻上限   : {ceil or '不限（任何范围申请都会自动生效；拉黑/exclude 照旧）'}")
    print(f"  注册表     : {path}")
    raise SystemExit(0)

if act == "off":
    w.pop("auto_grant", None)
    w.pop("elevation_ceiling", None)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"🔒 已关闭 {wid} 的自动授予：之后 agent 的申请只会记成待批，需要你跑 approve（或开预授权窗口）。")
    print(f"   已授予的范围不会自动收回；要一并收回：bash lighthouse.sh deny {wid}")
    raise SystemExit(0)

if act != "on":
    print('用法: lighthouse.sh auto-grant <窗口id> on|off|status [--ceiling "src/**,docs/**"]')
    raise SystemExit(1)

w["auto_grant"] = True
if ceiling:
    pats = [x.strip() for x in ceiling.split(",") if x.strip()]
    bad = [p for p in pats if p.startswith("/") or ".." in p or len(p) > 200]
    if bad or not pats:
        print(f"❌ 上限写法不合法: {bad or '空'}（只接受相对 glob，例如 src/** 、docs/**）")
        raise SystemExit(1)
    w["elevation_ceiling"] = pats
else:
    w.pop("elevation_ceiling", None)
path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"✅ 已为 {wid} 开启「申请即授予」")
print(f"   常驻上限：{ceiling or '不限（任何范围申请都会自动生效）'}")
print("   之后 agent 在对话里申请 → 直接生效，不用再跑本地命令。")
print(f"   ⚠️ 密钥默认拉黑与 exclude 仍然压过一切；想马上收紧：bash lighthouse.sh auto-grant {wid} off")
PYEOF
    ;;

  publish)
    echo "== 对外发布前检查（visibility）=="
    # 只拦「本次没有可发布内容」这一种情况；无关的 local 窗口只是提示「会跳过它」，
    # 不该让整条命令停摆（以前只要注册表里存在任何一个 local 窗口就必须 --force）。
    "$PY" - "$REGISTRY" <<'PYEOF'
import json, sys
wins = json.load(open(sys.argv[1]))["windows"]
enabled = {k: v for k, v in wins.items() if v.get("enabled", True)}
pub = [k for k, v in enabled.items() if v.get("visibility", "local") == "public"]
loc = [k for k, v in enabled.items() if v.get("visibility", "local") != "public"]
for k in loc:
    print(f"  ⏭  {k}: visibility=local —— 本次不发布它（保持私密，这是它该有的样子）")
if not pub:
    print("  ❌ 没有任何标了 public 的窗口 —— 没有可发布的内容")
    raise SystemExit(3)
print(f"  ✅ 将发布 {len(pub)} 个窗口：{', '.join(pub)}")
PYEOF
    rc=$?
    if [ "$rc" = "3" ] && [ "${2:-}" != "--force" ]; then
      echo "已中止：确认要对外，就把 windows.json 里对应窗口的 visibility 改成 public（或加 --force 跳过检查）。"
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
    awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
    ;;
esac
