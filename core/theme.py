"""设计令牌与主题：门户所有页面的颜色/间距/圆角/字号都从这里取。

为什么单独一个文件
------------------
门户有十几个页面，早先颜色和间距是散落在各页 f-string 里的字面量（#0a6cff、10px、14px…），
改一次要翻十几处、还容易漏。现在集中在这里：

  · 颜色：语义化命名（ink/dim/line/bg/card/brand/ok/warn/danger/accent），页面只引用变量
  · 间距：SPACE 刻度 4/8/12/16/24/32 → 页面里用 --s1..--s6
  · 圆角/字号/阴影：同一套刻度（--r1..--r4 / --f1..--f4 / --sh1）
  · 主题：THEMES 里三套配色，窗口配置 `kb.portal.theme` 选一套，默认 teal（松石绿）

给后来人（包括自己）的两条硬规矩
--------------------------------
1. **页面里不许写死颜色**：需要新颜色先加进 THEMES，再在页面里用 var(--x)。
   测试 `test_kb.py` 里有断言盯着这条（页面 HTML 里出现十六进制颜色即失败）。
2. **一屏只有一个主按钮**：主操作用 `class="primary"`，其余一律 ghost/danger/quiet/tiny，
   否则满屏蓝按钮，用户不知道先点哪个。

配色说明（每套的用途）
----------------------
teal    松石绿：温和、SaaS 常见 —— 默认（用户 2026-09-24 选定）
indigo  墨蓝 + 紫强调：像公文封皮，正式耐看，长表格不刺眼，偏公文/招投标气质
paper   暖纸质：米白底 + 赭石强调，像阅读器，适合以「看文件」为主的部署
"""

from __future__ import annotations

# ---------------------------------------------------------------- 主题配色
# 每套都提供同一组语义变量；数值都挑过对比度（正文/次要文字在白底上 ≥ 4.5:1）
THEMES: dict[str, dict[str, str]] = {
    "indigo": {
        "label": "墨蓝（默认）",
        "bg": "#f5f6f9",          # 页面底色：冷白
        "card": "#ffffff",
        "ink": "#15181d",         # 正文
        "dim": "#666e7a",         # 次要文字（≥4.5:1）
        "line": "#e4e7ec",        # 分隔线/边框
        "brand": "#1f4e8c",       # 主色：墨蓝（公文封皮）
        "brand_d": "#173c6d",     # 主色 hover
        "brand_b": "#eaf1fb",     # 主色浅底（选中/提示条）
        "accent": "#6c5ce7",      # 强调色：紫（当前页、链接、焦点）
        "accent_b": "#f1eefe",
        "ok": "#0f7b4a", "ok_b": "#e7f6ee",
        "warn": "#8a5a00", "warn_b": "#fff5e3",
        "danger": "#b3261e", "danger_b": "#fdeceb",
    },
    "teal": {
        "label": "松石绿",
        "bg": "#f4f7f7",
        "card": "#ffffff",
        "ink": "#141b1a",
        "dim": "#5f6b69",
        "line": "#e2e8e7",
        "brand": "#0f6b5f",
        "brand_d": "#0b524a",
        "brand_b": "#e6f4f1",
        "accent": "#0b7285",
        "accent_b": "#e6f2f5",
        "ok": "#0f7b4a", "ok_b": "#e7f6ee",
        "warn": "#8a5a00", "warn_b": "#fff5e3",
        "danger": "#b3261e", "danger_b": "#fdeceb",
    },
    "paper": {
        "label": "暖纸质",
        "bg": "#f7f4ee",
        "card": "#fffdf9",
        "ink": "#1c1a16",
        "dim": "#6d675c",
        "line": "#e8e2d6",
        "brand": "#8a4b1f",
        "brand_d": "#6d3a16",
        "brand_b": "#f7ece1",
        "accent": "#8a6d1f",
        "accent_b": "#f6f0dc",
        "ok": "#3f6b35", "ok_b": "#eaf3e6",
        "warn": "#8a5a00", "warn_b": "#fbf0d8",
        "danger": "#a8322a", "danger_b": "#fbeae8",
    },
}

DEFAULT_THEME = "teal"          # 2026-09-24 用户选定：默认松石绿

# ---------------------------------------------------------------- 与主题无关的刻度
# 间距/圆角/字号/阴影：所有页面共用，改这里就等于全站改版
TOKENS = """
    /* 间距刻度 */
    --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px; --s6: 32px;
    /* 圆角 */
    --r1: 7px; --r2: 10px; --r3: 14px; --r4: 999px;
    /* 字号 */
    --f1: 12px; --f2: 13px; --f3: 15px; --f4: 17px; --f5: 22px;
    /* 结构中性色：输入框描边、灰按钮、代码底色 —— 三套主题通用 */
    --line2: #d5d8de; --btn2: #eceef2; --btn2h: #e2e5ea;
    --th-bg: color-mix(in srgb, var(--brand) 4%, #fff);
    --row-hover: color-mix(in srgb, var(--brand) 3%, #fff);
    --code-bg: color-mix(in srgb, var(--ink) 5%, #fff);
    --focus: color-mix(in srgb, var(--accent) 32%, #fff);
    /* 阴影：只给卡片一层极淡的，别堆 */
    --sh1: 0 1px 2px rgba(16, 24, 40, .04);
    --sh2: 0 4px 14px rgba(16, 24, 40, .06);
"""


_CURRENT = {"name": DEFAULT_THEME}


def set_current(name: str | None) -> str:
    """门户启动/请求进来时调用：把窗口配置里的主题名落成当前主题。"""
    _CURRENT["name"] = resolve(name)
    return _CURRENT["name"]


def current() -> str:
    return _CURRENT["name"]


def theme_names() -> list[str]:
    """可用主题名（给 CLI/文档列出来）。"""
    return list(THEMES)


def is_theme(name: str | None) -> bool:
    return bool(name) and name in THEMES


def theme_css(name: str | None = None) -> str:
    """生成 CSS 变量块。名字不认识就退回默认主题（不报错，页面不能因为配置写错就白屏）。"""
    t = THEMES.get(name or "") or THEMES[DEFAULT_THEME]
    parts = " ".join(f"--{k}: {v};" for k, v in t.items() if k != "label")
    # 兼容早先页面里已经在用的老名字（--blue/--green/--amber/--red/--violet）
    legacy = ("--blue: var(--brand); --blue-d: var(--brand-d); --green: var(--ok);"
              " --green-b: var(--ok-b); --amber: var(--warn); --amber-b: var(--warn-b);"
              " --red: var(--danger); --red-b: var(--danger-b);"
              " --violet: var(--accent); --violet-b: var(--accent-b);")
    return f":root {{ color-scheme: light; {parts}{legacy}{TOKENS} }}"


def resolve(name: str | None) -> str:
    """把配置里的主题名归一化成有效主题名。"""
    return name if isinstance(name, str) and name in THEMES else DEFAULT_THEME
