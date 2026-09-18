#!/usr/bin/env python3
"""写开关 CLI —— 把「能不能改」的权利交给用户。

开关文件：<状态目录>/state/window-write.json（默认 ~/.lighthouse；server 每次调用现读，改完立即生效，不用重启）

用法：
  switch.py set <窗口> on [分钟数]   # 打开（给了分钟数就到期自动关闭）
  switch.py set <窗口> off          # 关闭
  switch.py status [窗口...]        # 看状态
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_ROOT = Path(os.path.expanduser(os.environ.get("LIGHTHOUSE_STATE", "~/.lighthouse")))
SWITCH_PATH = STATE_ROOT / "state" / "window-write.json"
CST = timezone(timedelta(hours=8))


def load() -> dict:
    try:
        return json.loads(SWITCH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(d: dict) -> None:
    SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    SWITCH_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def fmt(entry: dict) -> str:
    until = entry.get("until")
    live = bool(entry.get("enabled")) and not (until and time.time() > float(until))
    if live and until:
        return f"✅ 可写（{datetime.fromtimestamp(float(until), CST):%Y-%m-%d %H:%M} 自动关闭）"
    if live:
        return "✅ 可写（无期限，需手动关）"
    if until:
        return "🔒 只读（开关已到期自动关闭）"
    return "🔒 只读"


def main() -> int:
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 1
    cmd = a[0]

    if cmd == "status":
        d = load()
        ids = a[1:] or sorted(d)
        if not ids:
            print("(没有任何窗口开过写开关 → 全部只读)")
            return 0
        for i in ids:
            print(f"{i}: {fmt(d.get(i, {}))}")
        return 0

    if cmd == "set" and len(a) >= 3:
        wid, val = a[1], a[2].lower()
        minutes = None
        if len(a) > 3:
            if not a[3].isdigit():
                print("分钟数必须是整数")
                return 1
            minutes = int(a[3])
        d = load()
        if val in ("on", "true", "1", "yes"):
            entry = {"enabled": True, "at": datetime.now(CST).isoformat(timespec="seconds")}
            if minutes:
                entry["until"] = time.time() + minutes * 60
            d[wid] = entry
            save(d)
            print(f"✅ 写开关已打开: {wid}" + (f"（{minutes} 分钟后自动关闭）" if minutes else "（无期限，记得手动关！）"))
            print("   开启期间，对方可以 write_file / edit_file / make_dir / delete_file（每次改动自动备份+记账）。")
            return 0
        d[wid] = {"enabled": False, "at": datetime.now(CST).isoformat(timespec="seconds")}
        save(d)
        print(f"🔒 写开关已关闭: {wid}（回到只读）")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
