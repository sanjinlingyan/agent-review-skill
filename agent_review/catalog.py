"""Validate the agent's reading of the official text and rebuild obligations."""
from __future__ import annotations

import re

from .errors import ReviewError, ReviewIncomplete
from .inventory import section_title

LOOSE = ("建议", "可以", "允许", "或者", "或", "等")
TIGHT = ("必须", "全部", "且")
UNCLEAR = re.compile(r"未明|待定|不清楚|未知|无法确定")
ROUND = re.compile(r"^R\d+$")


def _loose(text: str) -> list[str]:
    cleaned = text.replace("等待", "").replace("等于", "")
    return [token for token in LOOSE if token in cleaned]


def _tight(text: str) -> list[str]:
    return [token for token in TIGHT if token in text]


def _require(condition, message):
    if not condition:
        raise ReviewError(message)


def _text(value, label):
    _require(isinstance(value, str) and value.strip(), f"{label}必须是非空文本")
    return value.strip()


def validate_catalog(inventory: dict, catalog: dict) -> dict:
    _require(isinstance(catalog, dict) and catalog.get("schema") == "agent-review.catalog.v1", "catalog.json 须为 agent-review.catalog.v1")
    incoming = catalog.get("documents")
    _require(isinstance(incoming, list) and incoming, "catalog.documents 必须列出全部依据文件")
    by_id = {doc["id"]: doc for doc in inventory["documents"]}
    _require(len(by_id) == len(inventory["documents"]), "依据文件编号重复")
    seen = []
    normalized = []
    for entry in incoming:
        _require(isinstance(entry, dict), "依据文件条目必须是对象")
        doc_id = entry.get("id")
        _require(doc_id in by_id and doc_id not in seen, f"依据文件编号无效或重复: {doc_id}")
        seen.append(doc_id)
        source = by_id[doc_id]
        title = _text(entry.get("title"), f"{doc_id} 标题")
        version = _text(entry.get("version"), f"{doc_id} 版本")
        if source["kind"] == "asset":
            _require(entry.get("role") == "asset", f"{source['path']} 是附件，须标明 role=asset 并说明用途")
            _text(entry.get("reason"), f"{source['path']} 的附件说明")
            _require(not entry.get("units"), "附件不包含验收条款")
            normalized.append({**source, "title": title, "version": version, "units": [], "unverified_extraction": False})
            continue
        paragraphs = list(source["paragraphs"])
        unverified = False
        if source["kind"] == "pdf":
            extracted = entry.get("paragraphs")
            if not isinstance(extracted, list) or not extracted:
                raise ReviewIncomplete(f"{source['path']} 尚未抽取，不能完成依据对账")
            paragraphs = []
            for index, para in enumerate(extracted, 1):
                _require(isinstance(para, dict), "PDF 段落必须是对象")
                text = _text(para.get("text"), "PDF 段落")
                page = para.get("page")
                _require(isinstance(page, int) and page > 0, "PDF 段落须有页码")
                paragraphs.append({"id": f"g{index}", "block_ids": [f"p{page}-l{index}"], "text": text, "page": page})
            unverified = True
        units = _units(entry.get("units"), paragraphs, source["path"])
        normalized.append({
            **source, "title": title, "version": version, "paragraphs": paragraphs,
            "units": units, "unverified_extraction": unverified,
        })
    missing = [doc["path"] for doc in inventory["documents"] if doc["id"] not in seen]
    _require(not missing, "依据文件未进入清单: " + "、".join(missing))
    clauses = _clauses(catalog.get("clauses"), normalized)
    _audits(catalog.get("coverage_audits"), [doc["id"] for doc in normalized])
    _fidelity(clauses, normalized, catalog.get("fidelity_overrides") or [])
    return {"documents": normalized, "clauses": clauses}


def _units(raw, paragraphs, filename):
    _require(isinstance(raw, list) and raw, f"{filename} 缺少语义单元")
    para_ids = [item["id"] for item in paragraphs]
    positions = {item["id"]: index for index, item in enumerate(paragraphs)}
    by_para = {item["id"]: item for item in paragraphs}
    covered, units, last = set(), [], -1
    for unit in raw:
        _require(isinstance(unit, dict), "语义单元必须是对象")
        unit_id = _text(unit.get("id"), "语义单元编号")
        chosen = unit.get("paragraph_ids")
        _require(isinstance(chosen, list) and chosen, f"{unit_id} 必须覆盖完整段落")
        _require(all(item in positions for item in chosen), f"{unit_id} 引用了不存在的段落；换行不能拆成新段落")
        indexes = [positions[item] for item in chosen]
        _require(indexes == list(range(indexes[0], indexes[-1] + 1)), f"{unit_id} 必须覆盖连续的整段")
        _require(indexes[0] > last, "语义单元必须按原文顺序排列")
        last = indexes[-1]
        overlap = covered.intersection(chosen)
        _require(not overlap, "同一段落不能重复归入多个单元: " + "、".join(sorted(overlap)))
        covered.update(chosen)
        relevance = unit.get("relevance")
        _require(relevance in {"applicable", "context", "excluded"}, f"{unit_id} 的 relevance 无效")
        reason = _text(unit.get("reason"), f"{unit_id} 的范围理由")
        if relevance == "excluded" and UNCLEAR.search(reason):
            raise ReviewError(f"{unit_id} 条件未明，不能排除出审查范围")
        paras = [by_para[item] for item in chosen]
        units.append({
            "id": unit_id,
            "paragraph_ids": chosen,
            "paragraphs": paras,
            "summary": _text(unit.get("summary"), f"{unit_id} 摘要"),
            "relevance": relevance,
            "reason": reason,
            "section": section_title(paras, unit.get("section_title")),
        })
    uncovered = [item for item in para_ids if item not in covered]
    _require(not uncovered, f"{filename} 仍有段落未覆盖: " + "、".join(uncovered))
    return units


