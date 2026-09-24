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
    # ⚠️ 只把 visibility=public 的窗口写进隧道 —— `local` 的意思就是「不要对外」。
    #    以前这里只加一句注释照样往里写，结果标了 local 的窗口仍能从公网访问（实测 http=200）。
    #    安全承诺必须落在行为上：这里的过滤就是那句承诺的执行点。
    public = {k: v for k, v in wins.items() if v.get("visibility", "local") == "public"}
    skipped = [k for k in wins if k not in public]
    if skipped:
        lines.append(f"  # 以下窗口 visibility=local，已从公网入口剔除：{', '.join(skipped)}")
    for wid, w in public.items():
        lines += [
            f"  - hostname: {cfg['hostname']}",
            f"    path: {w['path']}*",
            f"    service: http://127.0.0.1:{w['port']}",
            f"    # ↑ 窗口: {wid}（{w.get('title', wid)}）",
        ]
    # 受控资料库的短地址：同事的地址形如 https://域名/kb-<口令>（不含窗口路径）。
    # 只有在「只有一个资料库窗口」时才加这条 —— 一条 /kb-* 规则只能指向一个服务，
    # 多个资料库窗口时短地址会串门，那种情况只保留长地址（<窗口路径>/kb-<口令>）。
    kb_wins = {k: v for k, v in public.items() if ((v.get("kb") or {}).get("enabled") is True)}
    if len(kb_wins) == 1:
        wid, w = next(iter(kb_wins.items()))
        lines += [
            f"  - hostname: {cfg['hostname']}",
            "    path: /kb-*",
            f"    service: http://127.0.0.1:{w['port']}",
            f"    # ↑ 受控资料库「{wid}」的短地址（同事的 /kb-<口令>）",
        ]
    elif len(kb_wins) > 1:
        lines.append(f"  # 有多个资料库窗口（{', '.join(kb_wins)}）：短地址 /kb-* 会串门，"
                     f"这些窗口只用长地址 <窗口路径>/kb-<口令>")

    if cfg.get("spare_hostname"):
        first = next(iter(public.items()), None)          # 备用入口同样只能指向 public 窗口
        if first:
            lines += [f"  - hostname: {cfg['spare_hostname']}   # 备用入口 → {first[0]} 窗口",
                      f"    service: http://127.0.0.1:{first[1]['port']}"]
    lines += ["  - service: http_status:404", ""]
    return "\n".join(lines)


def main() -> int:
    apply_ = "--apply" in sys.argv
    cfg = C.load()
    wins = C.windows()
    skipped = [k for k, v in wins.items() if v.get("visibility", "local") != "public"]
    if skipped:
        print(f"⏭  跳过（visibility=local，不写进公网入口）：{', '.join(skipped)}")
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
