"""Versioned hypothetical tests, blind packets and evidence-bound collection.

This checks receipt consistency, not the authenticity of a host or its isolation.
Expected answers live outside packets. The host must supply fresh blind sessions.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from .errors import ReviewError, ReviewIncomplete
from .io import confined, fingerprint, read_json, same_tree, sha, write_json

WARNING = "包含虚拟测试集及推测判断。对外使用前，请人工核实依据、适用性、实际证据及措辞。"
CHECKLIST = ("核实反馈事实与推测扩展", "核实本题、本轮及测试目标的适用性",
             "核实实际执行、盲审会话及证据", "核实缺陷归属和对外措辞；不得直接转发综合结论")
TARGETS = {"scorer", "agent_review"}
KINDS = {"infrastructure", "unparseable", "ambiguity", "capability"}


def require(value, message):
    if not value:
        raise ReviewError(message)


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label}须为非空文本")
    return value.strip()


def _open(run):
    require(not any((run / name).exists() for name in ("review.html", "repair.md")), "已定稿，不能补做虚拟测试")


def attach(path: Path, run: Path):
    path = path.resolve()
    suite = read_json(path)
    require(isinstance(suite, dict) and suite.get("schema") == "agent-review.virtual-suite.v1", "虚拟套件 schema 无效")
    for key in ("id", "version", "label"):
        text(suite.get(key), key)
    require(suite["label"].startswith("虚拟测试集"), "套件名称必须以虚拟测试集开头")
    require(isinstance(suite.get("task_ids"), list) and suite["task_ids"]
            and all(isinstance(i, str) and i.strip() for i in suite["task_ids"]), "套件须限定 task_ids")
    sources = suite.get("sources")
    require(isinstance(sources, list) and sources, "套件须登记反馈来源")
    records = []
    for source in sources:
        require(isinstance(source, dict), "来源须为对象")
        source_path = (path.parent / text(source.get("path"), "来源路径")).resolve()
        require(source_path.is_file() and sha(source_path) == source.get("sha256"), "虚拟套件来源哈希不符")
        text(source.get("locator"), "来源定位")
        records.append({"path": str(source_path), "sha256": source["sha256"]})
    cases = suite.get("cases")
    require(isinstance(cases, list) and cases, "套件须有固定用例")
    seen = set()
    for case in cases:
        require(isinstance(case, dict), "虚拟用例须为对象")
        cid = text(case.get("id"), "用例编号")
        require(cid not in seen and ":" not in cid, "虚拟用例编号重复或含冒号")
        seen.add(cid)
        for key in ("title", "family", "feedback_fact", "hypothesis", "applicability", "action", "rationale", "source_locator", "requirements"):
            text(case.get(key), key)
        require(case.get("origin") in {"feedback_reconstruction", "hypothesis_extension"}, "须区分反馈重建与推测扩展")
        require(isinstance(case.get("rounds"), list) and case["rounds"]
                and len(set(case["rounds"])) == len(case["rounds"])
                and all(i in {"R1", "R2", "R3"} for i in case["rounds"]), "用例轮次无效")
        require(isinstance(case.get("targets"), list) and case["targets"]
                and len(set(case["targets"])) == len(case["targets"])
                and set(case["targets"]) <= TARGETS, "测试目标无效")
        material = case.get("materials")
        require(isinstance(material, dict) and material and all(isinstance(v, str) for v in material.values()), "用例须提供文本/SVG重建材料")
        for name in material:
            require(name not in {"task.json", "requirements.md"}, "材料不能覆盖盲审任务")
            confined(run / "_internal" / "virtual" / "path-check", name)
        require(case.get("expected") in {"pass", "fail", "unknown"}, "用例预期无效")
        if case["expected"] == "unknown":
            require(case.get("unknown_kind") in KINDS, "预期 unknown 须注明原因类别")
    dest = run / "_internal" / "virtual"
    dest.mkdir(parents=True)
    write_json(dest / "suite.json", suite)
    return {"path": str(path), "sha256": sha(path), "sources": records,
            "frozen_sha256": sha(dest / "suite.json"), "validation_only": suite.get("validation_only") is True}


def verify(run, state):
    record = state.get("virtual_suite")
    if not record:
        return
    for item in [{"path": record["path"], "sha256": record["sha256"]}, *record["sources"]]:
        require(Path(item["path"]).is_file() and sha(Path(item["path"])) == item["sha256"], "虚拟套件或反馈来源发生变化，请新建运行")
    require(sha(run / "_internal" / "virtual" / "suite.json") == record["frozen_sha256"], "冻结虚拟套件发生变化")


def prepare(run, state, baseline):
    _open(run)
    require(state.get("virtual_suite"), "本运行未启用虚拟套件")
    folder = run / "_internal" / "virtual"
    if (folder / "manifest.json").exists():
        raise ReviewError("虚拟任务已冻结，不能重新生成或更换预期")
    suite = read_json(folder / "suite.json")
    relevant = [case for case in state["cases"] if case["task_id"] in suite["task_ids"]]
    require(relevant, "虚拟套件不适用于本次题号")
    (folder / "baseline.json").write_bytes((run / "_internal" / "results.json").read_bytes())
    scope_path = folder / "scope.json"
    scope = read_json(scope_path) if scope_path.exists() else {}
    require(isinstance(scope, dict), "虚拟范围须为对象")
    exclusions = scope.get("excluded", {})
    require(isinstance(exclusions, dict), "excluded 须为实例编号到理由的对象")
    instances = []
    known = set()
    for subject in relevant:
        for case in suite["cases"]:
            for rnd in case["rounds"]:
                for target in case["targets"]:
                    iid = f"virtual:{subject['key']}:{case['id']}:{rnd}:{target}"
                    known.add(iid)
                    if iid in exclusions:
                        entry = exclusions[iid]
                        require(isinstance(entry, dict), "排除须写明理由及证据")
                        reason = text(entry.get("reason"), "排除理由")
                        require(not any(word in reason for word in ("未知", "未明", "待定", "环境不足", "无法执行")), "未知或执行受阻不能排除")
                        _proof(entry.get("evidence"), run)
                        continue
                    token = uuid4().hex
                    packet = folder / "packets" / token
                    packet.mkdir(parents=True)
                    for name, content in case["materials"].items():
                        p = confined(packet, name)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(content, encoding="utf-8")
                    (packet / "requirements.md").write_text(case["requirements"], encoding="utf-8")
                    write_json(packet / "task.json", {
                        "schema": "agent-review.blind-task.v1", "packet_id": token,
                        "round": rnd, "task": "按 requirements.md 阅读实际材料，判断符合、不符合或无法判断；给出定位和理由。不得读取任务包以外的套件或预期答案。",
                        "material_kind": "虚拟测试集的重建/合成材料，题设不冒充甲方官方原文",
                        "response": "verdict: pass/fail/unknown; reason; evidence: [{path, sha256, locator}]; unknown_kind（unknown时）",
                    })
                    instances.append({"instance_id": iid, "case_key": subject["key"], "task_id": subject["task_id"],
                                      "round": rnd, "target": target, "test_id": case["id"],
                                      "packet": fingerprint(packet)})
    require(set(exclusions) <= known, "排除清单包含未知实例")
    manifest = {"schema": "agent-review.virtual-manifest.v1", "run_id": state["run_id"],
                "suite_sha256": sha(folder / "suite.json"), "baseline_sha256": sha(folder / "baseline.json"),
                "catalog_sha256": sha(run / "_internal" / "catalog.json"),
                "exclusions": exclusions, "instances": instances}
    write_json(folder / "manifest.json", manifest)
    write_json(folder / "lock.json", {"manifest_sha256": sha(folder / "manifest.json")})
    return {"instances": len(instances), "excluded": len(exclusions), "manifest": str(folder / "manifest.json"), "human_notice": WARNING}


def manifest(run, state):
    verify(run, state)
    folder = run / "_internal" / "virtual"
    if not (folder / "manifest.json").is_file():
        raise ReviewIncomplete("尚未 virtual-prepare，官方首轮完成后生成虚拟任务")
    data = read_json(folder / "manifest.json")
    require(sha(folder / "manifest.json") == read_json(folder / "lock.json")["manifest_sha256"], "虚拟任务清单发生变化")
    require(sha(folder / "baseline.json") == data["baseline_sha256"], "官方首轮冻结结果发生变化")
    require(sha(folder / "suite.json") == data["suite_sha256"], "虚拟预期发生变化")
    # Official revisions after exposure require a new run; no silent answer tuning.
    require(sha(run / "_internal" / "results.json") == data["baseline_sha256"], "虚拟测试启动后官方判断变化，请新建运行")
    require(sha(run / "_internal" / "catalog.json") == data["catalog_sha256"], "虚拟测试启动后官方清单变化，请新建运行")
    for instance in data["instances"]:
        same_tree(instance["packet"], Path(instance["packet"]["path"]))
    for entry in data["exclusions"].values():
        _proof(entry["evidence"], run)
    return data


def _proof(raw, base):
    require(isinstance(raw, dict), "证据须为对象")
    path = confined(base, raw.get("path"))
    require(path.is_file() and sha(path) == raw.get("sha256"), "虚拟证据文件或哈希不符")
    return {"path": path.relative_to(base).as_posix(), "sha256": sha(path), "locator": text(raw.get("locator"), "证据定位")}


def execute(run, state, plan_path):
    from .execute import execute as host_execute
    _open(run)
    data = manifest(run, state)
    plan = read_json(plan_path)
    require(isinstance(plan, dict) and plan.get("schema") == "agent-review.virtual-execution-plan.v1", "虚拟执行计划 schema 无效")
    item = next((i for i in data["instances"] if i["instance_id"] == plan.get("instance_id")), None)
    require(item and item["target"] == "scorer", "虚拟执行计划须对应评分器实例")
    pointer = plan.get("result_pointer", ["verdict"])
    require(isinstance(pointer, list) and all(isinstance(k, str) for k in pointer), "result_pointer 须为字段路径数组")
    values = plan.get("value_map", {"pass": "pass", "fail": "fail", "unknown": "unknown"})
    require(isinstance(values, dict) and values and all(v in {"pass", "fail", "unknown"} for v in values.values()), "value_map 映射无效")
    require(all(values.get(k, k) == k for k in ("pass", "fail", "unknown")), "适配器不能翻转标准结论")
    subject = next(c for c in state["cases"] if c["key"] == item["case_key"])
    staging = run / "_internal" / "virtual" / "staging" / uuid4().hex
    shutil.copytree(Path(subject["submission"]["path"]), staging)
    require(not (staging / "_virtual_input").exists(), "交付已占用 _virtual_input，请调整交付副本")
    shutil.copytree(Path(item["packet"]["path"]), staging / "_virtual_input")
    forwarded = {k: plan[k] for k in ("authorization", "reviewer_id", "review_reason", "command", "timeout_seconds", "repeats", "result_file") if k in plan}
    require(isinstance(forwarded.get("result_file"), str) and forwarded["result_file"], "虚拟执行必须指定 result_file")
    forwarded.update(schema="agent-review.execution-plan.v1", case_key=item["case_key"])
    saved_plan = staging.parent / (staging.name + ".json")
    write_json(saved_plan, forwarded)
    copied_state = {**state, "cases": [{**subject, "submission": {"path": str(staging)}}]}
    receipt = host_execute(run, saved_plan, copied_state)
    folder = run / "_internal" / "executions" / receipt["execution_id"]
    receipt_path = folder / "receipt.json"
    receipt["virtual_binding"] = {"instance_id": item["instance_id"], "packet_sha256": item["packet"]["sha256"],
                                  "result_file": forwarded["result_file"], "result_pointer": pointer, "value_map": values}
    write_json(receipt_path, receipt)
    records = []
    for attempt in receipt["repeats"]:
        output = folder / f"repeat-{attempt['repeat']}" / forwarded["result_file"]
        record = {"schema": "agent-review.virtual-observation.v1", "instance_id": item["instance_id"],
                  "packet_sha256": item["packet"]["sha256"], "kind": "execution", "reason": "本次评分器副本执行",
                  "receipt": {"path": receipt_path.relative_to(run).as_posix(), "sha256": sha(receipt_path), "locator": f"repeat {attempt['repeat']}"},
                  "attempt": attempt["repeat"], "result_pointer": pointer, "value_map": values,
                  "result_file": output.relative_to(run).as_posix()}
        records.append(record)
    history = run / "_internal" / "virtual" / "observations" / (receipt["execution_id"] + ".json")
    write_json(history, {"observations": records})
    manifest(run, state)
    return {"receipt": str(receipt_path), "observations": str(history), "repeats": len(records)}


def _observation(raw, item, run, state, sessions):
    require(raw.get("packet_sha256") == item["packet"]["sha256"], "观察未绑定冻结盲审包")
    if item["target"] == "scorer":
        require(raw.get("kind") == "execution", "评分器结果必须来自实际副本执行回执")
        proof = _proof(raw.get("receipt"), run)
        receipt_path = confined(run, proof["path"])
        receipt = read_json(receipt_path)
        require(receipt.get("schema") == "agent-review.execution-receipt.v1"
                and receipt.get("case_key") == item["case_key"], "执行回执身份不符")
        binding = receipt.get("virtual_binding") or {}
        require(binding.get("instance_id") == item["instance_id"] and binding.get("packet_sha256") == item["packet"]["sha256"], "执行回执未绑定虚拟实例")
        require(binding.get("result_pointer") == raw.get("result_pointer") and binding.get("value_map") == raw.get("value_map"), "观察不能修改已审阅的适配映射")
        attempt = next((a for a in receipt["repeats"] if a["repeat"] == raw.get("attempt")), None)
        require(attempt, "执行轮次不存在")
        if attempt["timed_out"] or attempt["exit_code"] != 0:
            return None, "infrastructure", proof, "评分器超时或进程错误，须追查环境或能力原因"
        result_file = confined(run, raw.get("result_file"))
        workspace = receipt_path.parent / f"repeat-{attempt['repeat']}"
        require(result_file == confined(workspace, binding["result_file"]), "结果必须来自回执绑定的执行副本")
        require(fingerprint(workspace / "_virtual_input")["sha256"] == item["packet"]["sha256"], "评分器执行时测试材料变化")
        if not result_file.exists() and attempt["result_sha256"] is None:
            return None, "unparseable", proof, "评分器没有产生约定结果文件"
        if not result_file.is_file() or attempt["result_sha256"] != sha(result_file):
            raise ReviewError("执行结果文件或哈希不符")
        try:
            payload = read_json(result_file)
        except ReviewError:
            return None, "unparseable", proof, "评分器输出不是可读取的 JSON，须追查解析原因"
        observed = payload
        try:
            for key in raw["result_pointer"]:
                observed = observed[key]
            verdict = raw["value_map"][str(observed).lower() if isinstance(observed, bool) else str(observed)]
        except (KeyError, TypeError):
            return None, "unparseable", proof, "适配器输出未识别，不能默认通过或失败"
        return verdict, payload.get("unknown_kind") if isinstance(payload, dict) else None, proof, text(raw.get("reason"), "执行理由")
    require(raw.get("kind") == "blind_session", "Agent 自测必须来自新会话盲审")
    proof = _proof(raw.get("actor_record"), run)
    actor = read_json(confined(run, proof["path"]))
    require(actor.get("schema") == "agent-review.blind-actor.v1", "盲审会话记录 schema 无效")
    sid = text(actor.get("session_id"), "实际会话标识")
    require(sid not in sessions, "每个虚拟实例必须使用不同的新会话")
    sessions.add(sid)
    require(actor.get("inherited_context") is False and actor.get("expected_answers_visible") is False, "盲审不能继承历史或读取预期答案")
    require(actor.get("packet_sha256") == item["packet"]["sha256"], "会话记录未绑定盲审包")
    require(actor.get("simulated") is False or state["virtual_suite"]["validation_only"], "模拟会话不能作为真实业务盲审")
    _proof(actor.get("transcript"), run)
    judgment_proof = _proof(raw.get("judgment"), run)
    judgment = read_json(confined(run, judgment_proof["path"]))
    require(actor.get("judgment_sha256") == judgment_proof["sha256"], "会话记录与判断不一致")
    verdict = judgment.get("verdict")
    require(verdict in {"pass", "fail", "unknown"}, "盲审判断无效")
    evidence = judgment.get("evidence")
    require(isinstance(evidence, list) and evidence, "盲审判断须定位实际材料")
    for entry in evidence:
        _proof(entry, Path(item["packet"]["path"]))
    reason = text(judgment.get("reason"), "盲审理由")
    return verdict, judgment.get("unknown_kind"), judgment_proof, reason


def rows(run, state, *, final=False):
    if not state.get("virtual_suite"):
        return [], {"enabled": False}
    data = manifest(run, state)
    suite = read_json(run / "_internal" / "virtual" / "suite.json")
    cases = {case["id"]: case for case in suite["cases"]}
    known = {i["instance_id"] for i in data["instances"]}
    observations = {}
    resolution_file = run / "_internal" / "virtual" / "resolutions.json"
    resolutions = read_json(resolution_file).get("isolated", {}) if resolution_file.exists() else {}
    require(isinstance(resolutions, dict), "隔离处置须为按观察文件路径索引的对象")
    for path in sorted((run / "_internal" / "virtual" / "observations").glob("*.json")):
        payload = read_json(path)
        require(isinstance(payload, dict) and isinstance(payload.get("observations"), list), "观察清单须包含 observations 数组")
        for record in payload["observations"]:
            require(isinstance(record, dict) and record.get("instance_id") in known, "观察包含未知虚拟实例")
            observations.setdefault(record["instance_id"], []).append({**record, "_file": path.relative_to(run).as_posix(), "_sha256": sha(path)})
    found, blockers, sessions = [], [], set()
    for item in data["instances"]:
        case = cases[item["test_id"]]
        records = observations.get(item["instance_id"], [])
        decisions = []
        evidence = []
        reasons = []
        unresolved = None
        for record in records:
            verdict, kind, proof, reason = _observation(record, item, run, state, sessions)
            evidence.append({"root": "run", **proof, "origin": "measured", "missing": False, "case_key": item["case_key"]})
            reasons.append(f"{verdict or 'blocked'} ({kind or '未分类'}): {reason}")
            if verdict is None or (verdict == "unknown" and not (case["expected"] == "unknown" and kind == case.get("unknown_kind"))):
                resolution = resolutions.get(record["_file"])
                if resolution and kind in {"infrastructure", "unparseable", "ambiguity"}:
                    require(isinstance(resolution, dict) and resolution.get("sha256") == record["_sha256"], "隔离记录未绑定原始观察")
                    text(resolution.get("reason"), "隔离原因")
                    proof = _proof(resolution.get("evidence"), run)
                    evidence.append({"root": "run", **proof, "origin": "static", "missing": False, "case_key": item["case_key"]})
                    reasons.append("已隔离原始环境/解析/歧义故障：" + resolution["reason"])
                else:
                    unresolved = reason if verdict is None else "unknown 原因须先追查；不能直接转成乙方失败"
            else:
                decisions.append(verdict == case["expected"])
        if not decisions or unresolved:
            blockers.append({"instance_id": item["instance_id"], "reason": unresolved or "尚未执行或提交新会话盲审", "owner": "reviewer", "next_action": "补齐实际回执和原因后 virtual-collect"})
            continue
        status = "pass" if all(decisions) else "fail"
        label = "评分器" if item["target"] == "scorer" else "Agent Review"
        detail = f"目标：{label}；预期判断：{case['expected']}；实际：" + "；".join(reasons)
        place = {"document_id": "virtual-suite", "filename": suite["label"], "title": suite["label"], "version": suite["version"],
                 "section": "【虚拟测试集】" + case["family"], "locator": case["source_locator"], "unit_id": item["test_id"],
                 "excerpt": f"反馈事实：{case['feedback_fact']}\n推测：{case['hypothesis']}\n预期理由：{case['rationale']}", "unverified_extraction": False}
        found.append({**item, "clause_id": item["test_id"], "title": "【虚拟测试集】" + case["title"] + f"（{label}）",
                      "virtual": True, "origin_class": case["origin"], "expected": case["expected"], "observed": detail,
                      "text": case["action"], "summary": "预期匹配" if status == "pass" else "测试发现漏判或误拒；不指称原件含植入错误",
                      "impact": "影响内部综合审查；不代表甲方正式隐藏测试结论", "status": status, "progress": "completed",
                      "places": [place], "evidence": evidence, "action_results": [{"id": "virtual", "detail": detail}],
                      "repair": {"owner": "supplier_agent" if item["target"] == "scorer" else "reviewer",
                                 "problem": detail, "location": label + "判断逻辑", "action": "核实预期与证据后修复对应判断逻辑，保留原始材料及首次结果",
                                 "done_when": "同一冻结样例及合法替代、错误对照均得到正确处置",
                                 "allowed_paths": ["仅经核实的评分器实现"] if item["target"] == "scorer" else ["Agent Review 规则或实现"]}})
    if final and blockers:
        raise ReviewIncomplete(f"虚拟测试仍有 {len(blockers)} 项未完成或受阻")
    return found, {"enabled": True, "expected": len(data["instances"]), "completed": len(found), "blocked": len(blockers),
                   "blockers": blockers, "excluded": len(data["exclusions"]), "validation_only": state["virtual_suite"]["validation_only"]}


def collect(run, state):
    _open(run)
    found, progress = rows(run, state)
    write_json(run / "_internal" / "virtual" / "results.json", {"schema": "agent-review.virtual-results.v1", "rows": found, "progress": progress})
    return {**progress, "human_notice": WARNING}
