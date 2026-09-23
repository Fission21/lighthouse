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
#   lan <id> on|off|status            局域网直连：同网段设备用「本机 IP:端口」访问（不用域名/隧道）
#   json-response <id> on|off|status  POST 回应改纯 JSON（给不吃 SSE 的隧道/客户端；默认 SSE 帧）
#   relay <id> on [--host user@ip] [--port N] [--key ~/.ssh/x] | off | status
#                                           公网 IP 直连：ssh -R 反向隧道挂到你的服务器 IP:端口（不用域名）
#   elevate <id> [分钟|--for 2h] [--scope "src/**"]
#                                           预授权窗口：期间 agent 的范围申请在上限内自动批
#   auto-grant <id> on [--ceiling "src/**,docs/**"] [--ttl 2h|forever] | off | status
#                                           常驻提权策略：开启后上限内的申请立即生效（不用再跑命令）
#   approve <id> [--scope ...] [--for 2h|forever] [--minutes N]
#                                           批准 agent 的范围申请；--for 选授权时长
#   时长写法（approve / elevate / auto-grant --ttl 通用）：30m / 2h / 1d / 7d / 1w / forever
#                                           （纯数字 = 分钟；不写 = 无期限，用 deny 收回）
#   chat-approval <id> on [--ceiling "src/**,docs/**"] | off | status
#                                           对话内授权：用户在对话里说「授权你」即生效，不用在部署机跑命令
#                                           （信任式通道：只给本机/可信 agent 开，网页 AI 窗口不要开）
#   deny <id>                               收回全部提权（额外范围/待批申请/预授权窗口）
#   scope <id>                              看当前授权状态（含常驻策略）
#   issue "标题" [--area 模块] [--sev 高|中|低] [--detail "现象"]
#                                           记一条问题到 docs/ISSUES.md（不改代码也能攒问题）
#   kb <子命令> [参数]        受控资料库（台账 / 同事地址 / 申请 / 用量）：
#     scan <窗> [--extract auto|none|mineru] [--yes]     扫目录、抽文本、登记待批
#     pending|list|show <窗>                              看台账
#     approve <窗> <doc_id|--all-pending|--category X> [--level L] [--yes]
#     reject <窗> <doc_id> [--reason "…"] | reindex <窗>   审批 / 内容变更后重抽
#     requests <窗> [--pending]                           看同事的申请
#     decide <窗> <申请号> --approve --level L [--for 30d] | --deny [--reason "…"]
#     users <窗> | grant <窗> --name 张三 --level "L2-技术" [--for 30d]
#     set-level <窗> --name 张三 --level "L2-技术"         改等级（地址不变）
#     rotate <窗> --name 张三 | revoke <窗> --name 张三 [--enable]
#     invite <窗> --name 张三 --out 文件.md                发放 + 生成一页使用说明
#     notify <窗> [--ack] [--json]                         新申请（给 agent 汇报用）
#     usage <窗> [--days 7] [--by person|day|doc|tool] [--csv]
#     admin-url <窗>                                      管理页地址（本机打开）
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
if C.window_bind(reg) == "0.0.0.0":
    print(f"局域网: http://{C.lan_ip() or '<本机局域网IP>'}:{reg['port']}{reg['path']}   （同网段设备可直连）")
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
    id="${1:?用法: lighthouse.sh elevate <窗口id> [分钟数|--for 2h] [--scope \"src/**,*.py\"]}"; shift || true
    mins=30; scope=""; dur=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --scope) scope="$2"; shift 2 ;;
        --for|--duration) dur="$2"; shift 2 ;;
        ''|*[!0-9]*) shift ;;
        *) mins="$1"; shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$mins" "$scope" "$dur" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import scope as S
wid, mins, scope, dur = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
allowed = [x.strip() for x in scope.split(",") if x.strip()] if scope else []
try:
    m = S.parse_duration(dur) if dur else int(mins)
except ValueError as e:
    print(f"❌ {e}")
    raise SystemExit(1)