def _clauses(raw, documents):
    _require(isinstance(raw, list), "clauses 必须是数组")
    units = {}
    for doc in documents:
        for unit in doc["units"]:
            units[unit["id"]] = (doc, unit)
    found, seen = [], set()
    for clause in raw:
        _require(isinstance(clause, dict), "条款必须是对象")
        clause_id = _text(clause.get("id"), "条款编号")
        _require(clause_id not in seen, f"条款编号重复: {clause_id}")
        seen.add(clause_id)
        strength = clause.get("strength")
        scope = clause.get("scope")
        _require(strength in {"required", "conditional", "recommendation"}, f"{clause_id} 的强度无效")
        _require(scope in {"case", "batch"}, f"{clause_id} 的范围无效")
        chosen = clause.get("unit_ids")
        _require(isinstance(chosen, list) and chosen and all(item in units for item in chosen), f"{clause_id} 必须引用已覆盖的语义单元")
        for doc in documents:
            ids = [unit["id"] for unit in doc["units"]]
            picked = [item for item in chosen if item in ids]
            if not picked:
                continue
            positions = [ids.index(item) for item in picked]
            _require(positions == list(range(positions[0], positions[-1] + 1)), f"{clause_id} 的出处必须连续")
        applicability = clause.get("applicability")
        _require(isinstance(applicability, dict), f"{clause_id} 缺少适用性")
        decision = applicability.get("decision")
        _require(decision in {"included", "excluded"}, f"{clause_id} 的适用性只能是 included 或 excluded")
        reason = _text(applicability.get("reason"), f"{clause_id} 的适用性理由")
        _text(applicability.get("basis"), f"{clause_id} 的适用性依据")
        if decision == "excluded" and UNCLEAR.search(reason):
            raise ReviewError(f"{clause_id} 条件未明，不能排除")
        cited = [units[item][1] for item in chosen]
        if strength == "recommendation":
            _require(all(unit["relevance"] != "applicable" for unit in cited), f"{clause_id} 把适用义务写成了建议")
        if decision == "included" and strength in {"required", "conditional"}:
            _require(any(unit["relevance"] == "applicable" for unit in cited), f"{clause_id} 没有适用的原文单元")
        condition = clause.get("condition")
        if strength == "conditional" and decision == "included":
            _text(condition, f"{clause_id} 的触发条件")
        actions = clause.get("actions")
        _require(isinstance(actions, list) and actions, f"{clause_id} 缺少审查动作")
        cleaned_actions = []
        for action in actions:
            _require(isinstance(action, dict), "审查动作必须是对象")
            cleaned_actions.append({"id": _text(action.get("id"), "动作编号"), "text": _text(action.get("text"), "动作说明")})
        _require(len({item["id"] for item in cleaned_actions}) == len(cleaned_actions), f"{clause_id} 的动作编号重复")
        rounds = clause.get("rounds") or []
        _require(isinstance(rounds, list) and all(isinstance(item, str) and ROUND.fullmatch(item) for item in rounds), f"{clause_id} 的轮次无效")
        if scope == "batch":
            rounds = []
        found.append({
            "id": clause_id,
            "title": _text(clause.get("title"), f"{clause_id} 标题"),
            "text": _text(clause.get("text"), f"{clause_id} 要求"),
            "accept": _text(clause.get("accept"), f"{clause_id} 通过条件"),
            "strength": strength,
            "scope": scope,
            "rounds": rounds,
            "actions": cleaned_actions,
            "evidence_required": _text(clause.get("evidence_required"), f"{clause_id} 的证据要求"),
            "unit_ids": chosen,
            "decision": decision,
            "condition": condition if isinstance(condition, str) else "",
        })
    applicable = [unit["id"] for doc in documents for unit in doc["units"] if unit["relevance"] == "applicable"]
    cited = {unit_id for clause in found if clause["decision"] == "included" and clause["strength"] != "recommendation" for unit_id in clause["unit_ids"]}
    hanging = [unit_id for unit_id in applicable if unit_id not in cited]
    _require(not hanging, "适用段落没有对应的必要义务: " + "、".join(hanging))
    return found


