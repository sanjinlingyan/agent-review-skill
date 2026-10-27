"""Synthetic protocol tests, not evidence of real semantic review ability."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import test_review as base_review
from agent_review.demo import create_demo
from agent_review.engine import prepare, status, finalize, virtual_prepare, virtual_execute, virtual_collect
from agent_review.errors import ReviewError, ReviewIncomplete
from agent_review.io import read_json, sha, write_json
from agent_review.virtual import WARNING


class VirtualFlowTest(unittest.TestCase):
    setUp = base_review.ReviewFlowTest.setUp
    clause = base_review.ReviewFlowTest.clause
    guide_catalog = base_review.ReviewFlowTest.guide_catalog
    write_catalog = base_review.ReviewFlowTest.write_catalog
    judgments = base_review.ReviewFlowTest.judgments

    def start(self, targets=("scorer", "agent_review"), expected="fail"):
        create_demo(self.root / "demo")
        source = self.root / "feedback.md"
        source.write_text("Fictional feedback for protocol tests only.", encoding="utf-8")
        suite = self.root / "suite.json"
        write_json(suite, {"schema": "agent-review.virtual-suite.v1", "id": "DEMO-suite", "version": "1",
                          "label": "虚拟测试集 · 虚构协议样例", "validation_only": True, "task_ids": ["DEMO-001"],
                          "sources": [{"path": "feedback.md", "sha256": sha(source), "locator": "全文"}],
                          "cases": [{"id": "V-01", "title": "量纲", "family": "单位", "feedback_fact": "虚构反馈",
                                     "hypothesis": "虚构测试", "applicability": "本题", "action": "检查量纲",
                                     "rationale": "长度应带长度单位", "source_locator": "虚构反馈第1条",
                                     "requirements": "Length must be in mm or equivalent length units.",
                                     "origin": "hypothesis_extension", "rounds": ["R1"], "targets": list(targets),
                                     "expected": expected, "unknown_kind": "unparseable" if expected == "unknown" else None,
                                     "materials": {"sample.md": "Length: 12 K"}}]})
        run = prepare([(None, self.root / "demo" / "submission")], self.root / "demo" / "standards", self.root / "runs", virtual_suite=suite)
        self.write_catalog(run)
        status(run)
        self.judgments(run, name="pass", unit="pass")
        return run

    def freeze(self, run):
        virtual_prepare(run)
        return read_json(run / "_internal" / "virtual" / "manifest.json")["instances"]

    def scorer(self, run, item, verdict="fail", repeats=1, output=None, command=None):
        plan = self.root / ("plan-" + item["target"] + ".json")
        data = {"verdict": verdict}
        if output is not None:
            data = output
        script = "from pathlib import Path; Path('result.json').write_text(" + repr(json.dumps(data)) + ",encoding='utf-8')"
        write_json(plan, {"schema": "agent-review.virtual-execution-plan.v1", "instance_id": item["instance_id"],
                          "authorization": "reviewed-host-copy", "reviewer_id": "protocol-test-agent",
                          "review_reason": "Reviewed synthetic command writes only a synthetic JSON result inside the copy.",
                          "command": command or [sys.executable, "-c", script], "result_file": "result.json", "repeats": repeats})
        return virtual_execute(run, plan)

    def blind(self, run, item, verdict="fail", session="synthetic-session-1", **actor_overrides):
        folder = run / "_internal" / "virtual" / "synthetic-host" / session
        folder.mkdir(parents=True)
        sample = Path(item["packet"]["path"]) / "sample.md"
        judgment = folder / "judgment.json"
        write_json(judgment, {"verdict": verdict, "reason": "Synthetic protocol judgment, not a real blind review.",
                              "evidence": [{"path": "sample.md", "sha256": sha(sample), "locator": "line 1"}]})
        transcript = folder / "transcript.txt"
        transcript.write_text("Simulated host transcript; not a real agent call.", encoding="utf-8")
        actor = folder / "actor.json"
        def proof(path):
            return {"path": path.relative_to(run).as_posix(), "sha256": sha(path), "locator": "全文"}
        write_json(actor, {"schema": "agent-review.blind-actor.v1", "session_id": session,
                           "inherited_context": False, "expected_answers_visible": False, "simulated": True,
                           "packet_sha256": item["packet"]["sha256"], "judgment_sha256": sha(judgment),
                           "transcript": proof(transcript), **actor_overrides})
        write_json(run / "_internal" / "virtual" / "observations" / (session + ".json"), {
            "observations": [{"schema": "agent-review.virtual-observation.v1", "instance_id": item["instance_id"],
                              "packet_sha256": item["packet"]["sha256"], "kind": "blind_session",
                              "actor_record": proof(actor), "judgment": proof(judgment)}]})
        return actor

    def test_freeze_requires_official_baseline_and_unfinished_tests_block(self):
        run = self.start()
        results = run / "_internal" / "results.json"
        data = results.read_bytes()
        results.unlink()
        with self.assertRaises(ReviewIncomplete):
            virtual_prepare(run)
        results.write_bytes(data)
        with self.assertRaises(ReviewIncomplete):
            finalize(run)
        self.freeze(run)
        self.assertFalse(status(run)["formal_output"])
        self.assertEqual(virtual_collect(run)["blocked"], 2)
        with self.assertRaises(ReviewIncomplete):
            finalize(run)
        self.assertFalse((run / "review.html").exists())
        with self.assertRaises(ReviewError):
            virtual_prepare(run)

    def test_matching_rejection_passes_and_repeats_do_not_inflate_denominator(self):
        run = self.start()
        original = (self.root / "demo" / "submission" / "R1" / "notes.md").read_bytes()
        items = self.freeze(run)
        scorer = next(i for i in items if i["target"] == "scorer")
        reviewer = next(i for i in items if i["target"] == "agent_review")
        self.scorer(run, scorer, repeats=2)
        self.blind(run, reviewer)
        self.assertEqual(virtual_collect(run)["completed"], 2)
        self.assertEqual(status(run)["phase"], "ready_for_finalization")
        result = finalize(run)
        metrics = read_json(run / "_internal" / "metrics.json")
        self.assertEqual(metrics["total"], 4)
        self.assertEqual(metrics["virtual"]["pass"], 2)
        self.assertEqual(result["pass_rate"], 1)
        self.assertEqual(result["human_notice"], WARNING)
        self.assertEqual((self.root / "demo" / "submission" / "R1" / "notes.md").read_bytes(), original)
        for name in ("review.html", "repair.md"):
            content = (run / name).read_text(encoding="utf-8")
            self.assertIn(WARNING, content)
            self.assertIn("内部综合审查结论（含虚拟测试）", content)
            self.assertIn("软件验证材料", content)
        with self.assertRaises(ReviewError):
            virtual_collect(run)

    def test_miss_changes_combined_verdict_but_keeps_official_verdict(self):
        run = self.start(targets=("scorer",))
        item = self.freeze(run)[0]
        self.scorer(run, item, verdict="pass")
        result = finalize(run)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["official"]["verdict"], "pass")
        self.assertAlmostEqual(result["pass_rate"], 2/3)
        repair = (run / "repair.md").read_text(encoding="utf-8")
        self.assertIn("【虚拟测试集】", repair)
        self.assertIn("评分器判断逻辑", repair)

    def test_packets_have_neutral_names_and_no_expected_fields(self):
        run = self.start()
        for item in self.freeze(run):
            folder = Path(item["packet"]["path"])
            self.assertEqual(len(folder.name), 32)
            task = read_json(folder / "task.json")
            self.assertNotIn("expected", task)
            self.assertNotIn("test_id", task)
            self.assertEqual({p.name for p in folder.iterdir()}, {"task.json", "requirements.md", "sample.md"})
        (self.root / "feedback.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "来源发生变化"):
            status(run)

    def test_blind_receipts_cannot_claim_inherited_or_simulated_business_sessions(self):
        run = self.start(targets=("agent_review",))
        item = self.freeze(run)[0]
        actor = self.blind(run, item, inherited_context=True)
        with self.assertRaisesRegex(ReviewError, "继承"):
            virtual_collect(run)
        data = read_json(actor)
        data["inherited_context"] = False
        write_json(actor, data)
        observation = next((run / "_internal" / "virtual" / "observations").glob("*.json"))
        observed = read_json(observation)
        observed["observations"][0]["actor_record"]["sha256"] = sha(actor)
        write_json(observation, observed)
        statefile = run / "_internal" / "run.json"
        state = read_json(statefile)
        state["virtual_suite"]["validation_only"] = False
        write_json(statefile, state)
        with self.assertRaisesRegex(ReviewError, "模拟会话"):
            virtual_collect(run)

    def test_unknowns_and_failed_processes_are_blocked(self):
        run = self.start(targets=("scorer",))
        item = self.freeze(run)[0]
        self.scorer(run, item, output={"unexpected": 1})
        self.assertEqual(virtual_collect(run)["blocked"], 1)
        self.scorer(run, item, command=[sys.executable, "-c", "raise SystemExit(7)"])
        with self.assertRaises(ReviewIncomplete):
            finalize(run)

    def test_original_judgment_packet_and_result_cannot_change_after_freeze(self):
        run = self.start(targets=("scorer",))
        item = self.freeze(run)[0]
        result = self.scorer(run, item)
        observations = read_json(Path(result["observations"]))
        produced = run / observations["observations"][0]["result_file"]
        produced.write_text('{"verdict":"pass"}', encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "哈希"):
            virtual_collect(run)
        baseline = run / "_internal" / "results.json"
        baseline.write_text(baseline.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "官方判断变化"):
            status(run)

    def test_unknown_exclusion_cannot_disappear_from_statistics(self):
        run = self.start(targets=("scorer",))
        iid = "virtual:case-0001:V-01:R1:scorer"
        proof = run / "_internal" / "results.json"
        write_json(run / "_internal" / "virtual" / "scope.json", {"excluded": {iid: {
            "reason": "环境不足无法执行", "evidence": {"path": "_internal/results.json", "sha256": sha(proof), "locator": "全文"}}}})
        with self.assertRaisesRegex(ReviewError, "不能排除"):
            virtual_prepare(run)

    def test_packet_change_and_reused_session_are_rejected(self):
        run = self.start(targets=("agent_review",))
        item = self.freeze(run)[0]
        self.blind(run, item)
        original = next((run / "_internal" / "virtual" / "observations").glob("*.json"))
        duplicate = original.with_name("duplicate.json")
        duplicate.write_bytes(original.read_bytes())
        with self.assertRaisesRegex(ReviewError, "不同的新会话"):
            virtual_collect(run)
        duplicate.unlink()
        sample = Path(item["packet"]["path"]) / "sample.md"
        sample.write_text("Length: 12 mm", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "材料在审查期间发生变化"):
            virtual_collect(run)

    def test_expected_unknown_matches_only_the_correct_cause(self):
        run = self.start(targets=("scorer",), expected="unknown")
        item = self.freeze(run)[0]
        self.scorer(run, item, output={"verdict": "unknown", "unknown_kind": "unparseable"})
        self.assertEqual(virtual_collect(run)["completed"], 1)
        self.assertEqual(finalize(run)["virtual"]["pass"], 1)

    def test_isolated_environment_failure_needs_new_execution_and_keeps_old_record(self):
        run = self.start(targets=("scorer",))
        item = self.freeze(run)[0]
        attempt = self.scorer(run, item, command=[sys.executable, "-c", "raise SystemExit(7)"])
        path = Path(attempt["observations"])
        note = run / "_internal" / "diagnosis.md"
        note.write_text("Synthetic diagnosis: executable invocation fixed; original log preserved.", encoding="utf-8")
        write_json(run / "_internal" / "virtual" / "resolutions.json", {"isolated": {
            path.relative_to(run).as_posix(): {"sha256": sha(path), "reason": "已定位测试调用错误并保留原始回执",
                "evidence": {"path": "_internal/diagnosis.md", "sha256": sha(note), "locator": "全文"}}}})
        self.assertEqual(virtual_collect(run)["completed"], 0)
        self.scorer(run, item)
        self.assertEqual(virtual_collect(run)["completed"], 1)
        self.assertTrue(path.exists())
        self.assertEqual(finalize(run)["virtual"]["pass"], 1)

    def test_retry_cannot_erase_a_confirmed_misjudgment(self):
        run = self.start(targets=("scorer",))
        item = self.freeze(run)[0]
        self.scorer(run, item, verdict="pass")
        self.scorer(run, item, verdict="fail")
        self.assertEqual(finalize(run)["virtual"]["verdict"], "fail")


if __name__ == "__main__":
    unittest.main()