if not m:
    print("❌ 预授权窗口需要一个时长（例如 --for 30m）；无期限的批量放行请用 auto-grant")
    raise SystemExit(1)
arm = S.set_arm(wid, int(m), allowed, note="CLI 预授权窗口")
print(f"✅ 已开预授权窗口: {wid} — {S.describe_duration(int(m))}，范围上限 {allowed or '不限（申请多少批多少，密钥/exclude 仍不可见）'}")
try:
    import config as C
    _hint = S.dir_only_hint(C.window_root(C.windows()[wid]), allowed)
    if _hint:
        print("⚠️  " + _hint)
except Exception:
    pass

print("   期间 agent 调 request_access 会在上限内自动批准；到期自动失效。")
print(f"   想提前收回：bash lighthouse.sh deny {wid}")
PYEOF
    ;;

  approve)
    # 批准 agent 的申请（不给 --scope 就用它申请的那套范围）
    shift
    id="${1:?用法: lighthouse.sh approve <窗口id> [--scope \"src/**\"] [--for 2h|forever] [--minutes N]}"; shift || true
    minutes=""; scope=""; dur=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --minutes) minutes="$2"; shift 2 ;;
        --for|--duration) dur="$2"; shift 2 ;;
        --scope) scope="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$minutes" "$scope" "$dur" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import scope as S
wid, minutes, scope, dur = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
pend = S.get_pending(wid) or {}
include = [x.strip() for x in scope.split(",") if x.strip()] if scope else (pend.get("include") or [])
if not include:
    print(f"没有待批准的申请，也没给 --scope。用法：bash lighthouse.sh approve {wid} [--scope \"src/**\"] [--for 2h]")
    raise SystemExit(1)
try:
    mins = S.parse_duration(dur) if dur else (int(minutes) if minutes else None)
except ValueError as e:
    print(f"❌ {e}")
    raise SystemExit(1)
g = S.set_grant(wid, include, mins, note=(pend.get("reason") or "CLI 批准")[:200])
print(f"✅ 已批准 {wid}：额外可见 {g['include']}")
try:
    import config as C
    _hint = S.dir_only_hint(C.window_root(C.windows()[wid]), include)
    if _hint:
        print("⚠️  " + _hint)
except Exception:
    pass

print("   授权时长：" + (f"{S.describe_duration(mins)}（{S._describe_until(g['until'])}）" if g["until"] else "无期限（用 `bash lighthouse.sh deny " + wid + "` 收回）"))
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
w = C.windows(include_disabled=True).get(wid, {})
auto, ceil = C.auto_grant_policy(w)
info["auto_grant"] = auto
info["auto_grant_ceiling"] = (ceil or "不限（任何范围申请都会自动生效）") if auto else None
info["auto_grant_ttl"] = S.describe_duration(C.window_auto_grant_ttl(w)) if auto else None
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
    id="${1:?用法: lighthouse.sh auto-grant <窗口id> on|off|status [--ceiling \"src/**,docs/**\"] [--ttl 2h|forever]}"; shift || true
    act="${1:-status}"; shift || true
    ceiling=""; ttl=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --ceiling) ceiling="$2"; shift 2 ;;
        --ttl|--for) ttl="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$act" "$ceiling" "$ttl" <<'PYEOF'
import json, sys
sys.path.insert(0, sys.argv[1])
import config as C
import scope as S
wid, act, ceiling, ttl = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
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
        print(f"  授权时长   : {S.describe_duration(C.window_auto_grant_ttl(w))}（每次自动授予保持这么久）")
    print(f"  注册表     : {path}")
    raise SystemExit(0)

if act == "off":
    w.pop("auto_grant", None)
    w.pop("elevation_ceiling", None)
    w.pop("auto_grant_ttl_minutes", None)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"🔒 已关闭 {wid} 的自动授予：之后 agent 的申请只会记成待批，需要你跑 approve（或开预授权窗口）。")
    print(f"   已授予的范围不会自动收回；要一并收回：bash lighthouse.sh deny {wid}")
    raise SystemExit(0)

