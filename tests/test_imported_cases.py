"""Synthetic v2 protocol and source-allowlist regressions."""
import json
from pathlib import Path
import unittest

import test_virtual as protocol
from agent_review.engine import prepare, status, finalize, virtual_prepare, virtual_collect
from agent_review.errors import ReviewError, ReviewIncomplete
from agent_review.io import read_json, sha, write_json
from agent_review.virtual_cases import same_value


class ImportedCasesTest(unittest.TestCase):
    setUp = protocol.VirtualFlowTest.setUp
    start = protocol.VirtualFlowTest.start
    clause = protocol.VirtualFlowTest.clause
    guide_catalog = protocol.VirtualFlowTest.guide_catalog
    write_catalog = protocol.VirtualFlowTest.write_catalog
    judgments = protocol.VirtualFlowTest.judgments
    scorer = protocol.VirtualFlowTest.scorer

    def begin(self, kind="native_case", missing=False, target="scorer"):
        self.start(targets=("scorer",))
        suite = read_json(self.root / "suite.json")
        suite["schema"] = "agent-review.virtual-suite.v2"
        case = suite["cases"][0]
        case.update(kind=kind, origin="external_reconstruction", materials={}, expected=None,
                    targets=[target], expected_checks={"C1": "pass", "facts.values": [[0, 1.19]]}, unresolved_expectations=[])
        if kind == "diagnostic":
            case["expected_checks"] = {}
        if kind == "process_check":
            case.update(targets=["delivery"], rounds=["delivery"], expected="pass", expected_checks={},
                        process_steps="Read current record\nCheck the claimed version", criterion_text="Current evidence must match the version.")
        write_json(self.root / "suite-v2.json", suite)
        run = prepare([(None, self.root / "demo/submission")], self.root / "demo/standards", self.root / "runs-v2", virtual_suite=self.root / "suite-v2.json")
        self.write_catalog(run)
        status(run)
        self.judgments(run, name="pass", unit="pass")
        if kind != "process_check" and not missing:
            artifact = run / "_internal/control.pdf"
            artifact.write_bytes(b"%PDF-synthetic-native-fixture-not-a-real-delivery")
            source = self.root / "demo/standards/guide.md"
            write_json(run / "_internal/virtual/bindings.json", {case["id"]: {
                "files": {"artifact.pdf": {"path": "_internal/control.pdf", "sha256": sha(artifact), "locator": "synthetic"}},
                "basis_files": [{"root": "standards", "path": "guide.md", "sha256": sha(source), "locator": "全文"}]}})
        virtual_prepare(run)
        return run, read_json(run / "_internal/virtual/manifest.json")["instances"][0]

    def test_vector_fields_numbers_and_arrays_match_without_whole_report_pass(self):
        run, item = self.begin()
        result = self.scorer(run, item, output={"C1": "pass", "facts": {"values": [[0.0, 1.19]]}, "C3": "fail"})
        self.assertEqual(virtual_collect(run)["completed"], 1)
        self.assertEqual(finalize(run)["virtual"]["pass"], 1)
        observed = read_json(Path(result["observations"]))["observations"][0]
        self.assertEqual(read_json(run / observed["result_file"])["C3"], "fail")
        self.assertFalse(same_value(True, 1))
        self.assertFalse(same_value([0, 1], [0, 2]))

    def test_one_target_mismatch_fails_but_missing_target_blocks(self):
        run, item = self.begin()
        self.scorer(run, item, output={"C1": "fail", "facts": {"values": [[0, 1.19]]}})
        self.assertEqual(finalize(run)["virtual"]["verdict"], "fail")

    def test_missing_native_inputs_cannot_be_replaced_by_descriptive_text(self):
        run, item = self.begin(missing=True)
        self.assertTrue(item["missing_inputs"])
        self.assertEqual(virtual_collect(run)["blocked"], 1)
        with self.assertRaises(ReviewIncomplete):
            self.scorer(run, item, output={"C1": "pass"})
        with self.assertRaises(ReviewIncomplete):
            finalize(run)

    def test_diagnostic_observation_is_visible_but_not_in_denominator(self):
        run, item = self.begin(kind="diagnostic")
        self.scorer(run, item, output={"C1": "fail", "C3": "unknown"})
        progress = virtual_collect(run)
        self.assertEqual(progress["diagnostics_completed"], 1)
        self.assertEqual(progress["expected"], 0)
        result = finalize(run)
        self.assertEqual(result["virtual"]["total"], 0)
        self.assertIn("诊断观察（不计成败分母）", (run / "review.html").read_text(encoding="utf-8"))

    def test_process_check_is_bound_to_current_delivery_and_actions(self):
        run, item = self.begin(kind="process_check")
        state = read_json(run / "_internal/run.json")
        source = self.root / "demo/submission/R1/notes.md"
        raw = {"instance_id": item["instance_id"], "packet_sha256": item["packet"]["sha256"],
               "kind": "delivery_check", "reviewer_id": "actual-test-role", "verdict": "pass",
               "subject_sha256": state["cases"][0]["submission"]["sha256"], "reason": "Synthetic current-record audit",
               "action_results": [{"id": a["id"], "state": "performed", "detail": a["text"]} for a in item["actions"]],
               "evidence": [{"root": "submission", "path": "R1/notes.md", "sha256": sha(source), "locator": "全文", "origin": "static"}]}
        file = run / "_internal/virtual/observations/process.json"
        write_json(file, {"observations": [raw]})
        self.assertEqual(virtual_collect(run)["completed"], 1)
        raw["subject_sha256"] = "stale"
        write_json(file, {"observations": [raw]})
        with self.assertRaisesRegex(ReviewError, "当前交付"):
            virtual_collect(run)

    def test_missing_vector_key_and_unknown_are_not_defaulted_to_pass(self):
        run, item = self.begin()
        self.scorer(run, item, output={"C1": "pass"})
        self.assertEqual(virtual_collect(run)["blocked"], 1)
        with self.assertRaises(ReviewIncomplete):
            finalize(run)

    def test_new_blind_session_vector_does_not_receive_expected_values(self):
        run, item = self.begin(target="agent_review")
        packet = Path(item["packet"]["path"])
        task = read_json(packet / "task.json")
        self.assertEqual(task["requested_fields"], ["C1", "facts.values"])
        self.assertNotIn("expected_checks", task)
        host = run / "_internal/host-fixture"
        host.mkdir()
        artifact = packet / "artifact.pdf"
        judgment = host / "judgment.json"
        write_json(judgment, {"values": {"C1": "pass", "facts.values": [[0, 1.19]]}, "reason": "Synthetic protocol response only",
                              "evidence": [{"path": "artifact.pdf", "sha256": sha(artifact), "locator": "synthetic fixture"}]})
        transcript = host / "transcript.txt"
        transcript.write_text("Simulated protocol fixture; not a real agent review.", encoding="utf-8")
        def proof(path):
            return {"path": path.relative_to(run).as_posix(), "sha256": sha(path), "locator": "全文"}
        actor = host / "actor.json"
        write_json(actor, {"schema": "agent-review.blind-actor.v1", "session_id": "synthetic-vector-session",
                           "simulated": True, "inherited_context": False, "expected_answers_visible": False,
                           "packet_sha256": item["packet"]["sha256"], "judgment_sha256": sha(judgment), "transcript": proof(transcript)})
        write_json(run / "_internal/virtual/observations/blind.json", {"observations": [{"instance_id": item["instance_id"],
            "packet_sha256": item["packet"]["sha256"], "kind": "blind_session", "actor_record": proof(actor), "judgment": proof(judgment)}]})
        self.assertEqual(finalize(run)["virtual"]["pass"], 1)

    def test_standard_bundle_does_not_scan_history_as_official_text(self):
        self.start(targets=("scorer",))
        source = self.root / "demo/standards/guide.md"
        old = self.root / "old-report.md"
        old.write_text("Old conclusion: fail. Not a new official standard.", encoding="utf-8")
        descriptor = self.root / "standards-bundle.json"
        write_json(descriptor, {"schema": "agent-review.standards-bundle.v1", "version": "synthetic", "root": ".",
                              "documents": [{"name": "guide.md", "path": "demo/standards/guide.md", "role": "official_source", "sha256": sha(source)}],
                              "auxiliary": [{"name": "scope.md", "path": "old-report.md", "role": "scope_reference", "sha256": sha(old)}]})
        run = prepare([(None, self.root / "demo/submission")], None, self.root / "bundle-runs", standards_bundle=descriptor)
        inventory = read_json(run / "_internal/source-inventory.json")
        self.assertEqual([d["filename"] for d in inventory["documents"]], ["guide.md"])
        old.write_text("Changed context", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "标准资料入口"):
            status(run)


if __name__ == "__main__":
    unittest.main()
