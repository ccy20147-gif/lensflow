# 官方小说框架智能体

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-STY-004 |
| 标题 | 官方小说框架智能体 |
| 状态 | defined |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | 小说工作区 |
| 直接依赖 | TF-STY-001、TF-STY-002、TF-STY-003、TF-AGT-001、TF-WF-008 |
| 责任域 | 小说产品/AI |
| 个人 DRI | 待指派 |

## 2. 背景与问题

SeedV 参考实现定义的是从“初始核心骨架搭建”到“主题升华与续作延展迭代”的六阶融合迭代，不是传统六段式情节标签。该方法必须完整、顺序执行并整体校验；拆成主画布节点会让用户跳阶、误删合同或产生不合法半成品。

## 3. 目标与非目标

- 以一个官方 Agent 完成不可拆的六阶段框架、多线程故事线和严格结构输出。
- Agent 内部阶段保留 typed contract、validator、检查点、补问和 trace；外部以一个 managed 业务节点聚合展示，由 Workflow 编排工作台和资源提交。
- 非目标：不把六阶段注册为六个画布节点，不生成章节正文，也不嵌套调用其他 Agent/Workflow/Recipe。

## 4. 用户与权限

- V1 Core 仅当前项目 owner 可选择固定 Brief、WorldRevision 和 CharacterRevision、运行 Agent、回答补问并提交 FrameworkRevision；项目成员能力仅在 TF-TEAM-001 后生效。
- 其他账户即使可查看来源，也不自动获得框架编辑、派生或商业使用权。
- 输入资源按 revision 与当前权限校验；查看框架不自动授予来源资源派生或商业权。

## 5. 用户场景与主流程

1. 项目 owner 在一个“小说框架智能体”managed 节点绑定 idea/Brief、世界观和 OC。
2. 编译器生成可展开的 `ManagedAgentTaskPlan`；AgentInvoke 检查冲突与关键缺口，必要时只通过 RequestInput 补问。
3. Agent 内部严格执行 Stage 0 初始核心骨架搭建、Stage 1 核心维度迭代、Stage 2 主线多轮重构迭代、Stage 3 支线衍生与网状结构迭代、Stage 4 逻辑闭环与细节优化迭代、Stage 5 主题升华与续作延展迭代。
4. 每阶段先输出自己的 typed stage artifact，再通过该阶段独立 validator；六阶段全部通过后才 assembly 为 NovelFramework ArtifactVersion。
5. workflow-owned WorkbenchTask 固定该 ArtifactVersion，owner 在框架工作台整体查看和局部修订；保存产生新 ArtifactVersion/FrameworkDraft，提交由 ResourceCommit 冻结 FrameworkRevision 供扩写使用。

## 6. 功能需求

- FR-1：主画布和节点注册表只能暴露一个官方小说框架 Agent 节点。
- FR-2：内部阶段名称和顺序必须精确固定为：Stage 0 初始核心骨架搭建、Stage 1 核心维度迭代、Stage 2 主线多轮重构迭代、Stage 3 支线衍生与网状结构迭代、Stage 4 逻辑闭环与细节优化迭代、Stage 5 主题升华与续作延展迭代；不得重命名为六段式情节结构。
- FR-3：六个阶段必须分别注册独立 schema identity/version、typed contract 和 deterministic validator；每阶段记录稳定 stage_id、输入快照、结构结果、自检、继承摘要、校验报告、模型调用、成本和 checkpoint。
- FR-4：框架至少包含核心命题、人物弧、主线/对抗/情感线程、关键转折、伏笔/回收、阶段节拍和结局余韵。
- FR-5：输入只接受固定 CreativeBrief ArtifactRef、WorldRevision、CharacterRevision 或兼容 typed refs，不读取 latest。
- FR-6：关键意图或矛盾无法自动裁决时通过 TF-WF-008 RequestInput，恢复后从最近合法 checkpoint 继续。
- FR-7：任何阶段缺失、typed schema/validator 未通过或违反世界/角色硬约束时，禁止 assembly；Agent 成功只能输出 NovelFramework ArtifactVersion，不能直接生成 FrameworkRevision。
- FR-8：workflow-owned WorkbenchTask 允许编辑阶段内容，但保存的是一个整体 FrameworkDraft；提交以 compare-and-swap 冻结 FrameworkRevision，不得产生可独立运行的阶段资源。
- FR-9：单阶段内部重算必须传播后续阶段 stale 并重新整体校验，不能拼接未经复核的旧结果。
- FR-10：managed 节点视觉上保持一个卡片，但编译计划必须显式物化 AgentInvoke、可选 RequestInput、WorkbenchTask 和 ResourceCommit；Agent 不得创建/调用 WorkbenchTask 或 Human Gate，也不得写 ResourceDraft/Revision。