if act != "on":
    print('用法: lighthouse.sh auto-grant <窗口id> on|off|status [--ceiling "src/**,docs/**"] [--ttl 2h|forever]')
    raise SystemExit(1)

try:
    ttl_min = S.parse_duration(ttl) if ttl else None
except ValueError as e:
    print(f"❌ {e}")
    raise SystemExit(1)

w["auto_grant"] = True
if ttl_min:
    w["auto_grant_ttl_minutes"] = ttl_min
else:
    w.pop("auto_grant_ttl_minutes", None)
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
print(f"   授权时长：{S.describe_duration(ttl_min)}（每次自动授予保持这么久，到期自动收回；可换 --ttl 30m|2h|1d|7d|forever）")
print("   之后 agent 在对话里申请 → 直接生效，不用再跑本地命令。")
print(f"   ⚠️ 密钥默认拉黑与 exclude 仍然压过一切；想马上收紧：bash lighthouse.sh auto-grant {wid} off")
PYEOF
    ;;

  chat-approval)
    # 对话内授权（默认关）：开了之后，用户在对话里明确同意 → agent 带 user_confirmed=true 再次申请即生效
    shift
    id="${1:?用法: lighthouse.sh chat-approval <窗口id> on|off|status [--ceiling \"src/**,docs/**\"]}"; shift || true
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

if act == "status":
    on = C.window_chat_approval(w)
    print(f"窗口 {wid} 的「对话内授权」：{'开' if on else '关（默认）'}")
    if on:
        print(f"  上限       : {', '.join(C.window_ceiling(w)) or '不限'}")
        print("  ⚠️ 信任式通道：服务端验证不了用户是否真说了同意 —— 只对本机/可信 agent 开。")
    raise SystemExit(0)

if act == "off":
    w.pop("chat_approval", None)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"🔒 已关闭 {wid} 的「对话内授权」：带 user_confirmed 的申请也会落回待批。")
    print(f"   （已授予的范围不会自动收回；要一并收回：bash lighthouse.sh deny {wid}）")
    raise SystemExit(0)

if act != "on":
    print('用法: lighthouse.sh chat-approval <窗口id> on|off|status [--ceiling "src/**,docs/**"]')
    raise SystemExit(1)

w["chat_approval"] = True
if ceiling:
    pats = [x.strip() for x in ceiling.split(",") if x.strip()]
    bad = [p for p in pats if p.startswith("/") or ".." in p or len(p) > 200]
    if bad or not pats:
        print(f"❌ 上限写法不合法: {bad or '空'}（只接受相对 glob，例如 src/** 、docs/**）")
        raise SystemExit(1)
    w["elevation_ceiling"] = pats
path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"✅ 已为 {wid} 开启「对话内授权」")
print(f"   上限：{ceiling or '不限（拉黑/exclude 照旧压过一切）'}")
print("   流程：agent 申请 → 你回一句「授权你」→ 它带 user_confirmed=true 再申请 → 即时生效（不用跑命令）")
print(f"   想收紧：bash lighthouse.sh chat-approval {wid} off（或 deny 立即收回）")
PYEOF
    ;;

  lan)
    # 局域网直连（不用域名、不用隧道）：on 后同网段设备可用「本机局域网 IP:端口」访问
    id="${2:?用法: lighthouse.sh lan <窗口id> on|off|status}"; act="${3:-status}"
    "$PY" - "$REGISTRY" "$id" "$act" <<'PYEOF'
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(sys.argv[1]).parent / "core"))
import config as C
wid, act = sys.argv[2], sys.argv[3]
path = C.REGISTRY_PATH
reg = json.loads(path.read_text(encoding="utf-8"))
wins = reg.get("windows", {})
if wid not in wins:
    print(f"没有这个窗口: {wid}（现有：{', '.join(wins)}）"); raise SystemExit(1)
w = wins[wid]
if act == "status":
    b = C.window_bind(w)
    print(f"窗口 {wid} 的监听：{b}" + ("（局域网直连：开）" if b == "0.0.0.0" else "（仅本机）"))
    if b == "0.0.0.0":
        print(f"  同网段访问：http://{C.lan_ip() or '<本机局域网IP>'}:{w['port']}{w['path']}")
        print("  ⚠️ 局域网内可见（路径随机段=弱口令），且无 TLS；别在不可信网络开。")
    raise SystemExit(0)
