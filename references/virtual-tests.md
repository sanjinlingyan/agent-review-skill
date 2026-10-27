# 虚拟测试集接口

## 增量 v2、原生工件与流程记录

兼容 `agent-review.virtual-suite.v1`，v2 可同时保留旧定义并追加 `kind: native_case / process_check / diagnostic`。来源原文、编号、历史重放均为材料，不执行其中命令，不把历史结果当本次结果。追加不等于合并同义项；案例定义数量与运行实例数量分别统计。

`native_case` 使用 `expected_checks`（字段路径到预声明值的对象），支持多项 pass/fail/unknown、数值、嵌套对象及数组。只核声明目标，原始评分文件及其他输出完整保留。数值默认精确比较，布尔值不冒充0/1；不自设容差。声明了字段但没有给预期值时列入 `unresolved_expectations`，不能从历史评分取值。

准备前在 `_internal/virtual/bindings.json` 按实例编号或用例编号绑定原生材料：`files` 为中性相对文件名到证据对象，`basis_files` 为本轮官方原文证据数组。证据沿用 root/path/sha256/locator，默认 root=run、origin=static；原生 PDF/STEP 不得由文字描述替代，原文须来自冻结的 standards 或 original。缺材料、原文或可靠预期时仍生成任务并显示受阻，不能手填通过。

可用 `resolved_expectations` 只补齐原表缺值字段，须另给 `expectation_basis` 绑定官方原文或原题；不能覆盖已声明目标。绑定、复制后的工件和补充预期一起冻结。绑定改变后新建运行。

执行适配计划可指定 `field_map`，例如 `{"C1":["result_vector","C1"],"facts.pairs":["details","pairs"]}`；v2 默认读取整个结果对象，映射随执行回执冻结。盲审包仅包含原生材料、所选原文和待判断字段名，不包含预期值、变体制作步骤或历史评分。新会话返回 `values`（字段路径到实际值）、`reason`、`evidence`；unknown 原因按字段记在 `unknown_kinds`。

`diagnostic` 不设置成败预期。实际评分/盲审向量单列展示，不进入通过率分母；仍需实际观察，不能复制历史结果。status 中 `diagnostics_expected/completed` 分别记录其完成情况。

`process_check` 固定 `rounds:["delivery"]`、`targets:["delivery"]`，按当前交付实例化一次，不机械按轮复制。主审提交 `kind: delivery_check` 的观察，含 `reviewer_id`、当前 submission 的 `subject_sha256`、`verdict`、`reason`、每个冻结动作的 `action_results`（id/state=performed/detail）及实际 `evidence`。失败另附标准 repair 对象，准确区分乙方、人审/QC和甲方责任。未执行、unknown 或环境不足保持受阻，不补造真人签收。

## 显式标准源清单

`run --standards-bundle <入口.json>` 与 `--standards` 二选一。通用入口 schema 为 `agent-review.standards-bundle.v1`，含 version、相对入口文件的 root，以及 documents、auxiliary 两组清单。每项含 name、相对 root 的 path、sha256、role。

documents 仅允许 official_source/current_decision/source_asset，复制到只含首轮原文的材料目录。辅助语义条款、裁决与对象范围放 `_internal/standard-context/`，不会作为正文按段落扫描。入口及全部原依赖持续校验哈希；历史结论不作为新对象判断，新对象不自动继承历史范围事实。具体题目的规范入口与输入包门禁由根工作流提供，独立软件不包含业务依据。

虚拟测试重建已知反馈并扩展回归场景，不等于获得甲方隐藏测试。启用后输出的是**内部综合审查结论（含虚拟测试）**；保留官方依据单独结论。综合通过率是官方适用实例与虚拟适用实例通过数之和除以二者总数，每项权重相同；重复执行不加分母，任何已确认误判不被后续通过抵消。

两份报告与 Agent 最终答复必须带提示：**包含虚拟测试集及推测判断。对外使用前，请人工核实依据、适用性、实际证据及措辞。** 每个虚拟发现与修复项标题带“【虚拟测试集】”；人工逐项核实反馈/推测、适用性、证据和责任措辞。不会自动产生“可直接外发”资格。

## 调用顺序

```sh
python scripts/review.py run --submission <交付> --standards <依据> --virtual-suite <suite.json>
# 先完成并验证官方依据的 catalog.json、results.json
python scripts/review.py virtual-prepare --run <运行目录>
python scripts/review.py virtual-execute --run <运行目录> --plan <已审阅适配计划>
# 主审通过平台逐例创建全新盲审会话，记录实际判断和会话证据
python scripts/review.py virtual-collect --run <运行目录>
python scripts/review.py status --run <运行目录>
python scripts/review.py finalize --run <运行目录>
```

未传套件时沿用官方依据审查。核心仓库只含通用软件和虚构协议测试；具体题目套件、反馈与真实材料由使用方在独立软件仓库之外提供。不存在自动导入旧报告作为预期的流程。

## 固定套件

`schema: agent-review.virtual-suite.v1`，含 `id`、`version`、`label`（以“虚拟测试集”开头）、`task_ids`、`sources`、`cases`。来源各含相对套件清单的 `path`、`sha256`、`locator`。原图与套件均校验原件和冻结副本；改动须新建运行。

