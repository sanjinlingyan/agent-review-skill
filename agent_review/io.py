"""File identity, confinement, and atomic writes."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path

from .errors import ReviewError


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"无法读取 JSON {path.name}: {exc}") from exc


def atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    if hasattr(path, "is_junction") and path.is_junction():
        return True
    try:
        attrs = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x400)


def files(root: Path) -> list[Path]:
    if not root.is_dir() or is_link(root):
        raise ReviewError(f"材料目录不存在或为链接: {root}")
    found = []
    for directory, dirnames, names in os.walk(root, followlinks=False):
        dirnames.sort()
        current = Path(directory)
        for name in list(dirnames):
            path = current / name
            if is_link(path):
                raise ReviewError(f"材料含链接或重解析点: {path}")
        for name in sorted(names):
            path = current / name
            if is_link(path):
                raise ReviewError(f"材料含链接或重解析点: {path}")
            if path.is_file():
                found.append(path)
    return found


def fingerprint(root: Path) -> dict:
    rows = []
    for path in files(root):
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": sha(path),
            "executable": int(bool(path.stat().st_mode & 0o111)),
        })
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"path": str(root.resolve()), "sha256": digest, "count": len(rows), "files": rows}


def same_tree(saved: dict, root: Path):
    current = fingerprint(root)
    if current["sha256"] != saved["sha256"] or current["files"] != saved["files"]:
        raise ReviewError("材料在审查期间发生变化，请新建运行")
    if Path(saved["path"]).resolve() != root.resolve():
        raise ReviewError("材料路径与冻结记录不一致")


def ensure_output(path: Path, repo: Path, inputs: list[Path]) -> Path:
    path = path.resolve()
    for root in [repo.resolve(), *[item.resolve() for item in inputs if item.exists()]]:
        if path == root or _contains(path, root) or _contains(root, path):
            raise ReviewError(f"输出目录必须与仓库和输入目录分离: {path}")
    for parent in [path, *path.parents]:
        if parent.exists() and is_link(parent):
            raise ReviewError("输出路径不能包含链接或重解析点")
    return path


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent


def confined(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ReviewError("缺少相对路径")
    value = value.replace("\\", "/")
    if value.startswith("/") or Path(value).is_absolute() or ":" in value.split("/")[0]:
        raise ReviewError(f"必须使用相对路径: {value}")
    parts = Path(value).parts
    if ".." in parts:
        raise ReviewError(f"路径越界: {value}")
    lexical = root.absolute()
    current = lexical
    for part in parts:
        current = current / part
        if current.exists() and is_link(current):
            raise ReviewError(f"链接或重解析点不允许: {value}")
    resolved = current.resolve()
    if not _contains(root.resolve(), resolved) and resolved != root.resolve():
        raise ReviewError(f"路径越界: {value}")
    return resolved


def safe_extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            if name.startswith("/") or re_drive(name) or ".." in Path(name).parts:
                raise ReviewError(f"压缩包路径越界: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ReviewError(f"压缩包含链接: {info.filename}")
            target = confined(dest, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, target.open("wb") as sink:
                sink.write(source.read())
    files(dest)


def re_drive(name: str) -> bool:
    head = name.split("/")[0]
    return len(head) >= 2 and head[1] == ":"