if act == "on":
    w["bind"] = "0.0.0.0"
elif act == "off":
    w.pop("bind", None)
else:
    print('用法: lighthouse.sh lan <窗口id> on|off|status'); raise SystemExit(1)
path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if act == "on":
    print(f"✅ {wid} 已开「局域网直连」：同网段设备用 http://{C.lan_ip() or '<本机局域网IP>'}:{w['port']}{w['path']} 直连。")
    print("   ⚠️ 局域网内可见、无 TLS：只在你信得过的网络开；用完 `lan " + wid + " off` 收回。")
else:
    print(f"🔒 {wid} 已关「局域网直连」——回到仅本机 127.0.0.1。")
print("   ↻ 监听地址在启动时读取：`bash lighthouse.sh restart` 后生效。")
PYEOF
    ;;

  json-response)
    # 纯 JSON 回应（给不吃 SSE 的隧道/客户端；默认关，标准客户端两种都吃）
    id="${2:?用法: lighthouse.sh json-response <窗口id> on|off|status}"; act="${3:-status}"
    "$PY" - "$REGISTRY" "$id" "$act" <<'PYEOF'
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(sys.argv[1]).parent / "core"))
import config as C
wid, act = sys.argv[2], sys.argv[3]
path = C.REGISTRY_PATH
reg = json.loads(path.read_text(encoding="utf-8"))
wins = reg.get("windows", {})
if wid not in wins:
    print(f"没有这个窗口: {wid}（现有：{', '.join(wins)}）"); raise SystemExit(1)
w = wins[wid]
if act == "status":
    print(f"窗口 {wid} 的 POST 回应：{'纯 JSON（json_response 开）' if C.window_json_response(w) else 'SSE 帧（默认）'}")
    raise SystemExit(0)
if act == "on":
    w["json_response"] = True
elif act == "off":
    w.pop("json_response", None)
else:
    print('用法: lighthouse.sh json-response <窗口id> on|off|status'); raise SystemExit(1)
path.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"{'✅ ' + wid + ' 已改「纯 JSON 回应」' if act == 'on' else '🔒 ' + wid + ' 已切回标准 SSE 回应'}")
print("   ↻ 启动时读取：`bash lighthouse.sh restart` 后生效。")
PYEOF
    ;;

  relay)
    # 用你自己的公网服务器(IP)直连本机窗口 —— 不用域名、不用隧道服务商。
    # 原理：ssh -R 反向隧道，把「服务器IP:端口」转发到本机窗口端口；断线自动重连。
    id="${2:?用法: lighthouse.sh relay <窗口id> on [--host user@ip] [--port N] [--key ~/.ssh/xxx] | off | status}"
    act="${3:-status}"; shift 3 2>/dev/null || true
    host=""; rport=""; keyf=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --host) host="$2"; shift 2 ;;
        --port) rport="$2"; shift 2 ;;
        --key)  keyf="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    "$PY" - "$CORE" "$id" "$act" "$host" "$rport" "$keyf" <<'PYEOF'
import json, os, signal, socket, subprocess, sys, time
import urllib.request
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import config as C

wid, act, host, rport, keyf = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
wins = C.windows(include_disabled=True)
if wid not in wins:
    print(f"没有这个窗口: {wid}（现有：{', '.join(wins)}）"); raise SystemExit(1)
w = wins[wid]
sd = C.state_dir()
(sd / "state").mkdir(parents=True, exist_ok=True)
(sd / "logs").mkdir(parents=True, exist_ok=True)
pidf = sd / "state" / f"relay-{wid}.pid"
logf = sd / "logs" / f"relay-{wid}.log"
cfgpath = Path(sys.argv[1]).parent / "config.local.json"

