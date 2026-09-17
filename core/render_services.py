#!/usr/bin/env python3
"""按 windows.json 生成/移除各平台的服务定义（macOS launchd / Linux systemd user）。

用法：
  render_services.py            # 生成/更新所有 enabled 窗口的服务定义
  render_services.py list       # 列出 enabled 窗口 id
  render_services.py print <id> # 打印某个窗口的服务定义（调试用）

- macOS：写 ~/Library/LaunchAgents/com.lighthouse.window-<id>.plist，服务名 com.lighthouse.window-<id>
- Linux：写 ~/.config/systemd/user/lighthouse-window-<id>.service，服务名 lighthouse-window-<id>
- 其它平台：打印前台运行命令（自己用 screen/tmux/nohup 守着即可）
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

ROOT_DIR = C.ROOT_DIR
SERVER = ROOT_DIR / "core" / "server.py"
STATE = C.state_dir()
LOGS = STATE / "logs"

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{server}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>WINDOW_ID</key>
        <string>{wid}</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>{wd}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>
</dict>
</plist>
"""

SYSTEMD = """[Unit]
Description=Lighthouse window "{wid}"
After=network.target

[Service]
WorkingDirectory={wd}
Environment=WINDOW_ID={wid}
ExecStart={py} {server}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""


def target_dir() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library/LaunchAgents"
    return Path.home() / ".config/systemd/user"


def unit_name(wid: str) -> str:
    return f"com.lighthouse.window-{wid}.plist" if platform.system() == "Darwin" else f"lighthouse-window-{wid}.service"


def render_one(wid: str) -> str:
    py = os.environ.get("LIGHTHOUSE_PY") or sys.executable or "python3"
    if platform.system() == "Darwin":
        return PLIST.format(
            label=f"com.lighthouse.window-{wid}", py=py, server=str(SERVER), wid=wid,
            state=str(STATE), wd=str(ROOT_DIR),
            log=str(LOGS / f"window-{wid}.log"), log_err=str(LOGS / f"window-{wid}.err.log"),
        )
    return SYSTEMD.format(wid=wid, py=py, server=str(SERVER), state=str(STATE), wd=str(ROOT_DIR))


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "render"
    wins = C.windows()

    if cmd == "list":
        print("\n".join(wins))
        return 0

    if cmd == "print":
        wid = sys.argv[2]
        print(render_one(wid))
        return 0

    if platform.system() not in ("Darwin", "Linux"):
        print(f"[{platform.system()}] 不支持自动生成服务定义。请前台手动跑：")
        for wid in wins:
            print(f"  WINDOW_ID={wid} python3 {SERVER}")
        return 0

    d = target_dir()
    d.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    for wid in wins:
        out = d / unit_name(wid)
        out.write_text(render_one(wid), encoding="utf-8")
        print(f"rendered: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