## 7. 交互与展示

- 画布节点显示整体状态、当前内部阶段、待补问、质量阻断和成本，不暴露六个连线端口。
- 工作台用六阶段的精确名称导航，并以多线程故事地图展示同一框架数据，支持来源、typed 校验报告与约束侧栏。
- 未完成阶段可查看但明确标记“内部草稿，不可下游使用”。
- 修订 diff 同时展示阶段变化及其对人物弧、伏笔和后续阶段的影响。

## 8. 数据、类型与公共接口

- `NovelFramework` 是注册 Artifact schema；AgentInvoke 只输出其 ArtifactRef。workflow-owned WorkbenchTask/ResourceCommit 才可提升为 `Resource(resource_type=novel_framework)` 的 ResourceRevision。
- 内容扩展 `premise`、`stage_sections[6]`、`story_threads[]`、`character_arcs[]`、`turning_points[]`、`foreshadowing_ledger[]`、`constraint_check_report`。
- 内部 `FrameworkStageTrace` 是 Agent trace/checkpoint，不是可独立调用的 Workflow node 或 Resource。
- 六阶段 typed contract 以 SeedV 参考代码 `opensource/seedv/backend/app/schemas/script_stage_contracts.py` 的 `Stage0Artifact` 至 `Stage5Artifact` 为领域基线，并结合 `opensource/seedv/skills/script-expert/prompts/framework-architect-system.txt` 与 `opensource/seedv/skills/script-expert/agents/framework-architect.yaml` 的阶段目标；ToonFlow 必须重新注册自身 schema/version，不能在生产运行时依赖 SeedV 代码。

| 阶段 | schema_id / typed contract（参考模型） | validator_id | 独立 validator 的最低职责 |
| --- | --- | --- | --- |
| Stage 0 初始核心骨架搭建 | `toonflow.novel_framework.stage0@1` / `Stage0Artifact` | `novel_framework_stage0_validator@1` | 校验唯一核心目标、激励事件、终极高潮、完整五节拍；禁止分章/分集和后阶扩写内容 |
| Stage 1 核心维度迭代 | `toonflow.novel_framework.stage1@1` / `Stage1Artifact` | `novel_framework_stage1_validator@1` | 校验对立型/情感型必选支线、各自五节拍、主角立体性三步法及与主线绑定 |
| Stage 2 主线多轮重构迭代 | `toonflow.novel_framework.stage2@1` / `Stage2Artifact` | `novel_framework_stage2_validator@1` | 校验主线子循环、扩写公式、支线演化/交汇、冲突密度和 Stage 0 锚点只读继承 |
| Stage 3 支线衍生与网状结构迭代 | `toonflow.novel_framework.stage3@1` / `Stage3Artifact` | `novel_framework_stage3_validator@1` | 校验子支线父级/主线绑定、五节拍、多线交汇、配角弧和支线权重 |
| Stage 4 逻辑闭环与细节优化迭代 | `toonflow.novel_framework.stage4@1` / `Stage4Artifact` | `novel_framework_stage4_validator@1` | 校验问题到修复方案可追踪、锚点未改、伏笔回收、人物一致与节奏/冲突密度 |
| Stage 5 主题升华与续作延展迭代 | `toonflow.novel_framework.stage5@1` / `Stage5Artifact` | `novel_framework_stage5_validator@1` | 校验主题传递、全线 payoff、人物弧闭环、主线明确结局、续作伏笔有前置铺垫及扩写就绪 |

- 每个 validator 必须输出独立 `StageValidationReport(valid, contract_version, schema_version, schema_name, errors, warnings, repair_attempts, raw_output_hash)`；不能用一个“markdown_section_check 通过”代替结构和跨阶段语义校验。
- 调用严格使用 AgentInvoke，Agent 输出只能是 ArtifactRef；固定 Framework ResourceRef 由后续 workflow task 产生。

## 9. 状态机与业务规则

