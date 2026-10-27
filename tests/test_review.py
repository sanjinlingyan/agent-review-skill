"""Behavior tests for the agent review path."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_review.catalog import validate_catalog
from agent_review.demo import NOTES, create_demo
from agent_review.engine import execute, finalize, prepare, status
from agent_review.errors import ReviewError, ReviewIncomplete
from agent_review.inventory import build_inventory, paragraphs
from agent_review.io import read_json, sha, write_json


def audits(document_ids):
    return [
        {"pass": 1, "reviewer": "primary-agent", "method": "按原文分类并拆分义务", "document_ids": document_ids},
        {"pass": 2, "reviewer": "primary-agent", "method": "从原文反查", "document_ids": document_ids},
    ]


def paragraph_id(inventory, snippet, document=0):
    for item in inventory["documents"][document]["paragraphs"]:
        if snippet in item["text"]:
            return item["id"]
    raise AssertionError(snippet)


class ReviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "交付 材料"

    def start(self, notes=NOTES, standards_text=None):
        create_demo(self.root / "demo")
        if notes != NOTES:
            (self.root / "demo" / "submission" / "R1" / "notes.md").write_text(notes, encoding="utf-8")
        if standards_text is not None:
            (self.root / "demo" / "standards" / "guide.md").write_text(standards_text, encoding="utf-8")
        return prepare([(None, self.root / "demo" / "submission")], self.root / "demo" / "standards", self.root / "runs")

    def guide_catalog(self, inventory):
        doc = inventory["documents"][0]["id"]
        groups = {
            "u-intro": [paragraph_id(inventory, "虚构样品验收说明"), paragraph_id(inventory, "不是真实甲方标准")],
            "u-duty": [paragraph_id(inventory, "## 样品说明"), paragraph_id(inventory, "同时满足下面两项"), paragraph_id(inventory, "| 测量值 |")],
            "u-fig": [paragraph_id(inventory, "## 附图"), paragraph_id(inventory, "示意图不是验收条件")],
            "u-other": [paragraph_id(inventory, "## 其他题目"), paragraph_id(inventory, "专用阈值")],
        }
        return {
            "schema": "agent-review.catalog.v1",
            "documents": [{
                "id": doc, "title": "虚构样品验收说明", "version": "演示",
                "units": [
                    {"id": "u-intro", "paragraph_ids": groups["u-intro"], "summary": "演示背景", "relevance": "context", "reason": "背景说明，不含验收义务"},
                    {"id": "u-duty", "paragraph_ids": groups["u-duty"], "summary": "说明须同时包含名称和带单位的测量值", "relevance": "applicable", "reason": "表格列出两项独立义务"},
                    {"id": "u-fig", "paragraph_ids": groups["u-fig"], "summary": "示意图只是建议", "relevance": "context", "reason": "原文写明不是验收条件"},
                    {"id": "u-other", "paragraph_ids": groups["u-other"], "summary": "其他样品的阈值", "relevance": "excluded", "reason": "其他样品的专用要求，本题没有触发"},
                ],
            }],
            "clauses": [
                self.clause("C-NAME", ["u-duty"], "写明样品名称", "说明中要能确定样品名称", "说明里写有样品名称"),
                self.clause("C-UNIT", ["u-duty"], "测量值带单位", "测量值要带单位", "测量值写有单位"),
                self.clause("C-FIG", ["u-fig"], "可以附示意图", "可以附示意图", "有无示意图都不影响验收", strength="recommendation"),
            ],
            "coverage_audits": audits([doc]),
            "fidelity_overrides": [],
        }

    def clause(self, clause_id, units, title, text, accept, strength="required", rounds=("R1",)):
        return {
            "id": clause_id, "title": title, "text": text, "accept": accept, "strength": strength,
            "scope": "case", "rounds": list(rounds), "unit_ids": units,
            "actions": [{"id": "read", "text": "阅读交付说明并核对这项义务"}],
            "evidence_required": "交付说明中的对应内容",
            "applicability": {"decision": "included", "reason": "这项要求对本题适用", "basis": units[0]},
        }

    def write_catalog(self, run):
        inventory = read_json(run / "_internal" / "source-inventory.json")
        write_json(run / "_internal" / "catalog.json", self.guide_catalog(inventory))
        return inventory

    def judgments(self, run, *, name="pass", unit="fail"):
        state = read_json(run / "_internal" / "run.json")
        notes = Path(state["cases"][0]["submission"]["path"]) / "R1" / "notes.md"
        digest = sha(notes)
        obligations = read_json(run / "_internal" / "obligations.json")["instances"]
        rows = []
        for item in obligations:
            passed = (item["clause_id"] == "C-NAME" and name == "pass") or (item["clause_id"] == "C-UNIT" and unit == "pass")
            row = {
                "instance_id": item["instance_id"], "clause_id": item["clause_id"], "case_key": item["case_key"],
                "task_id": item["task_id"], "round": item["round"], "progress": "completed",
                "status": "pass" if passed else "fail",
                "summary": "说明写有演示翼片" if passed else "测量值 12 没有单位",
                "observed": "阅读了 R1/notes.md",
                "impact": "名称可以确定" if passed else "测量值无法解释",
                "action_results": [{"id": "read", "state": "performed", "detail": "对照说明全文和表格两项义务"}],
                "evidence": [{"root": "submission", "path": "R1/notes.md", "sha256": digest, "locator": "说明全文", "origin": "static"}],
            }
            if not passed:
                row["repair"] = {
                    "problem": "测量值 12 没有单位", "location": "R1/notes.md", "action": "补上测量值的单位",
                    "done_when": "说明中的测量值带有单位", "owner": "supplier_agent", "allowed_paths": ["R1/notes.md"],
                }
            rows.append(row)
        write_json(run / "_internal" / "results.json", {
            "schema": "agent-review.results.v1", "run_id": state["run_id"],
            "implementation_sha256": state["implementation_sha256"], "reviewer_id": "primary-agent",
            "clause_results": rows,
        })
        return rows

    def test_wrapped_lines_stay_one_paragraph_and_one_paragraph_can_hold_two_duties(self):
        found = paragraphs("第一行\n第二行\n第三行\n\n下一段")
        self.assertEqual([item["block_ids"] for item in found], [["l1", "l2", "l3"], ["l5"]])
        run = self.start()
        self.write_catalog(run)
        published = status(run)
        obligations = read_json(run / "_internal" / "obligations.json")["instances"]
        self.assertEqual(published["expected"], 2)
        self.assertEqual({item["clause_id"] for item in obligations}, {"C-NAME", "C-UNIT"})
        catalog = read_json(run / "_internal" / "catalog.json")
        catalog["documents"][0]["units"][1]["paragraph_ids"].append("g-split")
        write_json(run / "_internal" / "catalog.json", catalog)
        with self.assertRaises(ReviewError) as caught:
            finalize(run)
        self.assertIn("段落", str(caught.exception))

    def test_content_failure_is_reported_and_file_existence_does_not_pass_it(self):
        run = self.start()
        self.assertTrue((Path(read_json(run / "_internal" / "run.json")["cases"][0]["submission"]["path"]) / "R1" / "notes.md").is_file())
        self.write_catalog(run)
        status(run)
        self.judgments(run)
        result = finalize(run)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["completion_rate"], 1)
        self.assertEqual(result["pass_rate"], 0.5)
        html = (run / "review.html").read_text(encoding="utf-8")
        repair = (run / "repair.md").read_text(encoding="utf-8")
        self.assertIn("未通过", html)
        self.assertIn("写明样品名称", html)
        self.assertIn("测量值带单位", html)
        self.assertIn("fail-only", html)
        self.assertIn("max-width:650px", html)
        self.assertNotIn("示意图不是验收条件", html)
        self.assertNotIn("专用阈值", html)
        self.assertIn("case-0001:C-UNIT:R1", repair)
        self.assertNotIn("case-0001:C-NAME:R1", repair)
        self.assertIn("没有单位", repair)
        with self.assertRaises(ReviewError):
            finalize(run)

    def test_paraphrase_is_accepted_without_keyword_match(self):
        run = self.start("这件演示翼片的长度是 12 毫米\n")
        self.write_catalog(run)
        status(run)
        self.judgments(run, name="pass", unit="pass")
        self.assertEqual(finalize(run)["verdict"], "pass")
        html = (run / "review.html").read_text(encoding="utf-8")
        self.assertIn("验收通过率 100.0%", html)
        self.assertIn("本题适用要求全部通过", html)

    def test_loose_language_cannot_be_tightened_and_a_duty_cannot_become_advice(self):
        folder = self.root / "rules"
        folder.mkdir(parents=True)
        (folder / "rule.md").write_text("可以书面说明或附图。\n\n测量设备必须经过校准。\n", encoding="utf-8")
        inventory = build_inventory(folder)
        loose, strict = paragraph_id(inventory, "或附图"), paragraph_id(inventory, "必须经过校准")
        catalog = {
            "schema": "agent-review.catalog.v1",
            "documents": [{"id": inventory["documents"][0]["id"], "title": "规则", "version": "v0", "units": [
                {"id": "u1", "paragraph_ids": [loose], "summary": "说明方式", "relevance": "applicable", "reason": "对本题适用"},
                {"id": "u2", "paragraph_ids": [strict], "summary": "校准", "relevance": "applicable", "reason": "对本题适用"},
            ]}],
            "clauses": [
                self.clause("C-TIGHT", ["u1"], "提交材料", "必须全部提交书面说明且附图", "必须全部提交书面说明且附图", rounds=()),
                self.clause("C-CAL", ["u2"], "校准", "设备经过校准", "有校准记录", rounds=()),
            ],
            "coverage_audits": audits([inventory["documents"][0]["id"]]),
            "fidelity_overrides": [],
        }
        with self.assertRaises(ReviewError) as caught:
            validate_catalog(inventory, catalog)
        self.assertIn("收紧", str(caught.exception))
        catalog["documents"][0]["units"][0]["relevance"] = "context"
        catalog["documents"][0]["units"][0]["reason"] = "本例不把这段当作验收项"
        catalog["documents"][0]["units"][1]["relevance"] = "context"
        catalog["documents"][0]["units"][1]["reason"] = "改写成建议会丢掉必要义务"
        catalog["clauses"] = [self.clause("C-WEAK", ["u2"], "校准", "可以校准", "有校准记录即可", strength="recommendation", rounds=())]
        with self.assertRaises(ReviewError) as caught:
            validate_catalog(inventory, catalog)
        self.assertIn("必要义务", str(caught.exception))

    def test_missing_result_and_unclear_exclusion_block_different_ways(self):
        run = self.start()
        self.write_catalog(run)
        status(run)
        with self.assertRaises(ReviewIncomplete):
            finalize(run)
        self.assertFalse((run / "review.html").exists())
        catalog = read_json(run / "_internal" / "catalog.json")
        catalog["documents"][0]["units"][3]["reason"] = "条件未明"
        write_json(run / "_internal" / "catalog.json", catalog)
        with self.assertRaises(ReviewError) as caught:
            finalize(run)
        self.assertIn("不能排除", str(caught.exception))

    def test_blocked_review_is_not_a_supplier_defect(self):
        run = self.start()
        self.write_catalog(run)
        status(run)
        state = read_json(run / "_internal" / "run.json")
        obligations = read_json(run / "_internal" / "obligations.json")["instances"]
        done, waiting = obligations
        notes = Path(state["cases"][0]["submission"]["path"]) / "R1" / "notes.md"
        rows = [{
            "instance_id": done["instance_id"], "clause_id": done["clause_id"], "case_key": done["case_key"],
            "task_id": done["task_id"], "round": done["round"], "progress": "completed", "status": "pass",
            "summary": "名称已写明", "observed": "阅读了说明", "impact": "名称可以确定",
            "action_results": [{"id": "read", "state": "performed", "detail": "核对了名称"}],
            "evidence": [{"root": "submission", "path": "R1/notes.md", "sha256": sha(notes), "locator": "名称", "origin": "static"}],
        }, {
            "instance_id": waiting["instance_id"], "clause_id": waiting["clause_id"], "case_key": waiting["case_key"],
            "task_id": waiting["task_id"], "round": waiting["round"], "progress": "blocked",
            "blocker": {"reason": "没有可用的阅读器", "owner": "environment", "next_action": "安装阅读器后继续审查这一项"},
            "evidence": [], "action_results": [],
        }]
        write_json(run / "_internal" / "results.json", {
            "schema": "agent-review.results.v1", "run_id": state["run_id"],
            "implementation_sha256": state["implementation_sha256"], "reviewer_id": "primary-agent",
            "clause_results": rows,
        })
        progress = status(run)
        self.assertEqual(progress["blocked"], 1)
        self.assertEqual(progress["blockers"][0]["owner"], "environment")
        with self.assertRaises(ReviewIncomplete):
            finalize(run)
        self.assertFalse((run / "repair.md").exists())

    def test_unknown_cause_is_not_rewritten_as_pass_or_fail(self):
        run = self.start()
        self.write_catalog(run)
        status(run)
        self.judgments(run)
        payload = read_json(run / "_internal" / "results.json")
        payload["scorer_unknowns"] = [{
            "id": "score-1", "kind": "infrastructure", "disposition": "supplier_fail",
            "clause_instance": "case-0001:C-UNIT:R1", "reason": "评分器所在磁盘已满",
        }]
        write_json(run / "_internal" / "results.json", payload)
        with self.assertRaises(ReviewError) as caught:
            finalize(run)
        self.assertIn("不能直接转成", str(caught.exception))
        payload["scorer_unknowns"][0]["kind"] = "capability"
        payload["scorer_unknowns"][0]["disposition"] = "isolated"
        write_json(run / "_internal" / "results.json", payload)
        with self.assertRaises(ReviewError) as caught:
            finalize(run)
        self.assertIn("核心判定", str(caught.exception))
        payload["scorer_unknowns"][0]["disposition"] = "supplier_fail"
        write_json(run / "_internal" / "results.json", payload)
        self.assertEqual(finalize(run)["verdict"], "fail")

    def test_changed_input_comparison_and_execution_keep_their_boundaries(self):
        run = self.start("样品名称：演示翼片\n测量值：12 mm\n")
        self.write_catalog(run)
        status(run)
        notes = Path(read_json(run / "_internal" / "run.json")["cases"][0]["submission"]["path"]) / "R1" / "notes.md"
        original = notes.read_text(encoding="utf-8")
        notes.write_text(original + "补充\n", encoding="utf-8")
        with self.assertRaises(ReviewError) as caught:
            status(run)
        self.assertIn("发生变化", str(caught.exception))
        notes.write_text(original, encoding="utf-8")
        self.judgments(run, name="pass", unit="pass")
        results = run / "_internal" / "results.json"
        baseline = run / "_internal" / "comparisons" / "baseline.json"
        baseline.write_bytes(results.read_bytes())
        os.utime(baseline, (1_700_000_000, 1_700_000_000))
        payload = read_json(results)
        payload["clause_results"][0]["summary"] = "对照后维持名称判断"
        write_json(results, payload)
        prior = run / "_internal" / "comparisons" / "prior.json"
        write_json(prior, {"schema": "agent-review.comparison.v1", "baseline_sha256": sha(baseline),
                           "disclosures": ["seen-prior-report"], "dispositions": []})
        os.utime(prior, (1_700_000_100, 1_700_000_100))
        with self.assertRaises(ReviewError) as caught:
            finalize(run)
        self.assertIn("逐条处置", str(caught.exception))
        prior_data = read_json(prior)
        prior_data["dispositions"] = [{"instance_id": payload["clause_results"][0]["instance_id"], "decision": "revise", "reason": "重新阅读说明后，把摘要写得更具体"}]
        write_json(prior, prior_data)
        os.utime(prior, (1_700_000_100, 1_700_000_100))
        self.assertEqual(finalize(run)["verdict"], "pass")
        plan = self.root / "plan.json"
        write_json(plan, {"schema": "agent-review.execution-plan.v1", "authorization": "reviewed-host-copy",
                          "reviewer_id": "primary-agent", "review_reason": "已查看入口，命令只在副本里写一个结果文件",
                          "case_key": "case-0001", "command": [sys.executable, "-c", "open('out.txt','w',encoding='utf-8').write('ok')"],
                          "result_file": "out.txt"})
        with self.assertRaises(ReviewError):
            execute(run, plan)

    def test_execute_records_a_host_copy_and_rejects_an_unreviewed_plan(self):
        run = self.start()
        plan = self.root / "plan.json"
        write_json(plan, {"schema": "agent-review.execution-plan.v1", "reviewer_id": "primary-agent",
                          "review_reason": "已查看入口，命令只在副本里写一个结果文件", "case_key": "case-0001",
                          "command": [sys.executable, "-c", "open('out.txt','w',encoding='utf-8').write('ok')"],
                          "result_file": "out.txt", "repeats": 2})
        with self.assertRaises(ReviewError):
            execute(run, plan)
        payload = read_json(plan)
        payload["authorization"] = "reviewed-host-copy"
        write_json(plan, payload)
        receipt = execute(run, plan)
        self.assertFalse(receipt["sandbox"])
        self.assertEqual([item["exit_code"] for item in receipt["repeats"]], [0, 0])
        self.assertTrue(all(item["result_sha256"] for item in receipt["repeats"]))

    def test_batch_gap_zero_scope_and_report_order(self):
        create_demo(self.root / "demo")
        (self.root / "demo" / "submission" / "R1" / "notes.md").write_text("样品名称：演示翼片\n测量值：12 mm\n", encoding="utf-8")
        manifest = self.root / "batch.json"
        write_json(manifest, {"cases": [{"submission": "demo/submission"}],
                              "scope": {"label": "演示批次", "expected_ids": ["DEMO-001", "DEMO-009"], "excluded_ids": []}})
        from agent_review.engine import load_batch
        pairs, scope = load_batch(manifest)
        run = prepare(pairs, self.root / "demo" / "standards", self.root / "runs2", batch_path=manifest, scope=scope)
        self.write_catalog(run)
        status(run)
        self.judgments(run, name="pass", unit="pass")
        self.assertEqual(status(run)["phase"], "review_in_progress")
        with self.assertRaises(ReviewIncomplete):
            finalize(run)
        payload = read_json(run / "_internal" / "results.json")
        payload["batch_resolution"] = {"missing_ids": ["DEMO-009"], "extra_ids": [], "decision": "in-scope-missing",
                                       "reason": "DEMO-009 在本次授权批次内，但目录里没有提交"}
        write_json(run / "_internal" / "results.json", payload)
        result = finalize(run)
        self.assertEqual(result["verdict"], "fail")
        self.assertIn("未提交", (run / "review.html").read_text(encoding="utf-8"))
        self.assertIn("BATCH-MISSING", (run / "repair.md").read_text(encoding="utf-8"))
        self.assertEqual(status(run)["phase"], "finalized")
        self.assertTrue(status(run)["formal_output"])

    def test_out_of_scope_extra_cannot_remain_in_acceptance_denominator(self):
        from agent_review.engine import _batch_rows
        state = {"batch": True, "scope": {"expected_ids": ["DEMO-001"]},
                 "cases": [{"task_id": "DEMO-001"}, {"task_id": "DEMO-002"}]}
        payload = {"batch_resolution": {"missing_ids": [], "extra_ids": ["DEMO-002"],
                   "decision": "out-of-scope", "reason": "正式名单仅包含第一个样品"}}
        with self.assertRaisesRegex(ReviewIncomplete, "范围外"):
            _batch_rows(state, payload, self.root)

    def test_zip_without_record_keeps_filename_identity_and_rejects_duplicates(self):
        import warnings
        from agent_review.io import safe_extract
        self.root.mkdir(parents=True)
        standards = self.root / "std"
        standards.mkdir()
        (standards / "guide.md").write_text("样品须有名称。", encoding="utf-8")
        archives = []
        for name in ("sample-one", "sample-two"):
            archive = self.root / (name + ".zip")
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("notes.md", "sample")
            archives.append((None, archive))
        run = prepare(archives, standards, self.root / "runs")
        self.assertEqual([case["task_id"] for case in read_json(run / "_internal" / "run.json")["cases"]],
                         ["sample-one", "sample-two"])
        duplicate = self.root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as package:
                package.writestr("notes.md", "first")
                package.writestr("notes.md", "second")
        with self.assertRaisesRegex(ReviewError, "重复"):
            safe_extract(duplicate, self.root / "duplicate")
        corrupt = self.root / "invalid.zip"
        corrupt.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ReviewError, "有效"):
            safe_extract(corrupt, self.root / "invalid")

    def test_code_change_invalidates_old_run(self):
        run = self.start()
        with patch("agent_review.engine.implementation_sha256", return_value="changed"):
            with self.assertRaisesRegex(ReviewError, "代码已变化"):
                status(run)
            with self.assertRaisesRegex(ReviewError, "代码已变化"):
                finalize(run)

    def test_duplicate_units_and_wrong_reverse_pass_are_rejected(self):
        run = self.start()
        inventory = read_json(run / "_internal" / "source-inventory.json")
        catalog = self.guide_catalog(inventory)
        catalog["documents"][0]["units"][1]["id"] = "u-intro"
        with self.assertRaisesRegex(ReviewError, "唯一"):
            validate_catalog(inventory, catalog)
        catalog = self.guide_catalog(inventory)
        catalog["coverage_audits"][0]["method"] = "从原文反查"
        catalog["coverage_audits"][1]["method"] = "按原文分类"
        with self.assertRaisesRegex(ReviewError, "第二遍"):
            validate_catalog(inventory, catalog)

    def test_case_specific_clause_does_not_expand_to_other_tasks(self):
        from agent_review.catalog import instances
        run = self.start()
        inventory = read_json(run / "_internal" / "source-inventory.json")
        catalog = self.guide_catalog(inventory)
        catalog["clauses"][0]["task_ids"] = ["DEMO-001"]
        normalized = validate_catalog(inventory, catalog)
        cases = [{"key": "case-0001", "task_id": "DEMO-001"},
                 {"key": "case-0002", "task_id": "DEMO-002"}]
        rows = instances(normalized, cases)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["task_id"] for row in rows if row["clause_id"] == "C-NAME"], ["DEMO-001"])
        normalized["clauses"][0]["task_ids"] = ["TYPO"]
        with self.assertRaisesRegex(ReviewError, "题号"):
            instances(normalized, cases)

    def test_word_standard_requires_reading_instead_of_asset_exclusion(self):
        run = self.start()
        inventory = read_json(run / "_internal" / "source-inventory.json")
        catalog = self.guide_catalog(inventory)
        doc = inventory["documents"][0]
        doc["kind"] = "manual"
        doc["path"] = "guide.docx"
        extracted = [{"text": p["text"], "locator": f"正文段落 {i}"}
                     for i, p in enumerate(doc["paragraphs"], 1)]
        doc["paragraphs"] = []
        with self.assertRaises(ReviewIncomplete):
            validate_catalog(inventory, catalog)
        catalog["documents"][0]["paragraphs"] = extracted
        normalized = validate_catalog(inventory, catalog)
        self.assertTrue(normalized["documents"][0]["unverified_extraction"])
        from agent_review.inventory import kind_of
        self.assertEqual(kind_of(Path("guide.docx")), "manual")

    def test_catalog_order_and_empty_scope_report(self):
        standards = self.root / "std"
        submission = self.root / "sub"
        standards.mkdir(parents=True)
        (submission / "R1").mkdir(parents=True)
        (standards / "a.md").write_text("# 甲文件\n\n交付写明甲项。\n", encoding="utf-8")
        (standards / "b.md").write_text("# 乙文件\n\n交付写明乙项。\n", encoding="utf-8")
        (submission / "R1" / "notes.md").write_text("甲项和乙项都已写明。\n", encoding="utf-8")
        write_json(submission / "record.json", {"id": "DEMO-001", "revision": "v1"})
        run = prepare([(None, submission)], standards, self.root / "ordered-runs")
        inventory = read_json(run / "_internal" / "source-inventory.json")
        documents = []
        clauses = []
        for name, title, snippet in (("b.md", "乙文件", "乙项"), ("a.md", "甲文件", "甲项")):
            source = next(item for item in inventory["documents"] if item["filename"] == name)
            documents.append({
                "id": source["id"], "title": title, "version": "v0",
                "units": [{"id": "u-" + title, "paragraph_ids": [item["id"] for item in source["paragraphs"]],
                           "summary": title, "relevance": "applicable", "reason": "对本题适用"}],
            })
            clauses.append(self.clause("C-" + title, ["u-" + title], title, f"交付写明{snippet}", f"文本中写有{snippet}", rounds=()))
        write_json(run / "_internal" / "catalog.json", {
            "schema": "agent-review.catalog.v1", "documents": documents, "clauses": clauses,
            "coverage_audits": audits([item["id"] for item in documents]), "fidelity_overrides": [],
        })
        status(run)
        state = read_json(run / "_internal" / "run.json")
        digest = sha(submission / "R1" / "notes.md")
        rows = []
        for item in read_json(run / "_internal" / "obligations.json")["instances"]:
            rows.append({
                "instance_id": item["instance_id"], "clause_id": item["clause_id"], "case_key": item["case_key"],
                "task_id": item["task_id"], "round": item["round"], "progress": "completed", "status": "pass",
                "summary": "两项都写明了", "observed": "阅读了说明", "impact": "该项满足",
                "action_results": [{"id": "read", "state": "performed", "detail": "阅读了说明"}],
                "evidence": [{"root": "submission", "path": "R1/notes.md", "sha256": digest, "locator": "全文", "origin": "static"}],
            })
        write_json(run / "_internal" / "results.json", {
            "schema": "agent-review.results.v1", "run_id": state["run_id"], "implementation_sha256": state["implementation_sha256"],
            "reviewer_id": "primary-agent", "clause_results": rows,
        })
        finalize(run)
        html = (run / "review.html").read_text(encoding="utf-8")
        self.assertLess(html.index("乙文件"), html.index("甲文件"))

    def test_no_applicable_items_are_not_a_pass(self):
        standards = self.root / "advice"
        submission = self.root / "empty-sub"
        standards.mkdir(parents=True)
        submission.mkdir()
        (standards / "advice.md").write_text("建议附上一张示意图，便于阅读。\n", encoding="utf-8")
        (submission / "notes.md").write_text("没有附图。\n", encoding="utf-8")
        write_json(submission / "record.json", {"id": "DEMO-001", "revision": "v1"})
        run = prepare([(None, submission)], standards, self.root / "advice-runs")
        inventory = read_json(run / "_internal" / "source-inventory.json")
        doc = inventory["documents"][0]
        write_json(run / "_internal" / "catalog.json", {
            "schema": "agent-review.catalog.v1",
            "documents": [{"id": doc["id"], "title": "建议", "version": "v0", "units": [
                {"id": "u", "paragraph_ids": [item["id"] for item in doc["paragraphs"]], "summary": "附图建议",
                 "relevance": "context", "reason": "原文是建议，不是验收条件"},
            ]}],
            "clauses": [self.clause("C-FIG", ["u"], "附图", "可以附示意图", "有无示意图都不影响验收", strength="recommendation", rounds=())],
            "coverage_audits": audits([doc["id"]]), "fidelity_overrides": [],
        })
        state = read_json(run / "_internal" / "run.json")
        write_json(run / "_internal" / "results.json", {
            "schema": "agent-review.results.v1", "run_id": state["run_id"], "implementation_sha256": state["implementation_sha256"],
            "reviewer_id": "primary-agent", "clause_results": [],
        })
        result = finalize(run)
        self.assertEqual(result["verdict"], "no_applicable")
        self.assertIsNone(result["pass_rate"])
        html = (run / "review.html").read_text(encoding="utf-8")
        self.assertIn("无适用项", html)
        self.assertNotIn("验收通过率 100%", html)

    def test_archives_paths_and_cli_exit_codes(self):
        standards = self.root / "std"
        standards.mkdir(parents=True)
        (standards / "guide.md").write_text("# 规则\n\n交付写明样品名称。\n", encoding="utf-8")
        archive = self.root / "交付.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("record.json", json.dumps({"id": "DEMO-001", "revision": "v1"}))
            package.writestr("R1/说明.md", "样品名称：演示翼片\n")
        slipped = self.root / "bad.zip"
        with zipfile.ZipFile(slipped, "w") as package:
            package.writestr("../escape.txt", "no")
        with self.assertRaises(ReviewError):
            prepare([(None, slipped)], standards, self.root / "slip-runs")
        linked = self.root / "link.zip"
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(linked, "w") as package:
            package.writestr(info, "target")
        with self.assertRaises(ReviewError):
            prepare([(None, linked)], standards, self.root / "link-runs")
        with self.assertRaises(ReviewError):
            prepare([(None, archive)], standards, standards)
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env.pop("AGENT_REVIEW_HOST", None)
        env.pop("AGENT_REVIEW_MODEL_VERSION", None)
        env["AGENT_REVIEW_MODEL"] = "test-model"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "review.py"), "run", "--submission", str(archive),
             "--standards", str(standards), "--out", str(self.root / "cli-runs")],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run = Path(json.loads(completed.stdout)["run"])
        self.assertEqual(read_json(run / "_internal" / "run.json")["executor"]["model"], "test-model")
        self.assertEqual(read_json(run / "_internal" / "run.json")["executor"]["model_version"], "未暴露")
        again = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "review.py"), "finalize", "--run", str(run)],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(again.returncode, 3, again.stderr)
        self.assertFalse((run / "review.html").exists())
        self.assertIn("catalog.json", again.stderr)


if __name__ == "__main__":
    unittest.main()
