#!/usr/bin/env python3
"""按 windows.json + config.json 生成 cloudflared 隧道配置（单主机名 + 路径分流到各窗口端口）。

用法：
  render_ingress.py            # 只打印（dry-run）
  render_ingress.py --apply    # 写入配置文件并重启隧道进程

为什么是「一个主机名 + 路径分流」：加窗口时不用再动 DNS —— 新窗口只是多一条 path 规则。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402


def build(cfg: dict, wins: dict) -> str:
    yml = Path(cfg["cloudflared_config"]).expanduser()
    lines = [
        f"tunnel: {cfg['tunnel_id']}",
        f"credentials-file: {Path.home() / '.cloudflared' / (cfg['tunnel_id'] + '.json')}",
        "loglevel: info",
        "",
        "# 由 lighthouse 的 render_ingress.py 生成：单主机名 + 路径分流到各窗口端口。",
        "# 手工改动会被下一次 render 覆盖；要改范围请改 windows.json，要改域名请改 config.json。",
        "ingress:",
    ]
    for wid, w in wins.items():
        vis = w.get("visibility", "local")
        note = "  # ⚠️ visibility=local（私密窗口，对外暴露前请三思）" if vis == "local" else ""
        lines += [
            f"  - hostname: {cfg['hostname']}",
            f"    path: {w['path']}*",
            f"    service: http://127.0.0.1:{w['port']}{note}",
            f"    # ↑ 窗口: {wid}（{w.get('title', wid)}）",
        ]
    if cfg.get("spare_hostname"):
        first = next(iter(wins.items()), None)
        if first:
            lines += [f"  - hostname: {cfg['spare_hostname']}   # 备用入口 → {first[0]} 窗口",
                      f"    service: http://127.0.0.1:{first[1]['port']}"]
    lines += ["  - service: http_status:404", ""]
    return "\n".join(lines)


def main() -> int:
    apply_ = "--apply" in sys.argv
    cfg = C.load()
    wins = C.windows()
    if not wins:
        print("（没有启用的窗口：先在 windows.json 里登记一条，或跑 `bash lighthouse.sh new <id> <路径>`）")
        return 1
    if not cfg.get("tunnel_id"):
        print("""config.json 里还没有 tunnel_id —— 先建隧道（一次性）：

  cloudflared tunnel login
  cloudflared tunnel create lighthouse          # 记下输出的 UUID
  cloudflared tunnel route dns <UUID> <你的域名>   # 必须写 UUID，写隧道名会认错隧道
  # 然后把 UUID 填进 config.json 的 tunnel_id，再跑：bash lighthouse.sh publish
""")
        return 1

    text = build(cfg, wins)
    print(text)
    if apply_:
        yml = Path(cfg["cloudflared_config"]).expanduser()
        yml.parent.mkdir(parents=True, exist_ok=True)
        yml.write_text(text, encoding="utf-8")
        print(f"已写入 {yml}，重启隧道进程…")
        subprocess.run(["pkill", "-f", f"config {yml}"], capture_output=True)
        time.sleep(5)
        out = subprocess.run(["pgrep", "-fl", f"config {yml}"], capture_output=True, text=True).stdout.strip()
        if out:
            print("隧道进程:", out.split("\n")[0])
        else:
            print("隧道没在跑 —— 用你的守护方式拉起，例如：")
            print(f"  cloudflared tunnel --config {yml} run &")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
