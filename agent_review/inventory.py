"""Freeze official files as paragraph-sized reading units."""
from __future__ import annotations

import re
from pathlib import Path

from .errors import ReviewError
from .io import files, sha

TEXT = {".md", ".txt", ".markdown", ".csv", ".json", ".yml", ".yaml"}
HEADING = re.compile(r"#{1,6}\s+(.+)")


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT:
        return "text"
    if suffix == ".pdf":
        return "pdf"
    return "asset"


def paragraphs(text: str) -> list[dict]:
    groups, current = [], []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            current.append((number, line))
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    found = []
    for index, group in enumerate(groups, 1):
        found.append({
            "id": f"g{index}",
            "block_ids": [f"l{number}" for number, _line in group],
            "text": "\n".join(line for _number, line in group),
        })
    return found


def build_inventory(root: Path) -> dict:
    documents = []
    for index, path in enumerate(files(root), 1):
        kind = kind_of(path)
        text = ""
        if kind == "text":
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeError as exc:
                raise ReviewError(f"依据文本须为 UTF-8: {path.name}") from exc
            if not text.strip():
                raise ReviewError(f"依据文件没有可阅读正文: {path.name}")
        documents.append({
            "id": f"d-{index:02d}",
            "path": path.relative_to(root).as_posix(),
            "filename": path.name,
            "sha256": sha(path),
            "kind": kind,
            "paragraphs": paragraphs(text) if kind == "text" else [],
        })
    if not documents:
        raise ReviewError("甲方依据目录是空的")
    return {"schema": "agent-review.source-inventory.v1", "documents": documents}


def section_title(paras: list[dict], override: str | None = None) -> str:
    if isinstance(override, str) and override.strip():
        return override.strip()
    for para in paras:
        match = HEADING.match(para["text"].strip())
        if match:
            return match.group(1).strip()
    return "正文"
