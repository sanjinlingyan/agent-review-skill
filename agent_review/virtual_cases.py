"""Native controls, typed target vectors, live process checks and diagnostics."""
from __future__ import annotations

import math
from pathlib import Path
import re
import shutil

from .errors import ReviewError
from .io import confined, sha
from .results import _evidence, _repair, reviewer_id

KINDS = {"native_case", "process_check", "diagnostic"}


def require(condition, message):
    if not condition:
        raise ReviewError(message)


def validate(case):
    require(case.get("kind") in KINDS, "扩展案例类型无效")
    checks = case.get("expected_checks", {})
    require(isinstance(checks, dict) and all(isinstance(k, str) and k for k in checks), "expected_checks 须为字段路径到目标值的对象")
    if case["kind"] == "native_case":
        require(checks or case.get("unresolved_expectations"), "原生案例缺预声明目标")
    if case["kind"] == "diagnostic":
        require(not checks and case.get("expected") is None, "诊断观察不能补造预期")
    if case["kind"] == "process_check":
        require(case.get("targets") == ["delivery"] and case.get("rounds") == ["delivery"], "流程案例按交付对象核查，不机械分轮")
        require(case.get("expected") == "pass" and isinstance(case.get("criterion_text"), str) and case["criterion_text"].strip(), "流程案例须声明审查标准")


def steps(case):
    chunks = [line.strip() for line in str(case.get("process_steps", case["action"])).splitlines() if line.strip()]
    return [{"id": f"A{i}", "text": line} for i, line in enumerate(chunks, 1)]


def bound_evidence(raw, subject, state, run):
    require(isinstance(raw, dict), "工件绑定须为证据对象")
    return _evidence({"root": "run", "origin": "static", **raw}, {"case_key": subject["key"]}, state, run)


def source_path(proof, subject, state, run):
    root = proof["root"]
    base = run if root == "run" else Path(state["standards"]["path"]) if root == "standards" else Path(subject[root]["path"])
    return confined(base, proof["path"])


