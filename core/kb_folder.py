"""资料库的「文件夹」= 资料目录下的真实子目录（不是台账里的虚拟字段）。

设计取舍：上传整个文件夹时本来就会按原层级建目录，扫描也是按磁盘来，
所以「文件夹」只有磁盘这一份真相 —— 新建/移动/删除都在磁盘上做，台账里的 path 跟着改。

所有入参一律当不可信：相对路径都过 clean_rel()（挡 ../、绝对路径、隐藏名、超深层级、怪字符），
再 resolve() 后校验是否仍在资料目录内（双保险，防止符号链接之类的花样）。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

MAX_DEPTH = 6                                     # 跟上传时的层级上限保持一致
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


def clean_rel(rel: str) -> tuple[str, str]:
    """把用户给的文件夹名/路径洗成安全的相对路径（POSIX 风格，''=根目录）。

    返回 (相对路径, 为什么不行)。为什么不行非空时，相对路径是空串。
    """
    raw0 = (rel or "").replace("\\", "/").strip()
    if raw0.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw0):
        return "", "不能是绝对路径（只能写资料库里的相对位置）"
    raw = raw0.strip("/")
    if not raw:
        return "", ""                             # 根目录：合法
    # 关键：`..` / `.` / 空段一律拒绝，绝不「悄悄改写」成别的路径
    # （改写会让「删除 /etc/x」变成删除库里的 etc/x —— 用户以为删的是别的东西）
    segs = raw.split("/")
    if any(x.strip() in ("", ".", "..") for x in segs):
        return "", "路径里有 .. / . 或空的一段，不允许（只能写资料库里的相对位置）"
    parts = [p.strip() for p in segs]
    if not parts:
        return "", "文件夹名不合法"
    if len(parts) > MAX_DEPTH:
        return "", f"层级太深（最多 {MAX_DEPTH} 层）"
    for p in parts:
        if p.startswith("."):
            return "", "隐藏文件夹不允许"
        if _BAD_CHARS.search(p):
            return "", f"名字里有不允许的字符：{p}"
        if len(p) > 60:
            return "", f"名字太长：{p[:20]}…"
    return "/".join(parts), ""


def _inside(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def docs_dir_of(root: Path, docs_rel: str) -> Path:
    return (root / (docs_rel or "")).resolve()


def dirs_under(root: Path, docs_rel: str) -> list[str]:
    """资料目录下所有子目录（相对资料目录，POSIX 风格，跳过隐藏目录）。"""
    base = docs_dir_of(root, docs_rel)
    out: list[str] = []
    if not base.is_dir():
        return out
    for p in sorted(base.rglob("*")):
        if not p.is_dir():
            continue
        parts = p.relative_to(base).parts
        if any(x.startswith(".") for x in parts):
            continue
        out.append("/".join(parts))
    return out


def dir_of(path_in_catalog: str, docs_rel: str) -> str:
    """台账里的 path（相对窗口根）→ 所在文件夹（相对资料目录，''=根目录）。"""
    p = (path_in_catalog or "").replace("\\", "/")
    prefix = (docs_rel or "").strip("/")
    prefix = (prefix + "/") if prefix else ""
    if prefix and not p.startswith(prefix):
        return ""
    rest = p[len(prefix):]
    return rest.rsplit("/", 1)[0] if "/" in rest else ""


def rel_in_docs(path_in_catalog: str, docs_rel: str) -> str:
    """台账 path（相对窗口根）→ 相对资料目录的路径（POSIX，''=资料目录本身）。"""
    p = (path_in_catalog or "").replace("\\", "/")
    prefix = (docs_rel or "").strip("/")
    prefix = (prefix + "/") if prefix else ""
    if prefix and not p.startswith(prefix):
        return ""
    return p[len(prefix):]


def counts(docs: dict, docs_rel: str) -> dict[str, int]:
    """每个文件夹里**直接**放着几篇（不含子目录里的）。"""
    out: dict[str, int] = {}
    for e in docs.values():
        d = dir_of(e.get("path") or "", docs_rel)
        out[d] = out.get(d, 0) + 1
    return out


def subdirs_of(all_dirs: list[str], cur: str) -> list[str]:
    """cur 文件夹的直接子目录名（不含更深层）。"""
    out = []
    for d in all_dirs:
        if cur:
            if not d.startswith(cur + "/"):
                continue
            rest = d[len(cur) + 1:]
        else:
            rest = d
        if rest and "/" not in rest:
            out.append(rest)
    return out


def has_files(root: Path, docs_rel: str, rel: str) -> bool:
    """这个文件夹里（含子目录）有没有文件。"""
    base = docs_dir_of(root, docs_rel)
    rel2, why = clean_rel(rel)
    if why or not rel2:
        return True                               # 不合法就当"有东西"，别让人删
    target = base / rel2
    if not target.is_dir():
        return False
    return any(p.is_file() and not p.name.startswith(".") for p in target.rglob("*"))


def mkdir(root: Path, docs_rel: str, rel: str) -> tuple[str, str]:
    """在资料目录里新建文件夹。返回 (相对路径, 为什么不行)。"""
    rel2, why = clean_rel(rel)
    if why:
        return "", why
    if not rel2:
        return "", "没给文件夹名"
    base = docs_dir_of(root, docs_rel)
    target = base / rel2
    if not _inside(base, target):
        return "", "路径越界"
    if target.exists():
        return "", "这个文件夹已经有了"
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        return "", f"建不了：{e}"
    return rel2, ""


def rmdir(root: Path, docs_rel: str, rel: str) -> tuple[str, str]:
    """删一个**空**文件夹（里面只要有文件就拒绝，防手滑）。"""
    rel2, why = clean_rel(rel)
    if why:
        return "", why
    if not rel2:
        return "", "根目录不能删"
    base = docs_dir_of(root, docs_rel)
    target = base / rel2
    if not _inside(base, target):
        return "", "路径越界"
    if not target.is_dir():
        return "", "这个文件夹不在"
    if any(target.iterdir()):
        return "", "文件夹里还有东西（先清空/移走再删）"
    try:
        target.rmdir()
    except OSError as e:
        return "", f"删不掉：{e}"
    return rel2, ""


def move_file(root: Path, docs_rel: str, path_in_catalog: str, dest_rel: str) -> tuple[str, str]:
    """把台账里的 path 指向的文件挪到目标文件夹。

    返回 (新的 path（相对窗口根）, 为什么不行)。目标里重名会自动加「(2)」，绝不覆盖。
    """
    dest2, why = clean_rel(dest_rel)
    if why:
        return "", why
    base = docs_dir_of(root, docs_rel)
    src = (root / (path_in_catalog or "")).resolve()
    if not _inside(base, src):
        return "", "只允许移动资料目录里的文件"
    if not src.is_file():
        return "", "找不到这个文件（可能已经不在磁盘上了）"
    if dir_of(path_in_catalog, docs_rel) == dest2:
        return path_in_catalog, ""                # 已经在这个文件夹里了
    dstdir = (base / dest2) if dest2 else base
    if not _inside(base, dstdir):
        return "", "路径越界"
    dstdir.mkdir(parents=True, exist_ok=True)
    dst = dstdir / src.name
    n = 2
    while dst.exists():
        dst = dstdir / f"{src.stem} ({n}){src.suffix}"
        n += 1
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        return "", f"移不了：{e}"
    return dst.resolve().relative_to(root.resolve()).as_posix(), ""


def breadcrumb(cur: str) -> list[tuple[str, str]]:
    """面包屑：[(显示名, 相对路径)]，第一项是「全部资料」（''）。"""
    out = [("全部资料", "")]
    acc = []
    for part in [p for p in (cur or "").split("/") if p]:
        acc.append(part)
        out.append((part, "/".join(acc)))
    return out


# ---------------------------------------------------------------- 文件夹默认等级
def level_for(folders: dict, rel: str, fallback: str = "") -> str:
    """取某个文件夹的默认等级：自己没有就逐级往上找（子文件夹继承父文件夹）。"""
    parts = [x for x in (rel or "").split("/") if x]
    while parts:
        lv = str((folders or {}).get("/".join(parts), {}).get("level") or "")
        if lv:
            return lv
        parts.pop()
    return fallback or ""


# ---------------------------------------------------------------- 回收站
def trash_root(root: Path, docs_rel: str) -> Path:
    return docs_dir_of(root, docs_rel) / ".回收站"


def _load_index(tr: Path) -> dict:
    f = tr / "index.json"
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _save_index(tr: Path, idx: dict) -> None:
    tr.mkdir(parents=True, exist_ok=True)
    (tr / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def to_trash(root: Path, docs_rel: str, rel: str) -> tuple[str, str]:
    """把一个文件夹（或单篇文件）整体挪进回收站。返回 (回收站里的名字, 为什么不行)。"""
    rel2, why = clean_rel(rel)
    if why:
        return "", why
    if not rel2:
        return "", "根目录不能删"
    base = docs_dir_of(root, docs_rel)
    src = base / rel2
    if not _inside(base, src):
        return "", "路径越界"
    if not src.exists():
        return "", "找不到这个东西（可能已经不在磁盘上了）"
    tr = trash_root(root, docs_rel)
    tr.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{Path(rel2).name}"
    dst = tr / name
    n = 2
    while dst.exists():
        dst = tr / f"{name}-{n}"
        n += 1
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        return "", f"挪不进回收站：{e}"
    idx = _load_index(tr)
    idx[dst.name] = {"orig": rel2, "when": datetime.now().isoformat(timespec="seconds"),
                     "kind": "dir" if dst.is_dir() else "file"}
    _save_index(tr, idx)
    return dst.name, ""


def list_trash(root: Path, docs_rel: str) -> list[dict]:
    """回收站里有什么：名字 / 原来的位置 / 什么时候删的 / 多大。"""
    tr = trash_root(root, docs_rel)
    idx = _load_index(tr)
    out = []
    for name, meta in idx.items():
        p = tr / name
        if not p.exists():
            continue
        files = [x for x in p.rglob("*") if x.is_file()] if p.is_dir() else [p]
        out.append({"name": name, "orig": meta.get("orig") or "", "when": meta.get("when") or "",
                    "kind": meta.get("kind") or ("dir" if p.is_dir() else "file"),
                    "files": len(files), "bytes": sum(x.stat().st_size for x in files)})
    return sorted(out, key=lambda x: x.get("when") or "", reverse=True)


def restore_trash(root: Path, docs_rel: str, name: str, dest_rel: str = "") -> tuple[str, str]:
    """把回收站里的东西放回去（默认放回原位；重名自动加「(2)」）。返回 (放回后的相对路径, 为什么不行)。"""
    tr = trash_root(root, docs_rel)
    idx = _load_index(tr)
    meta = idx.get(name)
    if not meta:
        return "", "回收站里没有这一条"
    src = tr / name
    if not src.exists():
        return "", "回收站里的东西已经不在了"
    base = docs_dir_of(root, docs_rel)
    want = (dest_rel or meta.get("orig") or "").strip().strip("/")
    target = base / want
    if not _inside(base, target):
        return "", "路径越界"
    if target.exists():                                  # 原位被占了 → 加 (2)
        stem = target.name
        parent = target.parent
        n = 2
        while (parent / f"{stem} ({n})").exists():
            n += 1
        target = parent / f"{stem} ({n})"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(target))
    except OSError as e:
        return "", f"放不回去：{e}"
    idx.pop(name, None)
    _save_index(tr, idx)
    return target.resolve().relative_to(root.resolve()).as_posix(), ""


def purge_trash(root: Path, docs_rel: str, name: str = "", older_days: int = 0) -> tuple[int, str]:
    """彻底删掉回收站里的东西：指定一条，或清掉超过 N 天的（0 = 全部）。"""
    import time
    tr = trash_root(root, docs_rel)
    idx = _load_index(tr)
    if name and name not in idx:
        return 0, "回收站里没有这一条"
    cutoff = (time.time() - older_days * 86400) if older_days else 0
    n = 0
    for key in list(idx.keys()):
        if name and key != name:
            continue
        p = tr / key
        if cutoff and p.exists() and p.stat().st_mtime > cutoff:
            continue
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
        idx.pop(key, None)
        n += 1
    _save_index(tr, idx)
    return n, ""


def folder_stats(root: Path, docs_rel: str, rel: str) -> dict:
    """一个文件夹里有多少文件、多大（回收站统计用）。"""
    base = docs_dir_of(root, docs_rel)
    p = base / rel
    if not p.is_dir():
        return {"files": 0, "bytes": 0}
    fs = [x for x in p.rglob("*") if x.is_file() and not x.name.startswith(".")]
    return {"files": len(fs), "bytes": sum(x.stat().st_size for x in fs)}
