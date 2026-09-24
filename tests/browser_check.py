"""真浏览器交互验收（可选套件）：用真鼠标事件走一遍门户，并给排版做体检。

为什么单独一套：接口测试证明不了「点得动、拖得成、排得齐、看着不累」。
这个脚本用 Chrome DevTools Protocol 发**真鼠标事件**（不是 JS 里 `.click()`），
每一步都截图，最后按三条口径报告：① 操作能不能走通；② 排版对齐/间距；③ 信息是否过载、手机端会不会左右晃。

前置：本机 Chrome 带 `--remote-debugging-port=9222` 开着（脚本自己连，不会去抢 profile 锁）。

用法::

    python tests/browser_check.py --admin-url "<管理页地址含管理令>" --files-url "<同事资料页地址>" \
        [--shots /tmp/shots] [--window bidkb] [--keep]

不带参数时用 `lighthouse.sh kb admin-url <窗口>` 与库里的第一条同事地址自动拼地址。
带 `--window` 时脚本结束会把测试期间动过的资料挪回原位、删掉临时文件夹（`--keep` 保留现场）。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PORT = 9222
STALE = "（临时）交互测试"            # 临时文件夹统一前缀：收尾/开跑时按前缀清残留
TMP_DIR = STALE + "-" + time.strftime("%H%M%S")   # 每轮带时间戳，免得撞上一轮
results: list[tuple[str, bool, str]] = []


def check(label: str, good: bool, detail: str = "") -> None:
    results.append((label, good, str(detail)))
    print(f"  {'✅' if good else '❌'} {label}{(' — ' + str(detail)[:160]) if detail else ''}")


# ---------------------------------------------------------------- 极简 CDP 客户端
def _http(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # 本机直连，不走代理
    with op.open(req, timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


class Page:
    def __init__(self) -> None:
        self.ws = None
        self.id = 0

    async def open(self, url: str) -> None:
        import websockets
        t = _http(f"/json/new?{urllib.parse.quote(url, safe='')}", "PUT")
        self.ws = await websockets.connect(t["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
        self.target = t["id"]

    async def safe(self, method: str, **params):
        """点了会跳转的按钮时，mouseReleased 的应答可能永远不来 —— 当成「点了，页面在跳」。"""
        try:
            return await self.cmd(method, **params)
        except (TimeoutError, asyncio.CancelledError):
            return {}

    async def cmd(self, method: str, **params):
        assert self.ws is not None, "先 open()"
        self.id += 1
        await self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if msg.get("method") == "Page.javascriptDialogOpening":          # 原生确认框：自动点掉
                self.id += 1
                await self.ws.send(json.dumps({"id": self.id,
                                               "method": "Page.handleJavaScriptDialog",
                                               "params": {"accept": True}}))
                continue
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def js(self, expr: str):
        r = await self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def shot(self, path: Path, width: int = 1440, full: bool = True) -> None:
        await self.cmd("Emulation.setDeviceMetricsOverride", width=width, height=1000,
                       deviceScaleFactor=2, mobile=width < 500)
        m = await self.cmd("Page.getLayoutMetrics")
        h = int(m["cssContentSize"]["height"]) if full else 900
        await self.cmd("Emulation.setDeviceMetricsOverride", width=width, height=max(h, 400),
                       deviceScaleFactor=2, mobile=width < 500)
        await asyncio.sleep(0.4)
        r = await self.cmd("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        path.write_bytes(base64.b64decode(r["data"]))
        await self.cmd("Emulation.clearDeviceMetricsOverride")

    async def close(self) -> None:
        try:
            _http(f"/json/close/{self.target}")
        except Exception:
            pass
        if self.ws:
            await self.ws.close()


# ---------------------------------------------------------------- 动作封装
async def wait_ready(pg: Page, marker: str = "", tries: int = 40) -> str:
    """等页面稳定（表单提交会整页刷新，刷新途中会短暂报错）。"""
    for _ in range(tries):
        await asyncio.sleep(0.25)
        try:
            ok = await pg.js("document.readyState === 'complete'")
            has = await pg.js(f"!!document.querySelector({json.dumps(marker)})") if marker else True
            if ok and has:
                break
        except Exception:                                                   # noqa: BLE001
            continue
    await asyncio.sleep(0.3)
    try:                                                                    # 跳转后覆盖会丢，重新注入
        await pg.js("window.confirm = () => true")
    except Exception:                                                       # noqa: BLE001
        pass
    return await pg.js("document.body.innerText") or ""


async def center(pg: Page, sel: str) -> dict | None:
    return await pg.js(f"""(() => {{
      const e = document.querySelector({json.dumps(sel)});
      if (!e) return null;
      e.scrollIntoView({{block: 'center'}});
      const r = e.getBoundingClientRect();
      return {{x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2)}};
    }})()""")


async def click(pg: Page, sel: str, marker: str = "") -> tuple[bool, str]:
    """真鼠标点击（按下 + 松开）。"""
    p = await center(pg, sel)
    if not p:
        return False, f"找不到元素：{sel}"
    await pg.safe("Input.dispatchMouseEvent", type="mouseMoved", x=p["x"], y=p["y"])
    await pg.safe("Input.dispatchMouseEvent", type="mousePressed", x=p["x"], y=p["y"],
                  button="left", clickCount=1)
    await pg.safe("Input.dispatchMouseEvent", type="mouseReleased", x=p["x"], y=p["y"],
                  button="left", clickCount=1)
    return True, await wait_ready(pg, marker)


async def drag(pg: Page, src: str, dst: str, payload: str) -> tuple[bool, str]:
    """真拖放：走 Chrome 的拖放管线（dragEnter → dragOver → drop）。

    实测坑：CDP 合成的鼠标事件（mousePressed + mouseMoved）**不会**触发 HTML5 `dragstart`，
    所以「按住拖」这条路测不出真实拖拽；要用 `Input.setInterceptDrags` + `Input.dispatchDragEvent`，
    它走的就是浏览器真正的拖放链路（目标会高亮、drop 会带上 dataTransfer）。
    """
    a = await center(pg, src)
    b = await center(pg, dst)
    if not a or not b:
        return False, f"找不到拖拽两端：{src} / {dst}"
    data = {"items": [{"mimeType": "text/plain", "data": payload}], "dragOperationsMask": 1}
    await pg.safe("Input.setInterceptDrags", enabled=True)
    await pg.safe("Input.dispatchDragEvent", type="dragEnter", x=b["x"], y=b["y"], data=data)
    await pg.safe("Input.dispatchDragEvent", type="dragOver", x=b["x"], y=b["y"], data=data)
    await asyncio.sleep(0.3)
    hot = await pg.js("document.querySelectorAll('.dropok').length")
    await pg.safe("Input.dispatchDragEvent", type="drop", x=b["x"], y=b["y"], data=data)
    await pg.safe("Input.setInterceptDrags", enabled=False)
    body = await wait_ready(pg)
    return bool(hot), body


async def layout(pg: Page, width: int = 1440) -> dict:
    """排版体检：按钮间距 / 被截断的文本 / 整页能不能横向晃 / 页面多高。"""
    await pg.cmd("Emulation.setDeviceMetricsOverride", width=width, height=900,
                 deviceScaleFactor=2, mobile=width < 500)
    await asyncio.sleep(0.5)
    r = await pg.js("""(() => {
      const gaps = [];
      document.querySelectorAll(".actsrow, .bar, .flev, td.acts, form.inline").forEach(g => {
        const els = [...g.querySelectorAll("a,button,select,input[type=submit]")].filter(e => e.offsetParent);
        for (let i = 1; i < els.length; i++) {
          const a = els[i-1].getBoundingClientRect(), b = els[i].getBoundingClientRect();
          // 同一行才比间距；负数=换到下一行（inline 折叠块会让 top 看起来一样），不算「挤」
          if (Math.abs(a.top - b.top) < Math.max(a.height, b.height)) {
            const g = Math.round((b.left - a.right) * 10) / 10;
            if (g >= 0) gaps.push(g);
          }
        }
      });
      const clip = [...document.querySelectorAll("td,th,span.hint,summary,label")]
        .filter(e => e.scrollWidth > e.clientWidth + 6 && e.clientWidth > 0 && getComputedStyle(e).overflowX !== "auto")
        .slice(0, 6).map(e => (e.className || e.tagName) + ":" + e.scrollWidth + ">" + e.clientWidth);
      return {按钮对: gaps.length, 最小间距: gaps.length ? Math.min(...gaps) : null,
              挤在一起的: gaps.filter(g => g < 6).length,
              页面能横向晃: document.documentElement.scrollWidth > window.innerWidth + 1,
              被截断的: clip,
              页面高: Math.round(document.body.scrollHeight),
              屏数: Math.round(document.body.scrollHeight / window.innerHeight * 10) / 10};
    })()""")
    await pg.cmd("Emulation.clearDeviceMetricsOverride")
    return r or {}


# ---------------------------------------------------------------- 地址与清理
def auto_urls(window: str) -> tuple[str, str]:
    env = {"LIGHTHOUSE_PY": sys.executable}
    def run(*args: str) -> str:
        r = subprocess.run(["bash", str(REPO / "lighthouse.sh"), *args],
                           capture_output=True, text=True, cwd=str(REPO), env={**__import__("os").environ, **env})
        return (r.stdout + r.stderr).strip()
    admin = ""
    for line in run("kb", "admin-url", window).splitlines():
        if "http" in line:
            admin = line.split()[-1] if line.split() else ""
    users = json.loads((Path.home() / ".lighthouse/state/kb-users.json").read_text(encoding="utf-8"))[window]
    token = next((v["token"] for v in users.values() if isinstance(v, dict) and v.get("token")), "")
    base = admin.split("/w-")[0]
    return admin, f"{base}{admin[admin.index('/w-'):].split('/admin')[0]}/files?t={token}" if admin else ""


def _cli(window: str, *args: str) -> str:
    """`kb <子命令> <窗口> ...`（子命令在前，窗口紧跟其后）。"""
    r = subprocess.run(["bash", str(REPO / "lighthouse.sh"), "kb", args[0], window, *args[1:]],
                       capture_output=True, text=True, cwd=str(REPO))
    return (r.stdout + r.stderr).strip()


def cleanup(window: str) -> str:
    """把测试期间动过的资料挪回原位、删掉临时文件夹（含上一次被中断留下的残留）。

    匹配用前缀「（临时）交互测试」而不是本轮的名字 —— 中途 Ctrl-C 掉的那一轮不会执行收尾，
    下一轮开跑时（main 里开头也会调一次）必须顺手把它清掉，否则真库里会越积越多。
    """
    out = []
    cat_file = Path.home() / f".lighthouse/kb/{window}/catalog.json"
    if not cat_file.is_file():
        return ""
    cat = json.loads(cat_file.read_text(encoding="utf-8"))
    for did, e in list((cat.get("docs") or {}).items()):
        if STALE in (e.get("path") or ""):
            out.append(_cli(window, "mv", did, "--to", "")[-80:])          # 先挪回根目录
    folders = sorted({d.split("/")[0] for d in (cat.get("folders") or {}) if STALE in d})
    for f in folders:
        out.append(_cli(window, "flevel", f, "--clear")[-60:])
    for d in sorted({p for p in _all_dirs(window) if STALE in p}, reverse=True):
        out.append(_cli(window, "rmdir", d)[-60:])
    for t in _trash_names(window):
        if STALE in t:
            out.append(_cli(window, "trash", "--purge", t)[-60:])
    # 台账里没有的空文件夹也要清：建完还没放东西就被中断的那次，只会留在磁盘上
    import shutil
    lib = Path.home() / "demo" / "招投标文档库" / "原始文档"
    for d in sorted(lib.glob(STALE + "*"), reverse=True):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            out.append(f"清掉残留文件夹 {d.name}")
    return " ｜ ".join(x for x in out if x)


def _all_dirs(window: str) -> list[str]:
    cat = json.loads((Path.home() / f".lighthouse/kb/{window}/catalog.json").read_text(encoding="utf-8"))
    docs = {e.get("path", "").split("/")[1] for e in (cat.get("docs") or {}).values()
            if len(e.get("path", "").split("/")) > 2}
    return sorted(docs | set(cat.get("folders") or {}))


def _trash_names(window: str) -> list[str]:
    cat = json.loads((Path.home() / f".lighthouse/kb/{window}/catalog.json").read_text(encoding="utf-8"))
    return sorted({e.get("trash") for e in (cat.get("docs") or {}).values() if e.get("trash")})


def _tmp_name() -> str:
    return TMP_DIR


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-url", default="")
    ap.add_argument("--files-url", default="")
    ap.add_argument("--window", default="bidkb")
    ap.add_argument("--shots", default="/tmp/lighthouse-browser-shots")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    admin_url, files_url = a.admin_url, a.files_url
    if not (admin_url and files_url):
        admin_url, files_url = auto_urls(a.window)
    if not admin_url:
        print("❌ 拿不到地址（用 --admin-url / --files-url 指定）")
        return 2
    out = Path(a.shots)
    out.mkdir(parents=True, exist_ok=True)
    tmp_dir = _tmp_name()
    if not a.keep:                                    # 开跑前先把上一轮残留清掉
        cleanup(a.window)
    try:
        _http("/json/version")
    except Exception as e:                                                  # noqa: BLE001
        print(f"⚠️  连不上 Chrome CDP（{PORT}）：{e}\n"
              "   先启动：'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' "
              "--remote-debugging-port=9222 --user-data-dir=$HOME/chrome-cdp '--remote-allow-origins=*'")
        return 3

    pg = Page()
    shots: list[str] = []

    async def snap(name: str, width: int = 1440, full: bool = True) -> None:
        p = out / f"{len(shots) + 1:02d}-{name}.png"
        await pg.shot(p, width=width, full=full)
        shots.append(str(p))
        print(f"     📸 {p.name}")

    await pg.open(admin_url)
    body = ""
    for _ in range(12):                                       # 首次加载偶发空 body，读到有内容为止
        body = await wait_ready(pg, "tr.docrow")
        if len(body) > 200:
            break
    await pg.cmd("Page.enable")
    await pg.cmd("Runtime.evaluate", expression="window.confirm = () => true")   # 确认框一律确认

    print("\n【1】管理页初始状态")
    check("打开就是管理页（标题对）", "资料库管理页" in body, body[:40])
    has_new = await pg.js("""!!document.querySelector('input[name="dir"]')""")
    check("「文件夹」区与新建入口都在", "文件夹" in body and has_new, f"新建输入框={has_new}")
    _more = await pg.js("""(() => {
        const d = document.querySelector('tr.docrow details.rowmore');
        if (!d) return null;
        const btns = [...d.querySelectorAll('button')].map(b => b.textContent.trim());
        return {开着的: d.open, 里面: btns};
    })()""")
    check("资料行带「⋯」里的改标题 / 进回收站（默认收着，DOM 里在）",
          bool(_more) and _more["开着的"] is False and "改标题" in _more["里面"]
          and "进回收站" in _more["里面"], str(_more))
    check("回收站区默认不占地方（空的就不显示）", "回收站（" not in body)
    await snap("管理页-初始")
    lay = await layout(pg)
    check(f"按钮不挤（{lay.get('按钮对')} 对，最小 {lay.get('最小间距')}px）",
          lay.get("挤在一起的") == 0, json.dumps(lay, ensure_ascii=False))
    check("桌面宽度整页不横向晃", not lay.get("页面能横向晃"), str(lay.get("被截断的")))
    check(f"首屏信息量：整页 {lay.get('屏数')} 屏（超过 12 屏就该折叠了）",
          (lay.get("屏数") or 0) <= 12, str(lay.get("页面高")))

    print("\n【2】真鼠标：新建文件夹")
    _fill = json.dumps(tmp_dir)
    await pg.js(f"""(() => {{ const i = document.querySelector('input[name="dir"]');
                   i.value = {_fill}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")
    ok, body = await click(pg, 'form[action$="/admin/mkdir"] button', ".ok, .warn")
    check("点「新建文件夹」→ 回执说建好了", ok and "已新建文件夹" in body, body[:80])
    await snap("新建文件夹-回执")

    print("\n【3】真拖放：把资料拖进文件夹")
    ok, body = await click(pg, 'a.crumb[data-drop=""]', "tr.docrow")
    rows = await pg.js("""[...document.querySelectorAll('tr.docrow')].map(r => r.dataset.did)""")
    check("点面包屑「全部资料」能回根目录（看得到资料行）", ok and bool(rows), str(rows)[:70])
    if rows:
        before = len(rows)
        hot, body = await drag(pg, "tr.docrow td.grab", f'tr.frow[data-drop="{tmp_dir}"]', rows[0])
        after = await pg.js("document.querySelectorAll('tr.docrow').length")
        check("拖到文件夹行上会高亮（拖放链路真的通了）", hot, body[:60])
        check("松手后资料挪走了（根目录少一篇）",
              (after == before - 1) or ("已把 1 篇挪到" in body), f"{before} → {after}｜{body[:60]}")
        await snap("拖拽后-目标文件夹")
        check("拖完自动停在目标文件夹里，能看到刚挪进来的那一篇",
              tmp_dir in body and "已把 1 篇挪到" in body, body[:90])

    print("\n【4】真鼠标：给文件夹设默认等级")
    ok, body = await click(pg, 'a.crumb[data-drop=""]', "tr.frow")
    check("点面包屑回根目录（看到文件夹行）", ok and tmp_dir in body, body[:70])
    _t = json.dumps(tmp_dir)
    found = await pg.js(f"""(() => {{
        const f = [...document.querySelectorAll('form[action$="/admin/flevel"]')]
                    .find(x => x.querySelector('input[name=folder]').value === {_t});
        if (!f) return false;
        const s = f.querySelector('select');
        s.value = [...s.options].map(o => o.value).filter(Boolean).pop();     // 挑最后一档，容易看出变没变
        s.dispatchEvent(new Event('change', {{bubbles: true}}));
        f.dataset.test = '1'; return true; }})()""")
    check("文件夹行上有「默认等级」下拉", bool(found))
    ok, body = await click(pg, 'form[action$="/admin/flevel"][data-test="1"] button', ".ok, .warn")
    check("点「存默认等级」→ 回执说清了「只影响以后新进来的」",
          ok and f"文件夹「{tmp_dir}」的默认等级" in body and "以后" in body, body[:110])
    await snap("文件夹-默认等级已存")

    print("\n【4】真鼠标：给文件夹设默认等级")
    _t = json.dumps(tmp_dir)
    await pg.js(f"""(() => {{
        const f = [...document.querySelectorAll('form[action$="/admin/flevel"]')]
                    .find(x => x.querySelector('input[name=folder]').value === {_t});
        const s = f.querySelector('select');
        s.value = [...s.options].map(o => o.value).filter(Boolean).pop();     // 挑最后一档，容易看出变没变
        s.dispatchEvent(new Event('change', {{bubbles: true}}));
        f.dataset.test = '1'; }})()""")
    await click(pg, 'form[action$="/admin/flevel"][data-test="1"] button', ".ok, .warn")
    check("点「存默认等级」→ 回执说清了「只影响以后新进来的」",
          f"文件夹「{tmp_dir}」的默认等级" in body and "以后" in body, body[:110])
    await snap("文件夹-默认等级已存")

    print("\n【5】真鼠标：展开行内「⋯」→ 进回收站 → 放回")
    ok, body = await click(pg, 'tr.docrow details.rowmore > summary', "details.rowmore[open]")
    check("点行内「⋯」能展开（改标题 / 进回收站藏在里面）",
          ok and "进回收站" in body, body[:70])
    await snap("行内-更多操作已展开")
    ok, body = await click(pg, 'details.rowmore[open] button[name="one"][value$="@trash"]', "body")
    check("点「进回收站」→ 回收站区出现这一项", ok and "回收站（" in body, body[:90])
    await snap("回收站-有东西")
    ok, body = await click(pg, 'form[action$="/admin/trash"] button[value="restore"]', ".ok, .warn")
    check("点「放回」→ 回到原位置、回收站区消失", ok and "回收站（" not in body, body[:90])
    await snap("回收站-已放回")

    print("\n【6】同事资料页（按文件夹分组）")
    await pg.open(files_url)
    body = await wait_ready(pg, "details")
    check("同事页按文件夹分组（有 📁 分组标题）", "📁" in body and "篇" in body, body[:70])
    check("每个分组有「打包下载这个文件夹」", "打包下载这个文件夹" in body)
    await snap("同事页-按文件夹分组")
    lay2 = await layout(pg)
    check(f"同事页按钮不挤（最小 {lay2.get('最小间距')}px）", lay2.get("挤在一起的") == 0,
          json.dumps(lay2, ensure_ascii=False))
    lay3 = await layout(pg, 390)
    check("同事页手机宽度（390px）整页不横向晃", not lay3.get("页面能横向晃"),
          json.dumps(lay3, ensure_ascii=False))
    await snap("同事页-手机", width=390, full=False)

    print("\n【7】管理页手机宽度")
    await pg.open(admin_url)
    await wait_ready(pg, "table")
    lay4 = await layout(pg, 390)
    check("管理页手机宽度整页不横向晃", not lay4.get("页面能横向晃"), json.dumps(lay4, ensure_ascii=False))
    check("手机宽度下按钮也不挤", lay4.get("挤在一起的") == 0, str(lay4.get("最小间距")))
    await snap("管理页-手机", width=390, full=False)
    await pg.close()

    if not a.keep:
        print(f"\n🧹 清理：{cleanup(a.window) or '（没有要清的）'}")

    bad = [x for x in results if not x[1]]
    print(f"\n════════ 浏览器交互验收：{len(results) - len(bad)}/{len(results)} 通过 ════════")
    for x in bad:
        print(f"  ❌ {x[0]} — {x[2][:140]}")
    print("截图：" + "、".join(Path(s).name for s in shots))
    print(f"目录：{out}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