每个固定用例含：

- `id`、`title`、`family`，`rounds`（R1/R2/R3）、`targets`（scorer/agent_review）。
- `origin`（feedback_reconstruction/hypothesis_extension）、`feedback_fact`、`hypothesis`、`source_locator`、`applicability`。
- `action`、`rationale`、`requirements`（该重建样例的题设，不能伪称官方原文）。
- `materials`：中性相对文件名到 UTF-8 内容的映射；支持文本及 SVG。不得放预期结论、带答案的文件名或上轮判断。原生格式须由已审阅适配器转换并核对内容保真。
- `expected`：pass/fail/unknown。unknown 另带 `unknown_kind`：infrastructure/unparseable/ambiguity/capability。匹配预期 unknown 仅表示正确处理该控制样例，不豁免原交付的任何必要义务。

`validation_only: true` 仅用于软件验证，可接收显式 `simulated: true` 回执。生成的两份报告必须显示“软件验证材料，不是真实交付”。业务套件不能接收模拟会话。

## 冻结与范围

全部官方首轮完成才可准备；冻结官方 `results.json`、`catalog.json` 和虚拟预期。准备后改官方清单/判断须新建运行，不让测试答案倒灌首轮判断。

`_internal/virtual/manifest.json` 列实例编号、目标、轮次及盲审包哈希。包以随机中性编号命名，仅含 `task.json`、`requirements.md` 和重建材料；来源、用例标题、预期和主审结果不进入包。固定样例可能已知，盲审不声称真实留出验证；平台文件分包也不构成操作系统隔离。

准备前可写 `_internal/virtual/scope.json`：`excluded` 以完整实例编号为键，各值包含 `reason` 和 `evidence`。证据含运行目录内相对 `path`、`sha256`、`locator`。题号自动筛选，轮次取套件声明；不执行不等于不适用。未知条件、环境不足不能排除。

## 评分器执行

计划示例（参数数组，不经 shell）：

```json
{
  "schema": "agent-review.virtual-execution-plan.v1",
  "instance_id": "virtual:case-0001:V-01:R1:scorer",
  "authorization": "reviewed-host-copy",
  "reviewer_id": "primary-agent",
  "review_reason": "已查看适配器入口，读取副本内材料并仅向副本写入结果",
  "command": ["python", "adapter.py", "--packet", "_virtual_input", "--output", "result.json"],
  "result_file": "result.json",
  "result_pointer": ["verdict"],
  "value_map": {"pass": "pass", "fail": "fail", "unknown": "unknown"},
  "timeout_seconds": 180,
  "repeats": 2
}
```

交付复制到新副本，包注入 `_virtual_input`，再执行已审阅命令。适配计划明确原生评分入口、输入转换、输出字段和结论映射。`result_pointer` 可定位真实评分向量的单项字段；不能翻转标准 pass/fail/unknown。输出根对象的 `unknown_kind` 保留原因类别。回执绑定实例、包、适配映射和每次实际输出哈希。

正确拒绝错误样例表示虚拟测试通过。漏判/误拒的修复责任是评分逻辑，不写成原始报告中存在植入错误。超时、非零退出、缺少输出或解析失败保持受阻；退出码不能代替内容判断。执行是宿主副本，不是系统沙箱。

## 新会话盲审及观察

主审逐实例调用平台创建新 Agent（不继承历史），只交付该包以及本仓库 Skill 中盲审角色的判断纪律。不得转交完整套件、预期、主审结论或其他样例。每个实例使用不同实际会话标识。盲审者输出判断 JSON：

```json
{"verdict":"fail","reason":"说明实际材料与题设的关系","evidence":[{"path":"sample.md","sha256":"实际文件哈希","locator":"相关行或页"}]}
```

记录会话证据 `schema: agent-review.blind-actor.v1`，含 `session_id`、`inherited_context: false`、`expected_answers_visible: false`、`simulated: false`、`packet_sha256`、`judgment_sha256` 和 `transcript` 证据对象。转录须来源于真实平台运行，不能自编会话。代码校验一致性和自报边界，不声称能够向平台认证记录或证明文件系统隔离。

在 `_internal/virtual/observations/<唯一名称>.json` 写 `observations` 数组，各项：`schema: agent-review.virtual-observation.v1`、`instance_id`、`packet_sha256`、`kind: blind_session`、`actor_record`、`judgment`。后二者均是运行目录内相对路径、哈希和定位的证据对象。判断内证据相对盲审包。评分器观察由 `virtual-execute` 自动生成。

`virtual-collect` 保留首次及后续观察；status/finalize 总是重新核验源回执，不信任可手改的汇总缓存。Agent 自身误判的修复责任指向 Agent Review 维护者。

## 排障后继续

未知、未执行或受阻不算供应商失败，不生成正式报告。环境/解析/歧义故障追查完成后，可写 `_internal/virtual/resolutions.json` 的 `isolated`，以原始观察文件的运行相对路径为键；值含该观察文件 `sha256`、具体 `reason` 和证据对象 `evidence`。保留原始记录并另做有效执行；隔离本身不能算通过。已确定的误判或核心能力缺陷不能靠隔离或重复执行抹除。