- Agent Run/NodeRun、RequestInput/WorkbenchTask HumanTask、Framework Draft/RevisionStatus 分离；Agent `succeeded` 不代表 ResourceCommit 成功。
- 内部阶段只能 pending -> running -> validating -> passed/failed；六个 typed validator 均 passed 后才可 assembled 为 ArtifactVersion。
- 阶段重跑创建新 attempt，后续阶段置 stale；旧 checkpoint 和输出保持审计可读。
- 相同 run/checkpoint/answer 的恢复幂等，禁止跳过尚未通过的阶段。

## 10. 失败、降级与恢复

- 模型或校验失败按阶段有限重试；耗尽后停在该阶段，不生成伪完整框架。
- 服务重启从最近 passed checkpoint 恢复，已通过阶段不重复计费。
- 输入 revision 在运行前失权则阻断；运行中发生安全撤权按策略取消/隔离。
- 工作台并发冲突显示三方 diff；阶段顺序、schema identity 和整体合同不可被手工删除或降级。

## 11. 安全、隐私、内容与授权

- Agent 只接收连接且授权的内容，不读取项目中未绑定的世界或角色。
- 来源文本、世界和 OC 的 revision、角色、GrantSnapshot 与当前权限决策均写入 lineage。
- 内部工具只能走平台批准的 ToolInvocation 与凭证 broker；工作台重算必须显式请求新运行，不能直接调用 provider。
- 内容安全阻断必须可定位阶段和规则，不能以空内容补齐六阶段。

## 12. 观测与运营

- 记录各阶段时长、attempt、checkpoint、补问、schema/约束检查、成本与最终版本。
- 指标包括六阶段完成率、阶段失败分布、补问率、重跑率、约束违规率和用户修改率。
- 质量评测使用固定 idea/world/OC 样本和统一 rubric，比较结构完整、线程闭环、角色弧与约束遵循。

## 13. 验收标准

- AC-1：Given 合法 idea/world/OC，When Agent 完成，Then 单个节点先输出含六个精确命名阶段、六份 StageValidationReport 且整体校验通过的 NovelFramework ArtifactVersion。
- AC-2：Given Stage 3 支线衍生与网状结构迭代失败，When 重试耗尽，Then 不产生可下游消费的 Framework ArtifactVersion/Revision，Stage 0 至 Stage 2 checkpoint 可恢复。
- AC-3：Given owner 修改 Stage 1 核心维度迭代，When 保存并重算，Then Stage 2 至 Stage 5 标 stale 并在整体校验前不得拼接旧结果。
- AC-4：Given RequestInput 后刷新和服务重启，When 用户回答，Then 从同一阶段 checkpoint 恢复且不重复前序阶段。
- AC-5：Given 用户尝试把六阶段拆为独立画布节点，When 查询注册表/编译，Then 不存在可连接的阶段节点类型。
- AC-6：Given 打开 managed 节点高级运行视图，When FrameworkRevision 最终提交，Then 可追踪 Agent ArtifactVersion、workflow-owned WorkbenchTask 和 ResourceCommit，Agent trace 中不存在 Gate/Workbench/Revision 写入。

## 14. 测试场景

- 正常：六个精确命名阶段、独立 typed validator、多个故事线程、补问、工作台修订、提交和扩写输入。
- 边界：最小短篇、复杂多角色、无情感线的合法类型、最大允许框架体量。
- 失败：阶段缺失、顺序错乱、约束冲突、模型超时、schema 失败和 stale assembly。
- 权限：非 owner 修改/提交、无权 World/Character、撤权重跑和来源正文脱敏。
- 并发/恢复：阶段 attempt fencing、重复回答、编辑 CAS、worker 重启和事件重放。

## 15. 交付与回退

- 官方 AgentRevision 通过 managed preset 灰度；旧 revision 可回滚式激活但运行始终固定 ID。
- 功能关闭时保留 FrameworkRevision 的工作台只读与导出，不拆分为临时节点替代。
- 发布证据包括六阶段状态 contract tests、恢复演练、固定样本质量报告和画布抽象检查。

## 16. 已决策事项与开放问题

- 已决策：六阶段框架不可分割，主画布永远是一个官方 Agent 节点。
- 已决策：内部可审计不等于外部原子化；阶段 trace 不能被其他节点直接调用。
- 已决策：六阶段语义与 typed contract 以 SeedV 参考实现为基线；Agent 只产出 ArtifactVersion，工作台和 ResourceCommit 属于 Workflow。
- 开放问题：无阻塞 V1 Core 的开放问题。