def load_relay() -> dict:
    try:
        d = json.loads(cfgpath.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return d.get("relay") or {}

def save_relay(host_v: str, port_v: int, key_v: str) -> None:
    d = json.loads(cfgpath.read_text(encoding="utf-8")) if cfgpath.exists() else {}
    d["relay"] = {"host": host_v, "port": port_v, "key": key_v,
                  "_comment": "公网服务器（IP 直连档）：ssh -R 反向隧道；改这个文件不进版本库"}
    cfgpath.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except (OSError, ProcessLookupError):
        return False

def read_pid():
    try:
        return int(pidf.read_text().strip())
    except Exception:
        return None

def stop_tunnel() -> None:
    pid = read_pid()
    if pid and alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(0.8)
    pidf.unlink(missing_ok=True)

if act == "status":
    pid = read_pid()
    r = load_relay()
    if not r.get("host"):
        print("relay 未配置。示例：bash lighthouse.sh relay " + wid + " on --host root@1.2.3.4 --port 18888 --key ~/.ssh/id_ed25519")
        raise SystemExit(0)
    url = f"http://{r['host'].split('@')[-1]}:{r['port']}{w['path']}"
    okr = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=6) as resp:
            okr = f"可达（HTTP {resp.status}）"
    except Exception as e:
        okr = f"暂不可达（{str(e)[:60]}）"
    print(f"relay（{wid}）：{'运行中' if (pid and alive(pid)) else '未运行'}"
          + (f"，pid={pid}" if pid else ""))
    print(f"  公网地址：{url}   {okr}")
    print(f"  服务器：{r['host']}  端口：{r['port']}  日志：{logf}")
    raise SystemExit(0)

if act == "off":
    stop_tunnel()
    print(f"🔒 已关 {wid} 的 relay 隧道（服务器那头的监听会随之消失）。")
    raise SystemExit(0)

if act != "on":
    print("用法: lighthouse.sh relay <窗口id> on [--host user@ip] [--port N] [--key ~/.ssh/xxx] | off | status")
    raise SystemExit(1)

r = load_relay()
host = host or r.get("host") or ""
rport = int(rport) if rport else int(r.get("port") or 18888)
keyf = keyf or r.get("key") or ""
if not host:
    print("还没有配置过服务器。给一次就记住（存进 config.local.json，不进版本库）：")
    print(f"  bash lighthouse.sh relay {wid} on --host root@<你的服务器IP> --port 18888 --key ~/.ssh/<你的私钥>")
    raise SystemExit(1)
save_relay(host, rport, keyf)

stop_tunnel()   # 先收旧进程（含端口占用的）
key_opt = f"-i {os.path.expanduser(keyf)} " if keyf else ""
ssh_cmd = (f"ssh {key_opt}-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 "
           f"-o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -N "
           f"-R 0.0.0.0:{rport}:127.0.0.1:{w['port']} {host}")
loop = f"while :; do {ssh_cmd}; sleep 5; done"
with open(logf, "ab") as lf:
    proc = subprocess.Popen(["/bin/bash", "-c", loop], stdout=lf, stderr=subprocess.STDOUT,
                            start_new_session=True)
pidf.write_text(str(proc.pid))
ip = host.split("@")[-1]
url = f"http://{ip}:{rport}{w['path']}"
okr = ""
for _ in range(4):
    time.sleep(2.5)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(req, timeout=6) as resp:
            okr = f"✅ 公网可达（HTTP {resp.status}）"; break
    except Exception as e:
        okr = f"⏳ 暂未连通（{str(e)[:50]}）"
print(f"{'✅' if okr.startswith('✅') else '⚠️'} {wid} 的 relay 已启动（pid={proc.pid}，断线自动重连）")
print(f"   公网地址：{url}   {okr}")
if not okr.startswith("✅"):
    print("   排查：服务器 sshd 需 GatewayPorts clientspecified（或 yes）；端口别被防火墙拦；--key 是否正确。")
    print(f"   日志：{logf}")
print("   关闭：bash lighthouse.sh relay " + wid + " off")
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

  kb)
    shift
    exec "$PY" "$CORE/kb_cli.py" "$@"
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
