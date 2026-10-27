# 接口

用户不手写这些文件。Agent 在运行目录的 `_internal/` 里维护它们。示例都是虚构字段。

## 命令

```text
python scripts/review.py run --submission <路径> --standards <路径> [--original <路径>] [--out <目录>]
python scripts/review.py run --standards <路径> --batch <名单.json> [--out <目录>]
python scripts/review.py status --run <运行目录>
python scripts/review.py execute --run <运行目录> --plan <计划.json>
python scripts/review.py finalize --run <运行目录>
python scripts/review.py demo --out <仓库外目录>
```

返回码：0 表示命令完成，2 表示输入或操作错误，3 表示正式报告被请求时审查仍未完成或受阻。

## 运行目录

```text
<run_id>/
  review.html
  repair.md
  _internal/
    run.json
    source-inventory.json
    catalog.json
    catalog.frozen.json
    obligations.json
    results.json
    metrics.json
    executions/
    comparisons/
```

`run.json` 记录运行编号、代码哈希、依据和交付指纹，以及宿主暴露的产品、模型和版本。没有暴露的标识写“未暴露”。

## catalog.json

`schema` 为 `agent-review.catalog.v1`。`documents` 的顺序就是报告顺序，必须包含清单里的每个依据文件。

文本文件的 `units` 引用清单中的 `paragraph_ids`。PDF 在文件条目里提供 `paragraphs`，每段含 `page` 和 `text`。附件的 `role` 为 `asset`，并写 `reason`。

条款字段：`id`、`title`、`text`、`accept`、`strength`（required / conditional / recommendation）、`scope`（case / batch）、`rounds`、`actions`、`evidence_required`、`unit_ids`、`applicability`。`applicability.decision` 只能是 included 或 excluded，并带理由和依据。条件义务在纳入时要写 `condition`。

`coverage_audits` 至少两遍，包含 pass 1 和 pass 2，第二遍方法含“反查”。`fidelity_overrides` 只在有明确裁决时使用，写 `clause_id`、`added` 或 `weakened`，以及 `basis`。

## results.json

`schema` 为 `agent-review.results.v1`，并带本次 `run_id`、`implementation_sha256` 和 `reviewer_id`。`reviewer_id` 使用实际 Agent 标识，不能填真人、专家或 QC。

每条 `clause_results` 对应 `obligations.json` 里的一个实例，字段包括进度、结论、摘要、观察、影响、动作结果和证据。进度是 `not_started`、`in_progress`、`blocked`、`completed`。只有 `completed` 可以是 `pass` 或 `fail`。

证据的 `root` 是 `submission`、`original`、`standards` 或 `run`。`origin` 是 `static`、`historical` 或 `measured`。历史复用的 `verified` 必须包含 `identity`、`sha256`、`version`、`scope`。缺失写成 `missing: true`，而且该路径确实不存在。

未通过的 `repair` 包含 `problem`、`location`、`action`、`done_when`、`owner`、`allowed_paths`。`owner` 只能是 `supplier_agent`、`human_reviewer` 或 `customer`。受阻的 `blocker` 包含 `reason`、`owner`、`next_action`，`owner` 可以是 `environment` 或 `reviewer`。

`scorer_unknowns` 的 `kind` 是 `infrastructure`、`unparseable`、`ambiguity` 或 `capability`。`disposition` 是 `isolated`、`supplier_fail` 或 `reviewer_block`。后两种必须和对应实例的结论或受阻状态一致。

## 批次名单

```json
{
  "cases": [{"submission": "deliveries/DEMO-001", "original": "sources/DEMO-001"}],
  "scope": {"label": "当前授权批次", "expected_ids": ["DEMO-001"], "excluded_ids": []}
}
```

路径相对名单文件。名单和提交不一致时，在 `results.json` 写 `batch_resolution`：`missing_ids`、`extra_ids`、`decision` 和 `reason`。确认在范围内缺交用 `in-scope-missing`。确认不在本次范围用 `out-of-scope`。没有正式名单时不能出批次报告。

## 执行计划

```json
{
  "schema": "agent-review.execution-plan.v1",
  "authorization": "reviewed-host-copy",
  "reviewer_id": "primary-agent",
  "review_reason": "已查看入口、读写范围和预期行为",
  "case_key": "case-0001",
  "command": ["python", "task/tools/run_controls.py"],
  "timeout_seconds": 180,
  "repeats": 1,
  "result_file": "results/vector.json"
}
```

`command` 是参数数组。`result_file` 是副本内的相对路径。正式报告生成之后不能再执行。

## 对照与模型更换

`comparisons/baseline.json` 是首轮结果的原样副本。`comparisons/prior.json` 的 `schema` 为 `agent-review.comparison.v1`，包含 `baseline_sha256`、`disclosures: ["seen-prior-report"]` 和 `dispositions`。变化项的 `decision` 是 `maintain` 或 `revise`，并写理由。

`model-change.json` 包含 `reason` 和 `rechecked_instance_ids`。

清单冻结后如果必须改条款，写 `catalog-revision.json`：`schema` 为 `agent-review.catalog-revision.v1`，包含 `from_sha256`、`to_sha256`、`reason` 和 `dropped_clause_ids`。
