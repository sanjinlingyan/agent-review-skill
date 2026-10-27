"""Bind judgments to rebuilt obligations and to real files."""
from __future__ import annotations

import re
from pathlib import Path

from .errors import ReviewError, ReviewIncomplete
from .io import confined, sha

ORIGINS = {"static": "静态核验", "historical": "历史记录经复核复用", "measured": "本次实测"}
REPAIR_OWNERS = {"supplier_agent": "乙方 Agent", "human_reviewer": "真人复核者 / QC", "customer": "甲方"}
BLOCKER_OWNERS = {**REPAIR_OWNERS, "environment": "环境负责人", "reviewer": "审查 Agent"}
FAKE_REVIEWER = re.compile(r"(?:专家[ABab甲乙]?|QC|审核[员者]|人类|真人)", re.I)
VERIFIED = {"identity", "sha256", "version", "scope"}


def reviewer_id(value) -> str:
    if not isinstance(value, str) or not value.strip() or FAKE_REVIEWER.fullmatch(value.strip()):
        raise ReviewError("reviewer_id 应为实际 Agent 标识，不能冒充真人")
    return value.strip()


def validate_results(expected, payload, state, run, *, final: bool):
    if not isinstance(payload, dict):
        raise ReviewError("results.json 必须是对象")
    if payload.get("schema") != "agent-review.results.v1" or payload.get("run_id") != state["run_id"]:
        raise ReviewError("结果的 schema 或运行编号与本次冻结不一致")
    if payload.get("implementation_sha256") != state["implementation_sha256"]:
        raise ReviewError("审查代码已变化，请新建运行")
    reviewer_id(payload.get("reviewer_id"))
    rows = payload.get("clause_results")
    if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
        raise ReviewError("clause_results 必须是数组")
    by_expected = {item["instance_id"]: item for item in expected}
    seen = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if instance_id not in by_expected:
            raise ReviewError(f"结果包含清单之外的项目: {instance_id}")
        if instance_id in seen:
            raise ReviewError(f"结果重复: {instance_id}")
        seen[instance_id] = _row(row, by_expected[instance_id], state, run)
    missing = [item for item in by_expected if item not in seen]
    _scorer(payload.get("scorer_unknowns") or [], seen, by_expected)
    if final and missing:
        raise ReviewIncomplete("仍有适用项没有判断: " + "、".join(missing[:8]))
    open_rows = [item for item in seen.values() if item["progress"] != "completed"]
    if final and open_rows:
        sample = "、".join(item["instance_id"] for item in open_rows[:8])
        raise ReviewIncomplete(f"仍有适用项未完成或受阻: {sample}")
    ordered = [seen[item["instance_id"]] for item in expected if item["instance_id"] in seen]
    return ordered


