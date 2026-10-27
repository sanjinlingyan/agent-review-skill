"""Freeze an explicit source allowlist, keeping contextual ledgers out of text."""
from __future__ import annotations

from pathlib import Path
import shutil

from .errors import ReviewError
from .io import confined, is_link, read_json, sha, write_json


def materialize(path: Path, destination: Path, internal: Path):
    path = path.resolve()
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != "agent-review.standards-bundle.v1":
        raise ReviewError("标准资料入口 schema 无效")
    base = (path.parent / data.get("root", ".")).resolve()
    documents = data.get("documents")
    auxiliary = data.get("auxiliary", [])
    if not isinstance(documents, list) or not documents or not isinstance(auxiliary, list):
        raise ReviewError("标准入口须列原文与独立的辅助资料")
    records = [{"path": str(path), "sha256": sha(path)}]
    seen = set()
    for group, folder in ((documents, destination), (auxiliary, internal / "standard-context")):
        for item in group:
            if not isinstance(item, dict):
                raise ReviewError("标准文件须为对象")
            name = item.get("name")
            target = confined(folder, name)
            if name.casefold() in seen:
                raise ReviewError("标准入口重名")
            seen.add(name.casefold())
            if group is documents and item.get("role") not in {"official_source", "current_decision", "source_asset"}:
                raise ReviewError("首轮正文不能包含历史报告、旧判断或范围事实")
            source = confined(base, item.get("path"))
            if not source.is_file() or is_link(source) or sha(source) != item.get("sha256"):
                raise ReviewError("标准原件缺失或摘要不符")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            records.append({"path": str(source), "sha256": item["sha256"]})
    write_json(internal / "standards-bundle.json", data)
    return {"version": data.get("version"), "files": records,
            "context_notice": "辅助条款、裁决和范围需核当前适用性；不继承历史事实，不读取旧报告结论"}


def verify(record):
    if record:
        for item in record["files"]:
            path = Path(item["path"])
            if not path.is_file() or sha(path) != item["sha256"]:
                raise ReviewError("标准资料入口或依赖变化，请新建运行")
