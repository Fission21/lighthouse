#!/usr/bin/env python3
"""示例源码：一段查窗口状态的假实现（演示「源码也能按需给看」）。"""

WINDOWS = {
    "demo": {"port": 8951, "path": "/mcp-demo-7g2xq1", "writable": False},
}


def describe(win_id: str) -> str:
    w = WINDOWS.get(win_id)
    if not w:
        return f"没有这个窗口: {win_id}"
    state = "可写" if w["writable"] else "只读"
    return f"{win_id}: http://127.0.0.1:{w['port']}{w['path']}（{state}）"


if __name__ == "__main__":
    for wid in WINDOWS:
        print(describe(wid))