def _row(row, expected, state, run):
    for key in ("clause_id", "case_key", "task_id"):
        if row.get(key) != expected[key]:
            raise ReviewError(f"{expected['instance_id']} 的 {key} 与冻结清单不一致")
    if (row.get("round") or None) != expected["round"]:
        raise ReviewError(f"{expected['instance_id']} 的轮次与冻结清单不一致")
    progress = row.get("progress")
    if progress not in {"not_started", "in_progress", "blocked", "completed"}:
        raise ReviewError(f"{expected['instance_id']} 的进度无效")
    status = row.get("status")
    if progress == "completed":
        if status not in {"pass", "fail"}:
            raise ReviewError(f"{expected['instance_id']} 完成时只能是通过或未通过")
    elif status not in {None, ""}:
        raise ReviewError(f"{expected['instance_id']} 未完成时不能填写通过或未通过")
    else:
        status = None
    actions = {item["id"]: item for item in expected["actions"]}
    reported = row.get("action_results") or []
    if not isinstance(reported, list):
        raise ReviewError(f"{expected['instance_id']} 的动作结果必须是数组")
    done = {}
    for item in reported:
        if not isinstance(item, dict) or item.get("id") not in actions or item.get("id") in done:
            raise ReviewError(f"{expected['instance_id']} 的动作结果无法对应必要动作")
        if item.get("state") != "performed" or not isinstance(item.get("detail"), str) or not item["detail"].strip():
            raise ReviewError(f"{expected['instance_id']} 的动作 {item.get('id')} 还没有实际执行记录")
        done[item["id"]] = item["detail"].strip()
    if progress == "completed" and set(done) != set(actions):
        raise ReviewError(f"{expected['instance_id']} 还有必要动作没有执行")
    raw_evidence = row.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise ReviewError(f"{expected['instance_id']} 的证据必须是数组")
    evidence = [_evidence(item, expected, state, run) for item in raw_evidence]
    if progress == "completed" and not evidence:
        raise ReviewError(f"{expected['instance_id']} 缺少证据")
    if status == "pass":
        useful = [item for item in evidence if not item["missing"] and item["root"] != "standards"]
        if not useful:
            raise ReviewError(f"{expected['instance_id']} 仅有依据原文或缺失记录，不能判通过")
    repair = None
    blocker = None
    if status == "fail":
        repair = _repair(row.get("repair"), expected, state)
    if progress == "blocked":
        blocker = _blocker(row.get("blocker"), expected["instance_id"])
    for label in ("summary", "observed", "impact"):
        if progress == "completed" and (not isinstance(row.get(label), str) or not row[label].strip()):
            raise ReviewError(f"{expected['instance_id']} 缺少{label}")
    merged = dict(expected)
    merged.update(
        progress=progress, status=status, summary=(row.get("summary") or "").strip(),
        observed=(row.get("observed") or "").strip(), impact=(row.get("impact") or "").strip(),
        action_results=[{"id": key, "detail": done[key]} for key in actions if key in done],
        evidence=evidence, repair=repair, blocker=blocker,
    )
    return merged


def _evidence(item, expected, state, run):
    if not isinstance(item, dict):
        raise ReviewError("证据条目必须是对象")
    root = item.get("root")
    if root not in {"submission", "original", "standards", "run"}:
        raise ReviewError("证据 root 无效")
    origin = item.get("origin")
    if origin not in ORIGINS:
        raise ReviewError("证据须标明静态核验、历史复用或本次实测")
    if origin == "historical":
        verified = item.get("verified")
        if not isinstance(verified, list) or not VERIFIED.issubset(set(verified)):
            raise ReviewError("复用历史结果前须复核身份、哈希、版本、适用范围和执行范围")
    missing = bool(item.get("missing"))
    locator = item.get("locator")
    if not isinstance(locator, str) or not locator.strip():
        raise ReviewError("证据缺少定位")
    case_key = item.get("case_key") or expected["case_key"]
    base = _root(root, case_key, state, run)
    path = confined(base, item.get("path"))
    if missing:
        if path.exists():
            raise ReviewError(f"文件仍然存在，不能记为缺失: {item.get('path')}")
        return {"root": root, "path": item["path"].replace("\\", "/"), "locator": locator.strip(),
                "origin": origin, "missing": True, "sha256": None, "case_key": case_key}
    if not path.is_file():
        raise ReviewError(f"证据文件不存在；已核实缺失时须标记 missing: {item.get('path')}")
    digest = sha(path)
    if item.get("sha256") != digest:
        raise ReviewError(f"证据哈希与冻结文件不一致: {item.get('path')}")
    return {"root": root, "path": item["path"].replace("\\", "/"), "locator": locator.strip(),
            "origin": origin, "missing": False, "sha256": digest, "case_key": case_key}


def _root(root, case_key, state, run):
    if root == "standards":
        return Path(state["standards"]["path"])
    if root == "run":
        return run
    case = next((item for item in state["cases"] if item["key"] == case_key), None)
    if case is None or not case.get(root):
        raise ReviewError(f"证据根目录不存在: {root}")
    return Path(case[root]["path"])


