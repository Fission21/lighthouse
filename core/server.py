#!/usr/bin/env python3
"""window —— 通用「受控窗口」MCP 管

一扇窗 = 一份**明确声明**的给看范围。给外部（网页版 GPT / 任何 agent）看的每样东西
都必须先在这里登记，出了范围一律拒绝，而且每次调用都有账。

声明项（windows.json 里每个窗口）：
  root            只允许看这棵目录树（realpath 越界一律拒）
  include         只允许看匹配这些 glob 的文件（默认 ["**/*"]）
  exclude         这些 glob 不看（目录命中会连子树一起排除）
  deny_extra      额外拉黑规则（正则，匹配相对路径或文件名）
  tools           开放哪些工具（默认四个全开）
  max_file_kb     单文件字节上限
  max_output_chars 单次返回字符上限
  visibility      public / local（local 表示私密，做对外暴露时要剔除）
  port / path     监听端口与端点路径（路径里带随机段当弱口令）

内置安全（不可关）：
  · 默认拉黑：.env*、*.pem、*.key、id_rsa*、credentials*/secrets*/tokens*、
    .ssh/、.aws/、.gnupg/、*.sqlite/*.db、kdbx、keystore 等
  · 输出脱敏：sk-…、ghp_…、AIza…、AKIA…、Bearer …、私钥块、密码/密钥赋值 → «REDACTED»
  · 审计日志：<状态目录>/audit/<window>.jsonl（每次调用一行，默认 ~/.lighthouse）

启动：WINDOW_ID=<id> python server.py
   环境覆盖（测试用）：WINDOW_PORT / WINDOW_PATH / WINDOW_REGISTRY
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

HERE = Path(__file__).resolve().parent
def _default_registry() -> Path:
    """优先 windows.local.json（本机私有），没有就用仓库里的 windows.json。"""
    for name in ("windows.local.json", "windows.json"):
        cand = HERE.parent / name
        if cand.exists():
            return cand
    return HERE.parent / "windows.json"


REGISTRY_PATH = Path(os.path.expanduser(os.environ.get("WINDOW_REGISTRY") or str(_default_registry())))

def _state_root() -> Path:
    """状态目录：环境变量 LIGHTHOUSE_STATE > 配置文件 state_dir > ~/.lighthouse。"""
    env = os.environ.get("LIGHTHOUSE_STATE")
    if env:
        return Path(os.path.expanduser(env))
    for name in ("config.local.json", "config.json"):
        try:
            cfg = json.loads((HERE.parent / name).read_text(encoding="utf-8"))
            if cfg.get("state_dir"):
                return Path(os.path.expanduser(cfg["state_dir"]))
        except (OSError, json.JSONDecodeError):
            continue
    return Path(os.path.expanduser("~/.lighthouse"))

STATE_ROOT = _state_root()
AUDIT_DIR = STATE_ROOT / "audit"
BACKUP_DIR = STATE_ROOT / "backups"
SWITCH_PATH = STATE_ROOT / "state" / "window-write.json"
CLI_HINT = os.environ.get("LIGHTHOUSE_CLI", "bash lighthouse.sh")

import sys as _sys
_sys.path.insert(0, str(HERE))
import scope as SCOPE  # noqa: E402  （范围授权：grant / pending / arm / ceiling）
import config as CONF  # noqa: E402  （窗口提权策略：auto_grant / elevation_ceiling）
CST = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 默认拉黑（路径级）
DENY_PATTERNS = [
    # ⚠️ 一律大小写不敏感编译（见下方 Window.deny 的 IGNORECASE）。
    #    在 macOS/Windows 这类大小写不敏感的文件系统上，`docs/.ENV` 与 `docs/.env` 是同一个文件，
    #    大小写敏感的规则会被「一个字母换大小写」直接绕过 —— 这是实测出来的洞。
    r"(^|/)[^/]*\.env(\.[A-Za-z0-9_-]+)?$",                       # .env / .env.local / config.env / prod.env
    r"(^|/)[^/]*\.(pem|key|p12|pfx|ppk|kdbx|keystore|jks)$",      # 私钥 / 证书 / 密钥库
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\..*)?$",                   # SSH 私钥
    r"(^|/)[^/]*(secrets?|credentials?|passwords?|passwd|apikeys?|api[_-]keys?|tokens?)(\.[A-Za-z0-9]+)?$",
    r"(^|/)\.(ssh|aws|gnupg|docker|kube)(/|$)",                   # 凭据目录
    r"(^|/)\.(netrc|npmrc|pgpass|htpasswd|my\.cnf)$",
    r"(^|/)[^/]*\.(sqlite3?|db|mdb|bak|license|licence)$",
    r"(^|/)\.git(/|$)",                                           # 版本库内部：config/logs/objects 可能藏着历史里的密钥
]

# ---------------------------------------------------------------- 脱敏（内容级）
REDACT_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "api-key"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{20,}"), "github-token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github-pat"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "google-key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "slack-token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-key-id"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "aws-temp-key-id"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{20,}"), "bearer-token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "private-key"),
]


def _redact(text: str) -> tuple[str, int]:
    """把明显的凭据形态打码，返回 (处理后文本, 打码次数)。"""
    n = 0
    for pat, label in REDACT_RULES:
        text, k = pat.subn(f"«REDACTED:{label}»", text)
        n += k
    # 赋值式：保留键名，掩掉值（值已被掩过的不再重复处理）
    def _mask_assign(m: re.Match) -> str:
        return f"{m.group(1)}«REDACTED:value»"

    text, k = re.subn(
        r"(?i)\b((?:api[_-]?key|secret|password|passwd|token|access[_-]?key|private[_-]?key)\b\s*[=:]\s*[\"']?)(?!«REDACTED)([^\s\"',;]{6,})",
        _mask_assign,
        text,
    )
    n += k
    return text, n


def _glob_to_re(pat: str) -> re.Pattern:
    out, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        # '/**' 表示「这个目录本身 + 它的整棵子树」。
        # ⚠️ 少了这一条，`include: ["src/**"]` 会把 `list_files("src")` 判成越界——
        #    而「列出某个子目录」是最基本的操作（实测被 ChatGPT 当场撞到，报「不匹配 include」）。
        if c == "/" and pat[i + 1:i + 3] == "**":
            out.append("(?:/.*)?")
            i += 3
            if i < n and pat[i] == "/":
                i += 1
            continue
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # '**/' → 可选目录前缀（这样 '**/*' 也能匹配根级文件）；'**' 单独出现 → 任意
                if i + 2 < n and pat[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    # IGNORECASE 是安全必需，不是便利：文件系统大小写不敏感时（macOS/Windows），
    # 字符串比较必须与文件系统语义一致，否则 `PRIVATE/x` 能绕过 exclude `private/**`。
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


# ---------------------------------------------------------------- 写开关
def read_switches() -> dict:
    try:
        with open(SWITCH_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


class Window:
    """一扇窗的配置与范围判定。"""

    def __init__(self, wid: str, cfg: dict):
        self.id = wid
        self.cfg = cfg
        self.title = cfg.get("title", wid)
        _root = Path(os.path.expanduser(cfg["root"]))
        if not _root.is_absolute():          # 相对路径按注册表所在目录解析（方便整个仓库搬走）
            _root = REGISTRY_PATH.parent / _root
        self.root = _root.resolve()
        self.include = [_glob_to_re(p) for p in cfg.get("include", ["**/*"])]
        self.exclude = [_glob_to_re(p) for p in cfg.get("exclude", [])]
        # 与文件系统语义对齐：大小写不敏感匹配（否则 .ENV 能绕过 .env 的拉黑）
        self.deny = [re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS + cfg.get("deny_extra", [])]
        self.max_file_kb = int(cfg.get("max_file_kb", 512))
        self.max_output_chars = int(cfg.get("max_output_chars", 60000))
        self.port = int(os.environ.get("WINDOW_PORT") or cfg.get("port", 8990))
        self.path = os.environ.get("WINDOW_PATH") or cfg.get("path", "/mcp")
        if not self.path.startswith("/"):
            self.path = "/" + self.path
        self.visibility = cfg.get("visibility", "local")
        self.audit = AUDIT_DIR / f"{self.id}.jsonl"

    # ---- 判定 ----
    def check(self, rel: str) -> tuple[bool, str]:
        """rel 为 posix 相对路径；返回 (是否放行, 拒绝原因)。"""
        rel = (rel or "").lstrip("/")
        if any(ord(c) < 32 for c in rel):
            return False, "非法路径（含控制字符/空字节）"
        if len(rel) > 1024:
            return False, "非法路径（过长）"
        if not rel:
            return True, ""
        for pat in self.deny:
            if pat.search(rel):
                return False, "命中安全拉黑规则（密钥/凭据/数据库类）"
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            if any(p.match(sub) for p in self.exclude):
                return False, "不在给看范围（被 exclude 排除）"
        if not any(p.match(rel) for p in self.effective_include()):
            return False, "不在给看范围（不匹配 include）"
        return True, ""

    def pruned(self, rel: str) -> bool:
        """这个目录是否被**明确排除**（exclude / 默认拉黑）。

        ⚠️ 只看「不该看」，**不看 include**。原因：按类型给的 include（`**/*.py`、`*.md`）
        永远不匹配目录名，若用它剪枝，整棵目录树会在列举时消失 ——
        agent 连「有哪些文件可读」都发现不了。文件能不能读，由 yield 前的 `check()` 决定。
        """
        rel = (rel or "").lstrip("/")
        if not rel:
            return False
        for pat in self.deny:
            if pat.search(rel):
                return True
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            if any(p.match("/".join(parts[:i])) for p in self.exclude):
                return True
        return False

    def has_visible_under(self, rel: str) -> bool:
        """`rel` 这棵子树里有没有**任何**能给看的文件（只看有没有，不返回内容）。"""
        target, _ = self.resolve(rel)
        if target is None or not target.is_dir():
            return False
        for _ in _iter_files(target, 8):
            return True
        return False

    def effective_include(self) -> list[re.Pattern]:
        """注册表 include + 用户已授予的额外范围（grant）。exclude 与默认拉黑不受影响。"""
        pats = list(self.include)
        for extra in SCOPE.grant_include(self.id):
            try:
                pats.append(_glob_to_re(extra))
            except Exception:  # noqa: BLE001 —— 坏规则就当没授过
                continue
        return pats

    def resolve(self, rel: str) -> tuple[Path | None, str]:
        try:
            target = (self.root / (rel or ".")).resolve()
        except (OSError, RuntimeError, ValueError) as e:  # 符号链接环 / 空字节 / 非法字符
            return None, f"路径解析失败（{e.__class__.__name__}）"
        if target != self.root and self.root not in target.parents:
            return None, "路径越界（窗口外一律拒）"
        return target, ""

    def policy(self) -> tuple[bool, list[str]]:
        """本窗口的提权策略（实时读注册表；读不到退回启动时的快照 —— 两处都是 fail-closed）。"""
        return CONF.auto_grant_policy(_live_cfg() or self.cfg)

    def auto_grant_ttl(self) -> int | None:
        """常驻策略里「自动授予的时长」（分钟）；None = 无期限。"""
        return CONF.window_auto_grant_ttl(_live_cfg() or self.cfg)

    def scope_summary(self) -> dict:
        wcfg = self.cfg.get("write", {}) or {}
        master = bool(wcfg.get("enabled", False))
        sw = read_switches().get(self.id, {}) or {}
        until = sw.get("until")
        _auto, _ceiling = self.policy()
        _ttl = self.auto_grant_ttl() if _auto else None
        live = master and bool(sw.get("enabled")) and not (until and time.time() > float(until))
        return {
            "window": self.id,
            "title": self.title,
            "root": str(self.root),
            "include": self.cfg.get("include", ["**/*"]),
            "exclude": self.cfg.get("exclude", []),
            "denied_by_default": ["密钥/凭据/数据库/SSH 等"],
            "max_file_kb": self.max_file_kb,
            "visibility": self.visibility,
            "include_effective": list(self.cfg.get("include", ["**/*"])) + SCOPE.grant_include(self.id),
            "scope_elevation": {
                **SCOPE.summary(self.id),
                "auto_grant": _auto,
                "auto_grant_ceiling": ((_ceiling or "不限（任何范围申请都会自动生效）") if _auto else None),
                "auto_grant_ttl": (SCOPE.describe_duration(_ttl) if _auto else None),
                "chat_approval": CONF.window_chat_approval(_live_cfg() or self.cfg),
                "how_to_ask": (
                    "需要看更多时，让 agent 调 request_access(reason, include) 提出申请——"
                    "它只能申请，批准权在用户手里。用户在部署机器上批准：`lighthouse.sh approve <窗口>`；"
                    "或先开一个预授权窗口：`lighthouse.sh elevate <窗口> 30 [--scope \"src/**\"]`；"
                    "或为该窗口声明常驻策略：`lighthouse.sh auto-grant <窗口> on [--ceiling \"src/**\"] [--ttl 2h]`"
                    "（开启后上限内的申请立即生效，不用再跑命令）。"
                    "授权时长由用户选：`--for 30m|2h|1d|7d|forever`（不写 = 无期限）。"
                    "注意：exclude 与密钥默认拉黑永远不受提权影响。"
                ),
            },
            "write": {
                "master_enabled": master,
                "switch_on": bool(sw.get("enabled")),
                "switch_until": (datetime.fromtimestamp(float(until), CST).strftime("%Y-%m-%d %H:%M") if until else None),
                "currently_writable": live,
                "how_to_toggle": f"在部署机器上执行: {CLI_HINT} write {self.id} on|off [分钟数]",
            },
        }

    # ---- 写权限（两级：配置总开关 + 运行时开关，默认全关，fail-closed） ----
    def write_allowed(self) -> tuple[bool, str]:
        wcfg = self.cfg.get("write", {}) or {}
        if not wcfg.get("enabled", False):
            return False, "本窗口未开放写权限（配置 write.enabled=false）"
        sw = read_switches().get(self.id, {}) or {}
        if not sw.get("enabled"):
            return False, (f"写开关当前关闭（只读模式）。需要修改时请执行："
                           f"{CLI_HINT} write {self.id} on")
        until = sw.get("until")
        if until and time.time() > float(until):
            return False, f"写开关已到期自动关闭（{datetime.fromtimestamp(float(until), CST):%Y-%m-%d %H:%M}）"
        return True, ""

    @property
    def max_write_kb(self) -> int:
        return int((self.cfg.get("write", {}) or {}).get("max_write_kb", 256))

    def backup(self, target: Path, rel: str) -> str | None:
        """改动前把原文件另存一份（可回滚）。"""
        if not (self.cfg.get("write", {}) or {}).get("backup", True):
            return None
        if not target.is_file():
            return None
        stamp = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
        dest_dir = BACKUP_DIR / self.id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{stamp}-{rel.replace('/', '__')}"
        try:
            shutil.copy2(target, dest)
            return str(dest)
        except OSError:
            return None


def load_registry() -> dict:
    with open(REGISTRY_PATH, encoding="utf-8") as fh:
        return json.load(fh).get("windows", {})


WINDOW_ID = os.environ.get("WINDOW_ID", "")
_registry = load_registry()
if WINDOW_ID not in _registry:
    raise SystemExit(f"WINDOW_ID={WINDOW_ID!r} 不在 {REGISTRY_PATH}；可用: {list(_registry)}")
WIN = Window(WINDOW_ID, _registry[WINDOW_ID])
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

server = MCPServer(
    name=f"window-{WIN.id}",
    instructions=(
        f"受控窗口「{WIN.title}」：访问一个被明确划定范围的本机目录。"
        f"根目录 {WIN.root}；范围外、密钥类文件一律拒绝；输出中的凭据会自动打码。"
        "默认只读（window_info / list_files / read_file / search）；"
        "write_file / edit_file / make_dir / delete_file 四个写工具只有在用户打开写开关后才会生效，"
        "关闭时一律拒绝——需要改动请先让用户打开开关。"
        "先调 window_info 了解范围与写开关状态。"
    ),
)


# ---------------------------------------------------------------- 基础
def _live_cfg() -> dict:
    """实时读注册表里本窗口的配置。

    用户改了 auto_grant / 上限要「立刻生效」，所以不缓存、不依赖服务启动时的快照——
    收紧策略（关掉自动授予）必须马上拦住后续申请。读不到一律当没开（fail-closed）。
    """
    try:
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["windows"]
        return reg.get(WIN.id, {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def _audit(tool: str, args: dict, ok: bool, extra: dict | None = None) -> None:
    try:
        line = {
            "ts": datetime.now(CST).isoformat(timespec="seconds"),
            "window": WIN.id,
            "tool": tool,
            "args": {k: (v if not isinstance(v, str) or len(v) < 120 else v[:120] + "…") for k, v in (args or {}).items()},
            "ok": ok,
        }
        if extra:
            line.update(extra)
        with open(WIN.audit, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _dump(obj) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    red, n = _redact(text)
    if n:
        red = red.rstrip()[:-1] + f',\n  "_redactions": {n}\n}}' if red.rstrip().endswith("}") else red
    if len(red) > WIN.max_output_chars:
        red = red[: WIN.max_output_chars] + "…（输出超限已截断）"
    return red


def _iter_files(base: Path, depth: int):
    base_depth = len(base.parts)
    for dirpath, dirnames, filenames in os.walk(base):
        d = Path(dirpath)
        if len(d.parts) - base_depth >= depth:
            dirnames[:] = []
        rel_dir = str(d.relative_to(WIN.root)) if d != WIN.root else ""
        keep = []
        for name in sorted(dirnames):
            if name.startswith(".") or name in {"node_modules", "__pycache__", ".venv", "venv", ".git"}:
                continue
            sub = f"{rel_dir}/{name}" if rel_dir else name
            # 只剪「明确不该看」的目录（exclude / 默认拉黑）；**不要用 include 剪枝** ——
            # 否则 `include: ["**/*.py"]` 这类按类型的配置会让整棵目录树在列举时消失。
            if not WIN.pruned(sub):
                keep.append(name)
        dirnames[:] = keep
        for f in sorted(filenames):
            rel = f"{rel_dir}/{f}" if rel_dir else f
            ok, _ = WIN.check(rel)
            if not ok:
                continue
            fp = d / f
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            yield rel, size


# ---------------------------------------------------------------- 工具
@server.tool(description="本窗口的给看范围声明：根目录、include/exclude、上限、可见性。")
def window_info() -> str:
    _audit("window_info", {}, True)
    return _dump({**WIN.scope_summary(),
                  "tools": ["window_info", "list_files", "read_file", "search", "request_access"]})


@server.tool(description="列出窗口范围内的文件（越界/拉黑文件不会出现）。path 为窗口内相对路径，depth 默认 3。")
def list_files(path: str = "", depth: int = 3) -> str:
    ok, why = WIN.check(path)
    if not ok:
        # 目录本身不匹配 include ≠ 不能列：只要它**下面**有能给看的文件就算数。
        # 否则 `include: ["**/*.py"]` 这类「按类型给看」的配置下，agent 永远发现不了文件。
        # （只判「有没有」，不返回内容；真正列出来的每一项仍会逐个过闸。）
        if not WIN.has_visible_under(path):
            _audit("list_files", {"path": path}, False, {"reason": why})
            return _dump({"error": why, "window": WIN.id})
    target, err = WIN.resolve(path)
    if err or target is None:
        _audit("list_files", {"path": path}, False, {"reason": err})
        return _dump({"error": err, "window": WIN.id})
    if not target.is_dir():
        _audit("list_files", {"path": path}, False, {"reason": "不是目录"})
        return _dump({"error": f"不是目录: {path}"})
    files = [{"path": rel, "bytes": size} for rel, size in _iter_files(target, max(1, min(int(depth or 3), 8)))]
    _audit("list_files", {"path": path, "depth": depth}, True, {"files": len(files)})
    return _dump({"window": WIN.id, "path": path or ".", "count": len(files), "files": files})


@server.tool(description="读窗口内某文件的行区间（1 起，带行号）。范围外/密钥类文件会被拒；内容里的凭据自动打码。")
def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    ok, why = WIN.check(path)
    if not ok:
        _audit("read_file", {"path": path}, False, {"reason": why})
        return _dump({"error": why, "window": WIN.id})
    target, err = WIN.resolve(path)
    if err or target is None:
        _audit("read_file", {"path": path}, False, {"reason": err})
        return _dump({"error": err, "window": WIN.id})
    if not target.is_file():
        _audit("read_file", {"path": path}, False, {"reason": "文件不存在"})
        return _dump({"error": f"文件不存在: {path}"})
    size = target.stat().st_size
    if size > WIN.max_file_kb * 1024:
        _audit("read_file", {"path": path}, False, {"reason": "文件过大"})
        return _dump({"error": f"文件过大（{size}B > {WIN.max_file_kb}KB）"})
    raw = target.read_bytes()
    if b"\x00" in raw[:8192]:
        _audit("read_file", {"path": path}, False, {"reason": "二进制"})
        return _dump({"error": "二进制文件"})
    lines = raw.decode("utf-8", errors="replace").splitlines()
    offset = max(1, int(offset or 1))
    limit = max(1, min(int(limit or 200), 500))
    chunk = lines[offset - 1: offset - 1 + limit]
    body = "\n".join(f"{offset + i}|{ln}" for i, ln in enumerate(chunk))
    text, n = _redact(body)
    _audit("read_file", {"path": path, "offset": offset}, True, {"bytes": size, "redactions": n})
    return _dump({
        "window": WIN.id, "file": path, "total_lines": len(lines),
        "offset": offset, "returned": len(chunk),
        "next_offset": offset + len(chunk) if offset + len(chunk) <= len(lines) else None,
        "redactions": n,
        "content": text,
    })


@server.tool(description="在窗口范围内搜关键词（多关键词=全部命中）。范围外/拉黑文件不会被搜到，命中内容自动打码。")
def search(keyword: str, limit: int = 20) -> str:
    kws = [k for k in re.split(r"[,，\s]+", keyword or "") if k]
    if not kws:
        _audit("search", {"keyword": keyword}, False, {"reason": "空关键词"})
        return _dump({"error": "keyword 不能为空"})
    limit = max(1, min(int(limit or 20), 50))
    hits, scanned = [], 0
    for rel, size in _iter_files(WIN.root, 8):
        if size > WIN.max_file_kb * 1024:
            continue
        try:
            raw = (WIN.root / rel).read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        scanned += 1
        for ln, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            low = line.lower()
            if all(k.lower() in low for k in kws):
                text, _ = _redact(line.strip()[:200])
                hits.append({"file": rel, "line": ln, "text": text})
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    _audit("search", {"keyword": keyword}, True, {"hits": len(hits), "scanned": scanned})
    return _dump({"window": WIN.id, "keywords": kws, "hits": len(hits), "files_scanned": scanned, "results": hits})


# ---------------------------------------------------------------- 写工具（受开关控制）

# ---------------------------------------------------------------- 提权申请
BAD_PAT = re.compile(r"(^/|\.\.)")


def _validate_patterns(include: list[str]) -> tuple[list[str], str]:
    if not include:
        return [], "include 不能为空（给个 glob 列表，例如 [\"src/**\", \"*.py\"]）"
    if len(include) > 20:
        return [], "一次最多申请 20 条规则"
    clean = []
    for pat in include:
        if not isinstance(pat, str):
            return [], "include 必须是字符串列表"
        pat = pat.strip()
        if not pat or len(pat) > 200:
            return [], f"规则长度不合法: {pat[:30]!r}"
        if BAD_PAT.search(pat):
            return [], f"规则不能是绝对路径或包含 ..: {pat!r}"
        clean.append(pat)
    return clean, ""


@server.tool(description="【申请】申请扩大本窗口的给看范围（例如从「只能看文档」提到「也能看代码」）。"
                         "默认只会记成【待批准申请】——agent 无法自我提权，批准权在用户手里；"
                         "若用户为该窗口开了自动授予策略（或临时预授权窗口），则在授权上限内立即生效；"
                         "若用户在对话里已明确同意、且窗口开了「对话内授权」，带 user_confirmed=true 再次申请即生效。"
                         "密钥默认拉黑与 exclude 永远不受影响。")
def request_access(include: list[str], reason: str = "", user_confirmed: bool = False) -> str:
    clean, bad = _validate_patterns(include)
    if bad:
        _audit("request_access", {"include": include, "reason": reason}, False, {"reason": bad})
        return _dump({"error": bad, "window": WIN.id})

    ok, why = SCOPE.arm_allows(WIN.id, clean)
    arm = SCOPE.active_arm(WIN.id)
    if ok and arm:
        minutes = max(1, int((float(arm["until"]) - time.time()) // 60) or 1)
        g = SCOPE.set_grant(WIN.id, clean, minutes, note=f"预授权自动批准：{reason}"[:200], by="elevation-window")
        _audit("request_access", {"include": clean, "reason": reason}, True, {"granted": g["include"], "until": g["until"]})
        return _dump({
            "status": "granted",
            "window": WIN.id,
            "now_visible": clean,
            "until": g["until"],
            "message": "已在预授权窗口内批准。现在可以读这些范围了；到期自动收回。",
        })

    # 用户为这扇窗声明了「申请即授予」→ 上限内直接生效，不必再跑本地命令。
    # fail-closed：策略读不到 / 配置可疑 / 超出上限 → 一律落回待批，等用户亲自点头。
    auto, ceiling = CONF.auto_grant_policy(_live_cfg())
    if auto:
        ok_c, why_c = SCOPE.ceiling_allows(ceiling, clean)
        if ok_c:
            ttl = WIN.auto_grant_ttl()          # 用户选的授权时长（None = 无期限）
            g = SCOPE.set_grant(WIN.id, clean, ttl,
                                note=(f"按窗口授权策略自动授予（上限 {ceiling or '不限'}，"
                                      f"时长 {SCOPE.describe_duration(ttl)}）：{reason}")[:200],
                                by="auto-grant")
            _audit("request_access", {"include": clean, "reason": reason}, True,
                   {"granted": g["include"], "via": "auto-grant", "ceiling": ceiling or "不限",
                    "ttl_minutes": ttl})
            return _dump({
                "status": "granted",
                "via": "window-policy",
                "window": WIN.id,
                "now_visible": clean,
                "ceiling": ceiling or "不限",
                "duration": SCOPE.describe_duration(ttl),
                "until": g["until"],
                "message": (f"已按本窗口的授权策略直接生效，时长 {SCOPE.describe_duration(ttl)}"
                            + ("（到期自动收回）。" if ttl else "（无期限，直到被收回）。")
                            + "密钥默认拉黑与 exclude 照旧生效。"),
                "note": "用户已为该窗口开启自动授予；若这不是用户本意，用户可在部署机上改策略或 deny 立即收回。",
            })
        why = why_c

    # 对话内授权（用户声明制）：窗口开了 chat_approval，且用户在对话里明确同意后，
    # agent 带 user_confirmed=true 再次申请 → 立即生效（全程不用在部署机跑命令）。
    # ⚠️ 服务端验证不了「用户真说了」——它信任 agent 的转述；所以默认关、只该对本机/可信 agent 开。
    if user_confirmed:
        cfg_now = _live_cfg() or WIN.cfg
        if CONF.window_chat_approval(cfg_now):
            ceil_c = CONF.window_ceiling(cfg_now)
            ok_c, why_c = SCOPE.ceiling_allows(ceil_c, clean)
            if ok_c:
                ttl = WIN.auto_grant_ttl()
                g = SCOPE.set_grant(WIN.id, clean, ttl,
                                    note=(f"对话内授权（agent 转述用户已在对话中明确同意，时长 "
                                          f"{SCOPE.describe_duration(ttl)}）：{reason}")[:200],
                                    by="chat-approval")
                _audit("request_access", {"include": clean, "reason": reason, "user_confirmed": True}, True,
                       {"granted": g["include"], "via": "chat-approval", "ttl_minutes": ttl})
                return _dump({
                    "status": "granted",
                    "via": "chat-approval",
                    "window": WIN.id,
                    "now_visible": clean,
                    "ceiling": ceil_c or "不限",
                    "duration": SCOPE.describe_duration(ttl),
                    "until": g["until"],
                    "message": (f"已按「对话内授权」生效（用户在对话中已明确同意），时长 "
                                f"{SCOPE.describe_duration(ttl)}"
                                + ("（到期自动收回）。" if ttl else "（无期限，直到被收回）。")
                                + "密钥默认拉黑与 exclude 照旧生效。"),
                    "note": "该通道信任 agent 的转述；若不是用户本意，用户可 deny 立即收回，或关掉该窗口的 chat_approval。",
                })
            why = why_c
        else:
            why = "本窗口未开启「对话内授权」（user_confirmed 需要用户先在部署机开启该策略）"

    pending = SCOPE.set_pending(WIN.id, clean, reason)
    _audit("request_access", {"include": clean, "reason": reason}, True, {"pending": True, "note": why})
    chat_hint = ("\n（本窗口已开启「对话内授权」：用户若已在对话里明确同意，可再次调用 "
                 "request_access(include=..., reason=..., user_confirmed=true) 即时生效）"
                 if CONF.window_chat_approval(_live_cfg() or WIN.cfg) else "")
    return _dump({
        "status": "pending",
        "window": WIN.id,
        "requested": clean,
        "reason": reason,
        "user_confirmed_seen": bool(user_confirmed),
        "message": ("申请已记录，等用户批准。请把下面这句话原样转达给用户：\n"
                    f"「想看更多内容的话，在部署这台机器的终端里执行：{CLI_HINT} approve {WIN.id}」"
                    "（想一次给一段时间就加 `--for 2h`；可用 30m / 2h / 1d / 7d / forever，不写 = 无期限）"
                    + chat_hint
                    + (f"\n（预授权检查：{why}）" if arm is not None else "")),
        "note": ("本窗口虽已开自动授予，但这次申请超出了常驻上限 —— 需要用户亲自批准，不会自动生效。"
                 if auto else "agent 无法自我提权：没有用户的批准，这个申请不会改变任何可见范围。"),
    })


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _write_gate(tool: str, path: str) -> tuple[Path | None, str]:
    """写工具公共闸门：写开关 → 四道范围闸。任何一步不过就拒。"""
    ok, why = WIN.write_allowed()
    if not ok:
        _audit(tool, {"path": path}, False, {"reason": why, "mode": "read-only"})
        return None, why
    ok, why = WIN.check(path)
    if not ok:
        _audit(tool, {"path": path}, False, {"reason": why})
        return None, why
    target, err = WIN.resolve(path)
    if err or target is None:
        _audit(tool, {"path": path}, False, {"reason": err})
        return None, err
    if target == WIN.root:
        _audit(tool, {"path": path}, False, {"reason": "不能对窗口根目录本体操作"})
        return None, "不能对窗口根目录本体操作"
    return target, ""


@server.tool(description="【写】写入文件（mode=overwrite 覆盖 / append 追加）。仅在用户打开该窗口写开关后可用；写前自动备份原文件，写后记录前后哈希。")
def write_file(path: str, content: str, mode: str = "overwrite") -> str:
    if mode not in ("overwrite", "append"):
        return _dump({"error": f"mode 只能是 overwrite / append（收到 {mode!r}）", "window": WIN.id})
    target, err = _write_gate("write_file", path)
    if err or target is None:
        return _dump({"error": err, "window": WIN.id, "writable": False})
    data = content.encode("utf-8")
    if len(data) > WIN.max_write_kb * 1024:
        _audit("write_file", {"path": path}, False, {"reason": f"内容超限 {len(data)}B"})
        return _dump({"error": f"内容超限（{len(data)}B > {WIN.max_write_kb}KB）", "window": WIN.id})
    before = _sha(target)
    backup = WIN.backup(target, path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append" and target.exists():
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except OSError as e:
        _audit("write_file", {"path": path}, False, {"reason": f"写入失败 {e}"})
        return _dump({"error": f"写入失败: {e}", "window": WIN.id})
    after = _sha(target)
    _audit("write_file", {"path": path, "mode": mode}, True,
           {"bytes": len(data), "sha_before": before, "sha_after": after, "backup": backup})
    return _dump({"window": WIN.id, "ok": True, "file": path, "mode": mode,
                  "bytes": len(data), "sha_before": before, "sha_after": after, "backup": backup})


@server.tool(description="【写】对文件做精确替换（old_string→new_string）。默认要求唯一匹配；replace_all=true 才全部替换。受写开关控制，改动前自动备份。")
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    target, err = _write_gate("edit_file", path)
    if err or target is None:
        return _dump({"error": err, "window": WIN.id, "writable": False})
    if not target.is_file():
        _audit("edit_file", {"path": path}, False, {"reason": "文件不存在"})
        return _dump({"error": f"文件不存在: {path}", "window": WIN.id})
    if not old_string:
        return _dump({"error": "old_string 不能为空", "window": WIN.id})
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return _dump({"error": f"读取失败: {e}", "window": WIN.id})
    count = text.count(old_string)
    if count == 0:
        _audit("edit_file", {"path": path}, False, {"reason": "old_string 未匹配"})
        return _dump({"error": "old_string 在文件中未找到（没有做任何改动）", "window": WIN.id})
    if count > 1 and not replace_all:
        _audit("edit_file", {"path": path}, False, {"reason": f"匹配 {count} 处，需唯一"})
        return _dump({"error": f"old_string 匹配到 {count} 处；请提供更长上下文，或显式 replace_all=true", "window": WIN.id})
    new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
    if len(new_text.encode("utf-8")) > WIN.max_write_kb * 1024:
        return _dump({"error": f"结果超限（>{WIN.max_write_kb}KB）", "window": WIN.id})
    before = _sha(target)
    backup = WIN.backup(target, path)
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as e:
        _audit("edit_file", {"path": path}, False, {"reason": f"写入失败 {e}"})
        return _dump({"error": f"写入失败: {e}", "window": WIN.id})
    after = _sha(target)
    _audit("edit_file", {"path": path, "replace_all": replace_all}, True,
           {"replaced": count if replace_all else 1, "sha_before": before, "sha_after": after, "backup": backup})
    return _dump({"window": WIN.id, "ok": True, "file": path,
                  "replaced": count if replace_all else 1, "sha_before": before, "sha_after": after, "backup": backup})


@server.tool(description="【写】创建目录。受写开关控制。")
def make_dir(path: str) -> str:
    target, err = _write_gate("make_dir", path)
    if err or target is None:
        return _dump({"error": err, "window": WIN.id, "writable": False})
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _audit("make_dir", {"path": path}, False, {"reason": str(e)})
        return _dump({"error": f"创建失败: {e}", "window": WIN.id})
    _audit("make_dir", {"path": path}, True, {})
    return _dump({"window": WIN.id, "ok": True, "dir": path})


@server.tool(description="【写·危险】删除文件。必须先显式 confirm=true；删除前会把原文件备份到日志目录（可找回）。受写开关控制。")
def delete_file(path: str, confirm: bool = False) -> str:
    if not confirm:
        return _dump({"error": "危险操作：需要显式 confirm=true 才会删除", "window": WIN.id})
    target, err = _write_gate("delete_file", path)
    if err or target is None:
        return _dump({"error": err, "window": WIN.id, "writable": False})
    if not target.is_file():
        _audit("delete_file", {"path": path}, False, {"reason": "文件不存在"})
        return _dump({"error": f"文件不存在: {path}", "window": WIN.id})
    before = _sha(target)
    size = target.stat().st_size
    backup = WIN.backup(target, path)
    try:
        target.unlink()
    except OSError as e:
        _audit("delete_file", {"path": path}, False, {"reason": f"删除失败 {e}"})
        return _dump({"error": f"删除失败: {e}", "window": WIN.id})
    _audit("delete_file", {"path": path}, True, {"bytes": size, "sha_before": before, "backup": backup})
    return _dump({"window": WIN.id, "ok": True, "deleted": path, "bytes": size, "backup": backup})


if __name__ == "__main__":
    allowed = [h.strip() for h in os.environ.get("WINDOW_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = (TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed)
                if allowed else TransportSecuritySettings(enable_dns_rebinding_protection=False))
    print(f"[window:{WIN.id}] http://127.0.0.1:{WIN.port}{WIN.path} root={WIN.root} visibility={WIN.visibility}"
          f" | rebinding-protection={'on:' + ','.join(allowed) if allowed else 'off'}", flush=True)
    # stateless_http=True：每个请求用独立传输，服务端不跟踪会话。
    # 为什么：会话模式下，服务一重启，所有客户端手里的 mcp-session-id 就作废了，
    # 而不少客户端（如 WorkBuddy）不会自动重新握手、继续拿旧会话 id 调用 → 服务端回
    # “Session not found” → 对方以为「找不到 mcp 环境」，白白排查半天。
    # 本窗口的工具都是请求-应答式，不用服务端推送，也不需要会话状态 —— 无状态模式
    # 让「重启服务」对已连接的客户端完全无感（旧会话 id 直接被忽略）。
    server.run("streamable-http", host="127.0.0.1", port=WIN.port,
               streamable_http_path=WIN.path, transport_security=security,
               stateless_http=True)
