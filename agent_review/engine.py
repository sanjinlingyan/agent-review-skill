"""Freeze a review, then refuse a formal report until the agent finishes it."""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .catalog import instances, validate_catalog
from .errors import ReviewError, ReviewIncomplete
from .execute import execute as run_plan
from .inventory import build_inventory
from .io import atomic_text, ensure_output, fingerprint, is_link, read_json, safe_extract, same_tree, sha, sha_bytes, write_json
from .render import render_html, render_repair, verdict_of
from .results import validate_results
from . import virtual

REPO = Path(__file__).resolve().parents[1]
UNCLEAR = ("未明", "待定", "不清楚", "未知", "无法确定")


def implementation_sha256() -> str:
    digest = hashlib.sha256()
    paths = [*(REPO / "agent_review").glob("*.py"), REPO / "scripts" / "review.py",
             REPO / "AGENTS.md", REPO / "skills" / "agent-review" / "SKILL.md",
             *(REPO / "references").glob("*.md")]
    for path in sorted(paths):
        digest.update(path.relative_to(REPO).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def skill_commit() -> str:
    try:
        found = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "未暴露"
    return found or "未暴露"


def executor_record() -> dict:
    def exposed(name):
        value = os.environ.get(name, "").strip()
        return value or "未暴露"

    return {
        "host_product": exposed("AGENT_REVIEW_HOST"),
        "model": exposed("AGENT_REVIEW_MODEL"),
        "model_version": exposed("AGENT_REVIEW_MODEL_VERSION"),
        "python": platform.python_version(),
        "system": platform.system(),
        "skill_commit": skill_commit(),
    }


def _identity(submission: Path):
    for relative in ("record.json", "task/record.json"):
        path = submission / relative
        if not path.is_file():
            continue
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        task_id = data.get("id") or data.get("instance_id")
        if isinstance(task_id, str) and task_id.strip():
            revision = data.get("revision")
            revision = revision.strip() if isinstance(revision, str) and revision.strip() else "未声明"
            return task_id.strip(), revision
    return submission.name, "未声明"


def _materialize(source: Path, dest: Path):
    source = source.resolve()
    if source.is_file() and source.suffix.lower() == ".zip":
        safe_extract(source, dest)
        return dest, {"path": str(source), "sha256": sha(source)}
    if source.is_dir() and not is_link(source):
        return source, None
    raise ReviewError(f"输入须是目录或 ZIP: {source}")


def _scope(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ReviewError("scope 必须是对象")
    expected = raw.get("expected_ids")
    if expected is not None:
        if (not isinstance(expected, list) or not expected or len(expected) != len(set(expected))
                or any(not isinstance(item, str) or not item.strip() for item in expected)):
            raise ReviewError("scope.expected_ids 必须是非空且不重复的题号")
    excluded = raw.get("excluded_ids", [])
    if not isinstance(excluded, list) or any(not isinstance(item, str) for item in excluded):
        raise ReviewError("scope.excluded_ids 必须是题号数组")
    return raw


def load_batch(path: Path):
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ReviewError("批次清单必须包含非空 cases 数组")
    pairs = []
    for row in data["cases"]:
        if not isinstance(row, dict) or not isinstance(row.get("submission"), str) or not row["submission"].strip():
            raise ReviewError("每个 cases 条目都要有 submission")
        submission = (path.parent / row["submission"]).resolve()
        original = (path.parent / row["original"]).resolve() if isinstance(row.get("original"), str) else None
        pairs.append((original, submission))
    scope = data.get("scope")
    if isinstance(scope, str):
        scope = read_json((path.parent / scope).resolve())
    return pairs, _scope(scope)


def prepare(pairs, standards: Path, out: Path | None = None, *, batch_path: Path | None = None, scope=None, virtual_suite: Path | None = None):
    if not pairs:
        raise ReviewError("必须提供待审提交")
    standards = standards.resolve()
    inputs = [standards]
    for original, submission in pairs:
        inputs.append(submission)
        if original:
            inputs.append(original)
    if batch_path:
        inputs.append(batch_path)
    if virtual_suite:
        inputs.append(virtual_suite)
    out = ensure_output(out or REPO.parent / ".agent-review" / "runs", REPO, inputs)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:10]
    run = out / run_id
    run.mkdir(parents=True, exist_ok=False)
    (run / "_internal" / "comparisons").mkdir(parents=True)
    (run / "_internal" / "executions").mkdir(parents=True)
    standards_dir, standards_archive = _materialize(standards, run / "materials" / "standards")
    cases = []
    seen = set()
    for index, (original, submission) in enumerate(pairs, 1):
        key = f"case-{index:04d}"
        submission_dir, submission_archive = _materialize(submission, run / "materials" / key / "submission")
        original_dir, original_archive = (None, None)
        if original:
            original_dir, original_archive = _materialize(original, run / "materials" / key / "original")
            if original_dir.resolve() in {submission_dir.resolve(), standards_dir.resolve()}:
                raise ReviewError("原题、提交和依据必须是彼此独立的目录")
        if submission_dir.resolve() in seen or submission_dir.resolve() == standards_dir.resolve():
            raise ReviewError("提交、原题和依据必须彼此独立")
        seen.add(submission_dir.resolve())
        task_id, revision = _identity(submission_dir)
        if submission_archive and not any((submission_dir / name).is_file() for name in ("record.json", "task/record.json")):
            task_id = Path(submission_archive["path"]).stem
        cases.append({
            "key": key, "task_id": task_id, "revision": revision,
            "submission": fingerprint(submission_dir),
            "original": fingerprint(original_dir) if original_dir else None,
            "submission_archive": submission_archive, "original_archive": original_archive,
        })
    if len({item["task_id"] for item in cases}) != len(cases):
        raise ReviewError("同一题出现多份提交或版本；须明确选定版本，不能擅自取最新")
    state = {
        "schema": "agent-review.run.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": implementation_sha256(),
        "executor": executor_record(),
        "run_path": str(run),
        "standards": fingerprint(standards_dir),
        "standards_archive": standards_archive,
        "cases": cases,
        "batch": bool(batch_path),
        "scope": scope,
        "batch_manifest": None,
    }
    if batch_path:
        copied = run / "_internal" / "batch-manifest.json"
        copied.write_bytes(batch_path.read_bytes())
        state["batch_manifest"] = {"path": str(batch_path.resolve()), "sha256": sha(batch_path)}
    if virtual_suite:
        state["virtual_suite"] = virtual.attach(virtual_suite, run)
    write_json(run / "_internal" / "run.json", state)
    write_json(run / "_internal" / "source-inventory.json", build_inventory(standards_dir))
    return run


def _load(run: Path):
    run = run.resolve()
    state = read_json(run / "_internal" / "run.json")
    if state.get("schema") != "agent-review.run.v1" or state.get("run_id") != run.name:
        raise ReviewError("运行目录与冻结身份不一致")
    if state.get("implementation_sha256") != implementation_sha256():
        raise ReviewError("审查代码已变化，请新建运行")
    _verify(state)
    virtual.verify(run, state)
    state["run_path"] = str(run)
    return run, state


def _verify(state):
    same_tree(state["standards"], Path(state["standards"]["path"]))
    _archive(state.get("standards_archive"))
    for case in state["cases"]:
        same_tree(case["submission"], Path(case["submission"]["path"]))
        _archive(case.get("submission_archive"))
        if case.get("original"):
            same_tree(case["original"], Path(case["original"]["path"]))
            _archive(case.get("original_archive"))
    manifest = state.get("batch_manifest")
    if manifest and sha(Path(manifest["path"])) != manifest["sha256"]:
        raise ReviewError("批次名单在审查期间发生变化，请新建运行")


def _archive(record):
    if not record:
        return
    path = Path(record["path"])
    if not path.is_file() or sha(path) != record["sha256"]:
        raise ReviewError("归档在审查期间发生变化，请新建运行")


def _catalog(run: Path, state: dict):
    path = run / "_internal" / "catalog.json"
    if not path.is_file():
        return None, None
    catalog = validate_catalog(build_inventory(Path(state["standards"]["path"])), read_json(path))
    _reconcile(run, path)
    expected = instances(catalog, state["cases"])
    write_json(run / "_internal" / "obligations.json", {
        "schema": "agent-review.obligations.v1",
        "instances": [{key: item[key] for key in ("instance_id", "clause_id", "case_key", "task_id", "round", "title", "text", "actions", "evidence_required")} for item in expected],
    })
    return catalog, expected


def _reconcile(run: Path, catalog_path: Path):
    frozen = run / "_internal" / "catalog.frozen.json"
    current = catalog_path.read_bytes()
    digest = sha_bytes(current)
    if not frozen.exists():
        frozen.write_bytes(current)
        return
    previous = sha_bytes(frozen.read_bytes())
    if previous == digest:
        return
    revision_path = run / "_internal" / "catalog-revision.json"
    if not revision_path.is_file():
        raise ReviewError("依据清单在冻结后变化。新建运行，或写入 catalog-revision.json 说明增删")
    revision = read_json(revision_path)
    if revision.get("schema") != "agent-review.catalog-revision.v1":
        raise ReviewError("清单修订 schema 无效")
    if revision.get("from_sha256") != previous or revision.get("to_sha256") != digest:
        raise ReviewError("清单修订没有绑定冻结版本和当前版本")
    if not isinstance(revision.get("reason"), str) or not revision["reason"].strip():
        raise ReviewError("清单修订缺少理由")
    old_ids = {item.get("id") for item in read_json(frozen).get("clauses") or []}
    new_ids = {item.get("id") for item in read_json(catalog_path).get("clauses") or []}
    declared = revision.get("dropped_clause_ids") or []
    if not isinstance(declared, list):
        raise ReviewError("dropped_clause_ids 必须是数组")
    if old_ids - new_ids != set(declared):
        raise ReviewError("删除已冻结条款时必须逐条列出 dropped_clause_ids")


def _results_payload(run: Path):
    path = run / "_internal" / "results.json"
    if not path.is_file():
        return None
    return read_json(path)


def _batch_rows(state, payload, run: Path):
    if not state["batch"]:
        return []
    scope = state.get("scope") or {}
    expected_ids = scope.get("expected_ids")
    if not expected_ids:
        raise ReviewIncomplete("批次范围尚未确认，不能生成正式报告")
    received = [case["task_id"] for case in state["cases"]]
    missing = [item for item in expected_ids if item not in received]
    extra = [item for item in received if item not in expected_ids]
    excluded = set(scope.get("excluded_ids") or [])
    if excluded.intersection(received) or excluded.intersection(expected_ids):
        raise ReviewError("排除题混入本次范围")
    if not missing and not extra:
        return []
    resolution = payload.get("batch_resolution") if isinstance(payload, dict) else None
    if not isinstance(resolution, dict):
        raise ReviewIncomplete("正式名单与提交不一致，须在 batch_resolution 中处置后再生成报告")
    if resolution.get("missing_ids") != missing or resolution.get("extra_ids") != extra:
        raise ReviewError("batch_resolution 列出的题号与实际差额不一致")
    reason = resolution.get("reason")
    if not isinstance(reason, str) or not reason.strip() or any(token in reason for token in UNCLEAR):
        raise ReviewError("批次差额的理由未写明，条件未明不能排除")
    decision = resolution.get("decision")
    if decision == "out-of-scope":
        if extra:
            raise ReviewIncomplete("提交中包含范围外题目，请移除后新建运行，避免进入验收分母")
        return []
    if decision != "in-scope-missing" or not missing or extra:
        raise ReviewError("批次差额须明确为范围内缺交，或确认不在本次范围")
    manifest = run / "_internal" / "batch-manifest.json"
    label = scope.get("label") if isinstance(scope.get("label"), str) and scope.get("label").strip() else "授权批次"
    return [{
        "instance_id": "batch:BATCH-MISSING:all",
        "clause_id": "BATCH-MISSING",
        "case_key": "batch",
        "task_id": "",
        "round": None,
        "title": "正式名单中的题目未提交",
        "text": "本次授权批次列出的题目应有对应提交。",
        "summary": "授权批次内没有对应提交: " + "、".join(missing),
        "observed": "未找到: " + "、".join(missing),
        "impact": "缺交题目没有交付内容可审。",
        "status": "fail",
        "progress": "completed",
        "action_results": [{"id": "compare", "detail": "对照正式名单与实际提交题号"}],
        "places": [{
            "document_id": "batch-scope", "filename": "批次名单", "title": "批次名单", "version": label,
            "section": "授权范围", "locator": "相关段落", "excerpt": reason.strip(), "unit_id": "scope",
            "unverified_extraction": False,
        }],
        "evidence": [{
            "root": "run", "path": "_internal/batch-manifest.json", "locator": "cases",
            "origin": "static", "missing": False, "sha256": sha(manifest), "case_key": "batch",
        }],
        "repair": {
            "problem": "授权批次内的题目没有提交",
            "location": "批次提交目录",
            "action": "补交 " + "、".join(missing) + "，或请甲方把不在本次范围的题号从名单移除",
            "done_when": "名单与提交一致后重新审查",
            "owner": "supplier_agent",
            "allowed_paths": ["."],
        },
    }]


def _comparison(run: Path, rows):
    prior_path = run / "_internal" / "comparisons" / "prior.json"
    if not prior_path.is_file():
        return
    baseline_path = run / "_internal" / "comparisons" / "baseline.json"
    if not baseline_path.is_file():
        raise ReviewError("对照前须先冻结首轮结果到 comparisons/baseline.json")
    if prior_path.stat().st_mtime < baseline_path.stat().st_mtime:
        raise ReviewError("首轮结果必须先于旧报告对照被冻结")
    prior = read_json(prior_path)
    if prior.get("schema") != "agent-review.comparison.v1" or prior.get("baseline_sha256") != sha(baseline_path):
        raise ReviewError("对照记录没有绑定已冻结的首轮结果")
    if "seen-prior-report" not in (prior.get("disclosures") or []):
        raise ReviewError("读取旧报告后必须披露本次会话已经见过旧结论")
    baseline = read_json(baseline_path)
    before = {item.get("instance_id"): item for item in baseline.get("clause_results") or []}
    after = {item["instance_id"]: item for item in rows}
    changed = [key for key in set(before) | set(after)
               if (before.get(key) or {}).get("status") != (after.get(key) or {}).get("status")
               or ((before.get(key) or {}).get("summary") or "") != ((after.get(key) or {}).get("summary") or "")]
    decided = {}
    for item in prior.get("dispositions") or []:
        if not isinstance(item, dict) or item.get("decision") not in {"maintain", "revise"}:
            raise ReviewError("对照处置必须写明维持或修正")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ReviewError("对照处置缺少理由")
        decided[item.get("instance_id")] = item
    missing = [key for key in changed if key not in decided]
    if missing:
        raise ReviewError("首轮结果与对照后的结论不一致，且没有逐条处置: " + "、".join(str(item) for item in missing[:8]))


def _model_change(run: Path, rows):
    path = run / "_internal" / "model-change.json"
    if not path.is_file():
        return
    data = read_json(path)
    if not isinstance(data.get("reason"), str) or not data["reason"].strip():
        raise ReviewError("更换主模型时必须记录原因")
    ids = data.get("rechecked_instance_ids")
    known = {row["instance_id"] for row in rows}
    if not isinstance(ids, list) or not ids or not set(ids).issubset(known):
        raise ReviewIncomplete("更换主模型后须列出并完成受影响结论的复核")


def _documents(catalog, extra):
    documents = [{
        "id": doc["id"], "title": doc["title"], "version": doc["version"],
        "unverified": doc["unverified_extraction"],
    } for doc in catalog["documents"]]
    if extra:
        documents.insert(0, {"id": "batch-scope", "title": "批次名单", "version": extra[0]["places"][0]["version"], "unverified": False})
    return documents


def status(run: Path) -> dict:
    run, state = _load(run)
    reports = [(run / name).is_file() for name in ("review.html", "repair.md")]
    if any(reports):
        if not all(reports):
            raise ReviewError("正式报告不完整，请保留本次运行并新建运行")
        metrics = read_json(run / "_internal" / "metrics.json")
        return {**metrics, "phase": "finalized", "formal_output": True,
                "human_report": str(run / "review.html"), "agent_report": str(run / "repair.md")}
    try:
        catalog, expected = _catalog(run, state)
    except ReviewIncomplete as exc:
        return {"run_id": state["run_id"], "phase": "blocked", "review_completed": False, "formal_output": False,
                "fingerprint_ok": True, "blockers": [{"reason": str(exc), "owner": "reviewer", "next_action": "补齐依据抽取或范围后再继续"}]}
    if catalog is None:
        return {"run_id": state["run_id"], "phase": "needs_catalog", "review_completed": False,
                "formal_output": False, "fingerprint_ok": True,
                "message": "已冻结输入。请根据 source-inventory.json 写入 catalog.json，再写入 results.json。"}
    payload = _results_payload(run) or {}
    rows = validate_results(expected, payload, state, run, final=False) if payload else []
    blockers = [{"instance_id": row["instance_id"], **row["blocker"]} for row in rows if row["progress"] == "blocked"]
    completed = sum(row["progress"] == "completed" for row in rows)
    scope = state.get("scope") or {}
    batch_scope = "single"
    if state["batch"]:
        batch_scope = "confirmed" if scope.get("expected_ids") else "unresolved"
    ready = bool(payload) and completed == len(expected) and not blockers and batch_scope != "unresolved"
    if ready:
        try:
            extra = _batch_rows(state, payload, run)
            _comparison(run, rows + extra)
            _model_change(run, rows + extra)
        except ReviewIncomplete as exc:
            ready = False
            blockers.append({"reason": str(exc), "owner": "reviewer", "next_action": "完成范围对账后再定稿"})
    virtual_rows, virtual_progress = [], {"enabled": False}
    if state.get("virtual_suite"):
        try:
            virtual_rows, virtual_progress = virtual.rows(run, state)
            blockers.extend(virtual_progress["blockers"])
            ready = ready and not virtual_progress["blockers"]
        except ReviewIncomplete as exc:
            ready = False
            virtual_progress = {"enabled": True, "expected": None, "completed": 0, "blockers": [{"reason": str(exc), "owner": "reviewer", "next_action": "官方首轮完成后 virtual-prepare"}]}
            blockers.extend(virtual_progress["blockers"])
    return {
        "run_id": state["run_id"], "phase": "ready_for_finalization" if ready else "review_in_progress",
        "review_completed": False, "formal_output": False, "fingerprint_ok": True,
        "expected": len(expected) + virtual_progress.get("expected", 0) if virtual_progress.get("expected", 0) is not None else None,
        "recorded": len(rows) + len(virtual_rows), "completed": completed + len(virtual_rows),
        "pass": sum(row["status"] == "pass" for row in rows + virtual_rows),
        "fail": sum(row["status"] == "fail" for row in rows + virtual_rows),
        "official": {**_metrics(rows), "expected": len(expected), "completed": completed},
        "blocked": len(blockers), "blockers": blockers, "batch_scope": batch_scope,
        "virtual_tests": virtual_progress,
        "message": "预期项目见 _internal/obligations.json。全部完成后再 finalize。",
    }


def finalize(run: Path) -> dict:
    run, state = _load(run)
    if (run / "review.html").exists() or (run / "repair.md").exists():
        raise ReviewError("本次运行已经生成正式报告，不能覆盖；请新建运行")
    if not (run / "_internal" / "catalog.json").is_file():
        raise ReviewIncomplete("尚未写入 catalog.json")
    catalog, expected = _catalog(run, state)
    payload = _results_payload(run)
    if payload is None:
        raise ReviewIncomplete("尚未写入 results.json；预期项目见 _internal/obligations.json")
    rows = validate_results(expected, payload, state, run, final=True)
    rows.extend(_batch_rows(state, payload, run))
    _comparison(run, rows)
    _model_change(run, rows)
    _verify(state)
    official_rows = list(rows)
    virtual_rows, virtual_progress = virtual.rows(run, state, final=True)
    rows.extend(virtual_rows)
    state["document_order"] = _documents(catalog, [row for row in rows if row["clause_id"] == "BATCH-MISSING"])
    if state.get("virtual_suite"):
        suite = read_json(run / "_internal" / "virtual" / "suite.json")
        state["document_order"].append({"id": "virtual-suite", "title": suite["label"], "version": suite["version"], "unverified": False})
        state["official_rows"] = official_rows
    html, repair = render_html(state, rows), render_repair(state, rows)
    verdict = verdict_of(rows)
    metrics = {
        "schema": "agent-review.metrics.v1", "run_id": state["run_id"], "verdict": verdict,
        "review_completed": True, "total": len(rows),
        "pass": sum(row["status"] == "pass" for row in rows),
        "fail": sum(row["status"] == "fail" for row in rows),
        "completion_rate": 1 if rows else None,
        "pass_rate": (sum(row["status"] == "pass" for row in rows) / len(rows)) if rows else None,
        "instances": [{"instance_id": row["instance_id"], "status": row["status"], "clause_id": row["clause_id"],
                       "task_id": row["task_id"], "round": row["round"]} for row in rows],
    }
    if state.get("virtual_suite"):
        metrics.update(official=_metrics(official_rows), virtual=_metrics(virtual_rows),
                       conclusion_kind="internal_combined_with_virtual_tests", human_notice=virtual.WARNING,
                       validation_only=virtual_progress["validation_only"])
    write_json(run / "_internal" / "metrics.json", metrics)
    atomic_text(run / "review.html", html)
    atomic_text(run / "repair.md", repair)
    return {"verdict": verdict, "review_completed": True, "completion_rate": metrics["completion_rate"],
            "pass_rate": metrics["pass_rate"], "human_report": str(run / "review.html"),
            "agent_report": str(run / "repair.md"), "run_id": state["run_id"],
            **({"human_notice": virtual.WARNING, "official": metrics["official"], "virtual": metrics["virtual"]} if state.get("virtual_suite") else {})}


def _metrics(rows):
    return {"total": len(rows), "pass": sum(row["status"] == "pass" for row in rows),
            "verdict": verdict_of(rows), "pass_rate": sum(row["status"] == "pass" for row in rows) / len(rows) if rows else None}


def virtual_prepare(run: Path):
    run, state = _load(run)
    catalog, expected = _catalog(run, state)
    if catalog is None or _results_payload(run) is None:
        raise ReviewIncomplete("先完成官方依据的首轮清单和逐项判断")
    payload = _results_payload(run)
    rows = validate_results(expected, payload, state, run, final=True)
    rows.extend(_batch_rows(state, payload, run))
    _comparison(run, rows)
    _model_change(run, rows)
    return virtual.prepare(run, state, payload)


def virtual_execute(run: Path, plan: Path):
    run, state = _load(run)
    return virtual.execute(run, state, plan)


def virtual_collect(run: Path):
    run, state = _load(run)
    return virtual.collect(run, state)


def execute(run: Path, plan: Path) -> dict:
    run, state = _load(run)
    return run_plan(run, plan, state)