def packet(case, binding, folder, subject, state, run):
    meta = {"kind": case["kind"], "expected_checks": dict(case.get("expected_checks", {})),
            "expected_unknown_kind": case.get("expected_unknown_kind"), "bound_sources": [], "missing_inputs": [],
            "actions": steps(case) if case["kind"] == "process_check" else []}
    if case["kind"] == "process_check":
        return meta
    files = binding.get("files", {})
    basis = binding.get("basis_files", [])
    require(isinstance(files, dict) and isinstance(basis, list), "绑定须含 files 对象和 basis_files 数组")
    native = 0
    for name, raw in files.items():
        require(name not in {"task.json", "requirements.md"} and not name.startswith("basis/"), "绑定材料不能覆盖任务或依据")
        proof = bound_evidence(raw, subject, state, run)
        require(not proof["missing"], "缺失工件不能绑定为已提供")
        source = source_path(proof, subject, state, run)
        target = confined(folder, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        native += source.suffix.lower() in {".pdf", ".step", ".stp"}
        meta["bound_sources"].append(proof)
    if not native:
        meta["missing_inputs"].append("缺少原表所指的原生 PDF/STEP 控制工件；不能用文字描述代替")
    for i, raw in enumerate(basis, 1):
        proof = bound_evidence(raw, subject, state, run)
        require(proof["root"] in {"standards", "original"} and not proof["missing"], "盲审依据须来自冻结的官方依据或原题")
        source = source_path(proof, subject, state, run)
        target = folder / "basis" / (f"document-{i:03d}" + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        meta["bound_sources"].append(proof)
    if not basis:
        meta["missing_inputs"].append("缺少本轮官方原文绑定，不能将导入表预期或历史评分发给盲审者")
    unresolved = set(case.get("unresolved_expectations", []))
    supplied = binding.get("resolved_expectations", {})
    require(isinstance(supplied, dict) and set(supplied) <= unresolved, "补充预期只能填写原表未给值的字段，不能覆盖已声明目标")
    if supplied:
        proof = bound_evidence(binding.get("expectation_basis"), subject, state, run)
        require(proof["root"] in {"standards", "original"} and not proof["missing"], "补充预期须绑定存在的原文，不从历史重放结果猜答案")
        meta["bound_sources"].append(proof)
        meta["expected_checks"].update(supplied)
    if unresolved - set(supplied):
        meta["missing_inputs"].append("原表预期值待核准：" + "、".join(sorted(unresolved - set(supplied))))
    return meta


def verify_sources(item, subject, state, run):
    for raw in item.get("bound_sources", []):
        bound_evidence(raw, subject, state, run)


def lookup(value, path):
    if isinstance(value, dict) and path in value:
        return value[path]
    for key in path.split("."):
        value = value[int(key)] if isinstance(value, list) and key.isdigit() else value[key]
    return value


def project(payload, item, field_map=None):
    field_map = field_map or {}
    values = {}
    checks = item.get("expected_checks", {})
    if not checks:
        return {"values": payload.get("values", payload) if isinstance(payload, dict) else payload, "unknown_kinds": {}}
    for key in checks:
        try:
            if key in field_map:
                value = payload
                for part in field_map[key]:
                    value = value[int(part)] if isinstance(value, list) and str(part).isdigit() else value[part]
            else:
                value = lookup(payload.get("values", payload) if isinstance(payload, dict) else payload, key)
            values[key] = value
        except (KeyError, TypeError, IndexError):
            pass
    return {"values": values, "unknown_kinds": payload.get("unknown_kinds", {}) if isinstance(payload, dict) else {}}


def same_value(actual, expected):
    if isinstance(expected, bool):
        return type(actual) is bool and actual == expected
    if isinstance(expected, (int, float)):
        return type(actual) in {int, float} and math.isfinite(actual) and actual == expected
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(same_value(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(same_value(actual[k], v) for k, v in expected.items())
    return type(actual) is type(expected) and actual == expected


def compare(observed, item):
    values = observed.get("values", {})
    details, unresolved = [], []
    for key, expected in item["expected_checks"].items():
        if key not in values:
            unresolved.append(key + " 缺少实际输出")
            continue
        actual = values[key]
        kind = observed.get("unknown_kinds", {}).get(key)
        if actual == "unknown" and (expected != "unknown" or kind != item.get("expected_unknown_kind")):
            unresolved.append(key + " 的 unknown 须逐因追查")
            continue
        details.append({"path": key, "expected": expected, "actual": actual, "matched": same_value(actual, expected)})
    return (None if unresolved else all(d["matched"] for d in details)), details, unresolved


def process_observation(raw, item, state, run):
    require(raw.get("kind") == "delivery_check", "流程案例须提交当前交付的实际审查记录")
    reviewer_id(raw.get("reviewer_id"))
    require(raw.get("subject_sha256") == next(c["submission"]["sha256"] for c in state["cases"] if c["key"] == item["case_key"]), "流程观察未绑定当前交付")
    require(raw.get("verdict") in {"pass", "fail", "unknown"}, "流程观察判断无效")
    require(isinstance(raw.get("reason"), str) and raw["reason"].strip(), "流程观察须说明实际核查理由")
    actions = raw.get("action_results", [])
    require(isinstance(actions, list) and {a.get("id") for a in actions if isinstance(a, dict)} == {a["id"] for a in item["actions"]}
            and len(actions) == len(item["actions"])
            and all(a.get("state") == "performed" and isinstance(a.get("detail"), str) and a["detail"].strip() for a in actions), "每个流程步骤须有实际执行记录")
    evidence = raw.get("evidence", [])
    require(isinstance(evidence, list) and evidence, "流程观察须有实际证据")
    normalized = [_evidence(e, {"case_key": item["case_key"]}, state, run) for e in evidence]
    require(any(e["root"] != "standards" for e in normalized), "流程观察不能只引用标准而不核当前交付")
    if raw["verdict"] == "pass":
        require(any(e["root"] != "standards" and not e["missing"] for e in normalized), "流程通过须有当前实际证据，不能只填缺失记录")
    if raw["verdict"] == "fail":
        _repair(raw.get("repair"), item, state)
    return raw["verdict"], raw.get("unknown_kind"), {"path": raw["_file"], "sha256": raw["_sha256"], "locator": "当前流程审查记录"}, raw.get("reason", "")
