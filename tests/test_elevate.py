#!/usr/bin/env python3
"""提权测试（第 4 套）：验证「agent 只能申请，用户批准才生效」。

自带一套隔离环境（临时项目 + 临时注册表 + 临时状态目录 + 自己起一个服务），
不碰你的 windows.json，也不碰 ~/.lighthouse。

覆盖：
  ① 未授权时，范围外的文件确实读不到
  ② agent 调 request_access → 只得到 pending；**范围不会变**（agent 无法自我提权）
  ③ 用户用 CLI 批准（lighthouse.sh approve）→ 立刻可读
  ④ 提权**不能突破**默认拉黑（.env 依旧读不到）与 exclude
  ⑤ 用户收回（deny）→ 回到原始范围
  ⑥ 预授权窗口（elevate）：上限内的申请自动批准；超出上限的申请仍转 pending
  ⑦ 过期即失效
  ⑧ 常驻策略（auto-grant）：开启后上限内申请立即生效；超出上限仍 pending；
     关闭后立即回到 pending；上限配置可疑时 fail-closed；拉黑始终压过自动授予
  ⑨ 时长可自选（30m / 2h / 1d / forever）：解析不吞错；approve --for 30m 真的落成
     30 分钟有效期；auto-grant --ttl 2h 让每次自动授予带 2 小时到期
  ⑩ 对话内授权（chat_approval）：默认关时 user_confirmed 也落待批（fail-closed）；
     开启后用户在对话里同意 → agent 带 user_confirmed=true 即生效；带 ceiling 上限约束；
     关掉后立即回到待批

用法: python3 test_elevate.py [--keep]
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

fails: list[str] = []
count = 0


def check(label: str, good: bool, detail: str = "") -> None:
    global count
    count += 1
    print(f"  {'✅' if good else '❌'} {label}{(' — ' + detail) if detail else ''}")
    if not good:
        fails.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def call(session, name: str, args: dict) -> dict:
    res = await session.call_tool(name, args)
    text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def cli(*args: str, env: dict) -> str:
    """跑 lighthouse.sh 的真 CLI（验证用户侧命令真的能用）。"""
    r = subprocess.run(["bash", str(REPO / "lighthouse.sh"), *args],
                       capture_output=True, text=True, env=env, cwd=str(REPO))
    return (r.stdout + r.stderr).strip()


async def main() -> int:
    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="lh-elevate-"))
    root = tmp / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "code").mkdir()
    (root / "docs" / "readme.md").write_text("文档：可以看\n", encoding="utf-8")
    (root / "code" / "app.py").write_text("print('机密代码')\n", encoding="utf-8")
    (root / "docs" / ".env").write_text("DEMO_KEY=假密钥\n", encoding="utf-8")

    registry = tmp / "windows.json"
    registry.write_text(json.dumps({"windows": {"elev": {
        "title": "提权测试窗", "root": str(root),
        "include": ["README*", "docs/**"], "exclude": [], "deny_extra": [],
        "port": port, "path": "/w-elev-test", "visibility": "local",
        "write": {"enabled": False},
    }}}, ensure_ascii=False, indent=2), encoding="utf-8")

    state = tmp / "state"
    env = {**os.environ,
           "LIGHTHOUSE_STATE": str(state),
           "LIGHTHOUSE_PY": PY,
           "LIGHTHOUSE_REGISTRY": str(registry),
           "WINDOW_ID": "elev", "WINDOW_PORT": str(port), "WINDOW_PATH": "/w-elev-test",
           "WINDOW_REGISTRY": str(registry)}
    url = f"http://127.0.0.1:{port}/w-elev-test"
    print(f"提权测试 | 隔离环境: {tmp}\n窗口: elev (include = README*, docs/**)  服务: {url}\n")

    srv = subprocess.Popen([PY, str(REPO / "core" / "server.py")], env=env,
                           stdout=open(tmp / "server.log", "wb"), stderr=subprocess.STDOUT)
    try:
        for _ in range(40):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.5)
        else:
            print("❌ 服务没起来"); print((tmp / "server.log").read_text()[-800:]); return 2

        async with streamable_http_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()

                print("① 初始范围：文档可见，代码不可见")
                d = await call(session, "read_file", {"path": "docs/readme.md"})
                check("docs/readme.md 可读", "content" in d, d.get("error", "")[:40])
                c = await call(session, "read_file", {"path": "code/app.py"})
                check("code/app.py 被拒（不在 include）", "不在给看范围" in json.dumps(c, ensure_ascii=False),
                      c.get("error", "")[:50])

                print("\n② agent 申请提权 → 只得到 pending，范围不变")
                req = await call(session, "request_access",
                                 {"include": ["code/**"], "reason": "用户想看代码"})
                check("request_access 返回 pending", req.get("status") == "pending", json.dumps(req, ensure_ascii=False)[:80])
                check("pending 信息里带了用户的批准命令", "approve" in json.dumps(req, ensure_ascii=False))
                c2 = await call(session, "read_file", {"path": "code/app.py"})
                check("申请后代码依然不可读（无法自我提权）", "不在给看范围" in json.dumps(c2, ensure_ascii=False))
                info = await call(session, "window_info", {})
                check("window_info 里能看到待批申请", bool(info.get("scope_elevation", {}).get("pending")))

                print("\n③ 用户用 CLI 批准 → 立刻可读")
                out = cli("approve", "elev", "--minutes", "30", env=env)
                check("CLI approve 成功", "已批准" in out, out.splitlines()[0][:70] if out else "")
                c3 = await call(session, "read_file", {"path": "code/app.py"})
                check("批准后代码可读", "内容" not in "" and bool(c3.get("content")), str(c3)[:70])

                print("\n④ 提权不能突破默认拉黑 / 排除")
                envf = await call(session, "read_file", {"path": "docs/.env"})
                check(".env 仍被拒（拉黑压过提权）", "拉黑" in json.dumps(envf, ensure_ascii=False), envf.get("error", "")[:40])
                esc = await call(session, "read_file", {"path": "../../etc/hosts"})
                esc_blob = json.dumps(esc, ensure_ascii=False)
                check("越界仍被拒（路径逃逸）", "越界" in esc_blob or "给看范围" in esc_blob, esc.get("error", "")[:50])
                esc2 = await call(session, "read_file", {"path": "../../etc/passwd"})
                check("越界 + 敏感名 → 仍被拒", "越界" in json.dumps(esc2, ensure_ascii=False)
                      or "给看范围" in json.dumps(esc2, ensure_ascii=False)
                      or "拉黑" in json.dumps(esc2, ensure_ascii=False), esc2.get("error", "")[:50])

                print("\n⑤ 用户收回 → 回到原始范围")
                out = cli("deny", "elev", env=env)
                check("CLI deny 成功", "已收回" in out, out[:60])
                c4 = await call(session, "read_file", {"path": "code/app.py"})
                check("收回后代码重新不可读", "不在给看范围" in json.dumps(c4, ensure_ascii=False))

                print("\n⑥ 预授权窗口：上限内自动批准；超出上限转 pending")
                out = cli("elevate", "elev", "10", "--scope", "code/**", env=env)
                check("CLI elevate 成功", "预授权窗口" in out, out.splitlines()[0][:70] if out else "")
                g1 = await call(session, "request_access", {"include": ["code/**"], "reason": "上限内申请"})
                check("上限内申请自动批准", g1.get("status") == "granted", json.dumps(g1, ensure_ascii=False)[:80])
                c5 = await call(session, "read_file", {"path": "code/app.py"})
                check("自动批准后代码可读", bool(c5.get("content")))
                g2 = await call(session, "request_access", {"include": ["**/*"], "reason": "想全都要"})
                check("超上限申请不会自动批（转 pending）", g2.get("status") == "pending", json.dumps(g2, ensure_ascii=False)[:80])

                print("\n⑦ 越权模式被拦 + 过期即失效")
                badp = await call(session, "request_access", {"include": ["../../etc"], "reason": "坏规则"})
                check("含 .. 的规则被拒", "error" in badp, str(badp)[:60])

                scope_file = state / "state" / "window-scope.json"
                data = json.loads(scope_file.read_text(encoding="utf-8"))
                data["elev"]["grant"]["until"] = time.time() - 5          # 手动设为已过期
                scope_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                c6 = await call(session, "read_file", {"path": "code/app.py"})
                check("过期后自动失效（回到原始范围）", "不在给看范围" in json.dumps(c6, ensure_ascii=False))

                print("\n⑧ 常驻策略（auto-grant）：开了之后上限内的申请立即生效，不用再跑本地命令")
                cli("deny", "elev", env=env)                      # 清掉预授权窗口，确保测的是常驻策略
                g3 = await call(session, "request_access", {"include": ["code/**"], "reason": "默认没开策略"})
                check("默认（未开 auto-grant）申请仍只得到 pending", g3.get("status") == "pending",
                      json.dumps(g3, ensure_ascii=False)[:80])

                out = cli("auto-grant", "elev", "on", "--ceiling", "code/**", env=env)
                check("CLI auto-grant on 成功", "申请即授予" in out, out.splitlines()[0][:70] if out else "")
                g4 = await call(session, "request_access", {"include": ["code/**"], "reason": "上限内申请"})
                check("上限内申请立即生效（无需本地命令）", g4.get("status") == "granted",
                      json.dumps(g4, ensure_ascii=False)[:80])
                c7 = await call(session, "read_file", {"path": "code/app.py"})
                check("自动生效后代码可读", bool(c7.get("content")))
                g5 = await call(session, "request_access", {"include": ["**/*"], "reason": "想全都要"})
                check("超出常驻上限仍转 pending", g5.get("status") == "pending", json.dumps(g5, ensure_ascii=False)[:80])
                envf2 = await call(session, "read_file", {"path": "docs/.env"})
                check("自动授予也读不到 .env（拉黑优先）", "拉黑" in json.dumps(envf2, ensure_ascii=False))
                info2 = await call(session, "window_info", {})
                check("window_info 暴露常驻策略", info2.get("scope_elevation", {}).get("auto_grant") is True,
                      json.dumps(info2.get("scope_elevation", {}), ensure_ascii=False)[:80])

                out = cli("auto-grant", "elev", "off", env=env)
                check("CLI auto-grant off 成功", "已关闭" in out, out.splitlines()[0][:60] if out else "")
                cli("deny", "elev", env=env)
                g6 = await call(session, "request_access", {"include": ["code/**"], "reason": "关掉策略之后"})
                check("收紧后立即回到 pending（不重启也生效）", g6.get("status") == "pending",
                      json.dumps(g6, ensure_ascii=False)[:80])

                reg = json.loads(registry.read_text(encoding="utf-8"))
                reg["windows"]["elev"]["auto_grant"] = True
                reg["windows"]["elev"]["elevation_ceiling"] = ["/etc/**"]      # 绝对路径：非法配置
                registry.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
                cli("deny", "elev", env=env)
                g7 = await call(session, "request_access", {"include": ["code/**"], "reason": "配置可疑"})
                check("上限配置可疑时退化成 pending（fail-closed）", g7.get("status") == "pending",
                      json.dumps(g7, ensure_ascii=False)[:80])

                print("\n⑨ 时长可自选：30m / 2h / 1d / forever（短长由用户挑）")
                if str(REPO / "core") not in sys.path:
                    sys.path.insert(0, str(REPO / "core"))
                import scope as S
                check("parse_duration('30m') = 30 分钟", S.parse_duration("30m") == 30)
                check("parse_duration('2h') = 120 分钟", S.parse_duration("2h") == 120)
                check("parse_duration('1d') = 1440 分钟", S.parse_duration("1d") == 1440)
                check("parse_duration('90') = 90（纯数字按分钟）", S.parse_duration("90") == 90)
                check("parse_duration('forever') = 无期限", S.parse_duration("forever") is None)
                try:
                    S.parse_duration("两天半")
                    bad_dur = False
                except ValueError:
                    bad_dur = True
                check("看不懂的时长必须报错（不许静默吞）", bad_dur)

                cli("deny", "elev", env=env)
                await call(session, "request_access", {"include": ["code/**"], "reason": "测时长"})
                out = cli("approve", "elev", "--for", "30m", env=env)
                check("CLI approve --for 30m 成功", "已批准" in out and "30 分钟" in out,
                      out.replace("\n", " ")[:90] if out else "")
                c8 = await call(session, "read_file", {"path": "code/app.py"})
                check("按 --for 批准后代码可读", bool(c8.get("content")))
                data = json.loads((state / "state" / "window-scope.json").read_text(encoding="utf-8"))
                until = data["elev"]["grant"].get("until")
                left = None if until is None else int(until - time.time())
                check("有效期真的落在 ≈30 分钟（不是无期限）", left is not None and abs(left - 1800) < 120,
                      f"剩 {left}s")

                cli("deny", "elev", env=env)
                out = cli("auto-grant", "elev", "on", "--ceiling", "code/**", "--ttl", "2h", env=env)
                check("CLI auto-grant --ttl 2h 成功", "2 小时" in out, out.replace("\n", " ")[:90] if out else "")
                g9 = await call(session, "request_access", {"include": ["code/**"], "reason": "带 ttl 的自动授予"})
                check("自动授予仍 granted", g9.get("status") == "granted", json.dumps(g9, ensure_ascii=False)[:80])
                check("自动授予的回执带时长", g9.get("duration") == "2 小时", json.dumps(g9, ensure_ascii=False)[:80])
                data = json.loads((state / "state" / "window-scope.json").read_text(encoding="utf-8"))
                until = data["elev"]["grant"].get("until")
                left = None if until is None else int(until - time.time())
                check("auto-grant 的授权真的落在 ≈2 小时", left is not None and abs(left - 7200) < 150, f"剩 {left}s")

                print("\n⑩ 对话内授权：用户在对话里说「授权你」即生效（默认关，fail-closed）")
                cli("deny", "elev", env=env)
                cli("auto-grant", "elev", "off", env=env)
                rg = json.loads(registry.read_text(encoding="utf-8"))
                rg["windows"]["elev"].pop("auto_grant", None)
                rg["windows"]["elev"].pop("elevation_ceiling", None)
                registry.write_text(json.dumps(rg, ensure_ascii=False, indent=2), encoding="utf-8")
                c10 = await call(session, "request_access",
                                 {"include": ["code/**"], "reason": "用户说授权了", "user_confirmed": True})
                check("未开策略时 user_confirmed 也落待批（fail-closed）", c10.get("status") == "pending",
                      json.dumps(c10, ensure_ascii=False)[:80])
                out = cli("chat-approval", "elev", "on", env=env)
                check("CLI chat-approval on 成功", "对话内授权" in out, out.replace("\n", " ")[:90] if out else "")
                cli("deny", "elev", env=env)
                c11 = await call(session, "request_access",
                                 {"include": ["code/**"], "reason": "用户刚才明确同意了", "user_confirmed": True})
                check("开启后 user_confirmed=true 立即生效（via chat-approval）",
                      c11.get("status") == "granted" and c11.get("via") == "chat-approval",
                      json.dumps(c11, ensure_ascii=False)[:90])
                c12 = await call(session, "read_file", {"path": "code/app.py"})
                check("对话内授权后代码可读", bool(c12.get("content")))
                info3 = await call(session, "window_info", {})
                check("window_info 暴露对话内授权开关", info3.get("scope_elevation", {}).get("chat_approval") is True)
                out = cli("chat-approval", "elev", "off", env=env)
                check("CLI chat-approval off 成功", "已关闭" in out, out.replace("\n", " ")[:80] if out else "")
                cli("deny", "elev", env=env)
                c13 = await call(session, "request_access",
                                 {"include": ["code/**"], "reason": "关掉之后", "user_confirmed": True})
                check("关掉后立即回到待批（不重启也生效）", c13.get("status") == "pending",
                      json.dumps(c13, ensure_ascii=False)[:80])
                out = cli("chat-approval", "elev", "on", "--ceiling", "code/**", env=env)
                check("CLI chat-approval on --ceiling 成功", "对话内授权" in out, out.replace("\n", " ")[:80] if out else "")
                cli("deny", "elev", env=env)
                c14 = await call(session, "request_access",
                                 {"include": ["**/*"], "reason": "超出上限", "user_confirmed": True})
                check("超出上限的 user_confirmed 仍转待批", c14.get("status") == "pending",
                      json.dumps(c14, ensure_ascii=False)[:80])

    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
        if "--keep" not in sys.argv:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n===== 提权测试：{count - len(fails)} 项通过，{len(fails)} 项失败 =====")
    if fails:
        print("失败项:", fails)
    return 0 if not fails else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