def _repair(repair, expected, state):
    if not isinstance(repair, dict):
        raise ReviewError(f"{expected['instance_id']} 未通过时必须写出修复项")
    owner = repair.get("owner")
    if owner not in REPAIR_OWNERS:
        raise ReviewError("修复责任只能是乙方、真人复核者或甲方；审查环境受阻不进入修复单")
    allowed = repair.get("allowed_paths")
    if not isinstance(allowed, list) or any(not isinstance(item, str) for item in allowed):
        raise ReviewError("allowed_paths 必须是路径数组")
    if owner == "supplier_agent" and not allowed:
        raise ReviewError("交给乙方 Agent 的修复项必须写明允许修改的路径")
    if expected["case_key"] != "batch":
        case = next(item for item in state["cases"] if item["key"] == expected["case_key"])
        for value in allowed:
            confined(Path(case["submission"]["path"]), value)
    for label in ("problem", "location", "action", "done_when"):
        if not isinstance(repair.get(label), str) or not repair[label].strip():
            raise ReviewError(f"{expected['instance_id']} 的修复项缺少{label}")
    return {
        "problem": repair["problem"].strip(), "location": repair["location"].strip(),
        "action": repair["action"].strip(), "done_when": repair["done_when"].strip(),
        "owner": owner, "allowed_paths": allowed,
    }


def _blocker(blocker, instance_id):
    if not isinstance(blocker, dict):
        raise ReviewError(f"{instance_id} 受阻时必须写明原因、责任和下一步")
    owner = blocker.get("owner")
    if owner not in BLOCKER_OWNERS:
        raise ReviewError("受阻责任无效")
    for label in ("reason", "next_action"):
        if not isinstance(blocker.get(label), str) or not blocker[label].strip():
            raise ReviewError(f"{instance_id} 的受阻记录缺少{label}")
    return {"reason": blocker["reason"].strip(), "owner": owner, "next_action": blocker["next_action"].strip()}


def validate_scorer(unknowns, rows, expected_ids):
    if not isinstance(unknowns, list):
        raise ReviewError("scorer_unknowns 必须是数组")
    seen = set()
    by_id = {item["instance_id"]: item for item in rows.values()} if isinstance(rows, dict) else {item["instance_id"]: item for item in rows}
    for item in unknowns:
        if not isinstance(item, dict):
            raise ReviewError("评分器 unknown 记录必须是对象")
        unknown_id = item.get("id")
        if not isinstance(unknown_id, str) or not unknown_id or unknown_id in seen:
            raise ReviewError("评分器 unknown 编号无效或重复")
        seen.add(unknown_id)
        kind, disposition = item.get("kind"), item.get("disposition")
        if kind not in {"infrastructure", "unparseable", "ambiguity", "capability"}:
            raise ReviewError("unknown 原因须区分基础设施、解析、规则歧义或核心判定能力")
        if disposition not in {"isolated", "supplier_fail", "reviewer_block"}:
            raise ReviewError("unknown 处置无效")
        instance_id = item.get("clause_instance")
        if instance_id not in expected_ids:
            raise ReviewError("unknown 必须对应一个适用义务")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ReviewError("unknown 必须写明追查到的原因")
        if kind != "capability" and disposition == "supplier_fail":
            raise ReviewError("基础设施、解析失败或规则歧义不能直接转成乙方未通过")
        if kind == "capability" and disposition == "isolated":
            raise ReviewError("已确认的核心判定缺陷不能当成已隔离的 unknown")
        current = by_id.get(instance_id)
        if disposition == "supplier_fail" and (current is None or current.get("status") != "fail"):
            raise ReviewError("核心判定缺陷须落到对应义务的未通过结论")
        if disposition == "reviewer_block" and (current is None or current.get("progress") != "blocked"):
            raise ReviewError("尚未查清的 unknown 应保持审查受阻，不能先给验收结论")


def _scorer(unknowns, rows, expected):
    validate_scorer(unknowns, rows, set(expected))