def _audits(raw, document_ids):
    _require(isinstance(raw, list) and len(raw) >= 2, "须记录两遍原文核对，第二遍从原文反查")
    reviewers = set()
    passes = set()
    reverse = False
    for audit in raw:
        _require(isinstance(audit, dict), "核对记录必须是对象")
        reviewers.add(_text(audit.get("reviewer"), "核对人"))
        passes.add(audit.get("pass"))
        method = _text(audit.get("method"), "核对方法")
        if "反查" in method or "reverse" in method.lower():
            reverse = True
        ids = audit.get("document_ids")
        _require(isinstance(ids, list) and set(ids) == set(document_ids), "每一遍核对都要覆盖全部依据文件")
    _require(len(reviewers) == 1, "两遍核对是同一审查 Agent 完成的，不能记成另一位独立审查者")
    _require(reverse and passes.issuperset({1, 2}), "核对记录须包含第一遍分类和第二遍原文反查")


def _fidelity(clauses, documents, overrides):
    _require(isinstance(overrides, list), "fidelity_overrides 必须是数组")
    units = {unit["id"]: unit for doc in documents for unit in doc["units"]}
    by_clause = {}
    for item in overrides:
        _require(isinstance(item, dict), "忠实度修正必须是对象")
        clause_id = _text(item.get("clause_id"), "修正条款")
        added = item.get("added") or []
        weakened = bool(item.get("weakened"))
        _require(isinstance(added, list), "added 必须是数组")
        _text(item.get("basis"), f"{clause_id} 的修正依据")
        by_clause.setdefault(clause_id, {"added": set(), "weakened": False})
        by_clause[clause_id]["added"].update(added)
        by_clause[clause_id]["weakened"] = by_clause[clause_id]["weakened"] or weakened
    for clause in clauses:
        source = "\n".join(para["text"] for unit_id in clause["unit_ids"] for para in units[unit_id]["paragraphs"])
        claim = clause["text"] + "\n" + clause["accept"]
        loose, tight, source_tight = _loose(source), _tight(claim), _tight(source)
        extra = [token for token in tight if token not in source_tight]
        note = by_clause.get(clause["id"], {"added": set(), "weakened": False})
        if loose and extra and not set(extra).issubset(note["added"]):
            raise ReviewError(f"{clause['id']} 把“{'、'.join(loose)}”收紧成了“{'、'.join(extra)}”，须按原文改回或写明裁决依据")
        if "必须" in source and clause["strength"] == "recommendation" and not note["weakened"]:
            raise ReviewError(f"{clause['id']} 把原文的必要义务写成了建议")


def instances(catalog: dict, cases: list[dict]) -> list[dict]:
    units = {unit["id"]: (doc, unit) for doc in catalog["documents"] for unit in doc["units"]}
    found = []
    for clause in catalog["clauses"]:
        if clause["decision"] != "included" or clause["strength"] == "recommendation":
            continue
        places = []
        for unit_id in clause["unit_ids"]:
            doc, unit = units[unit_id]
            pages = [para["page"] for para in unit["paragraphs"] if para.get("page")]
            if not pages:
                locator = "相关段落"
            elif pages[0] == pages[-1]:
                locator = f"第 {pages[0]} 页"
            else:
                locator = f"第 {pages[0]}—{pages[-1]} 页"
            excerpt = "\n".join(para["text"] for para in unit["paragraphs"])
            if len(excerpt) > 800:
                excerpt = excerpt[:800] + "…"
            places.append({
                "document_id": doc["id"],
                "filename": doc["filename"],
                "title": doc["title"],
                "version": doc["version"],
                "section": unit["section"],
                "locator": locator,
                "excerpt": excerpt,
                "unit_id": unit["id"],
                "unverified_extraction": doc["unverified_extraction"],
            })
        rounds = clause["rounds"] or [None]
        targets = [{"key": "batch", "task_id": "", "revision": ""}] if clause["scope"] == "batch" else cases
        for case in targets:
            for rnd in rounds:
                suffix = rnd or "all"
                found.append({
                    "instance_id": f"{case['key']}:{clause['id']}:{suffix}",
                    "clause_id": clause["id"],
                    "case_key": case["key"],
                    "task_id": case["task_id"],
                    "round": rnd,
                    "title": clause["title"],
                    "text": clause["text"],
                    "actions": clause["actions"],
                    "evidence_required": clause["evidence_required"],
                    "places": places,
                })
    _require(len({item['instance_id'] for item in found}) == len(found), "义务实例编号冲突")
    return found
