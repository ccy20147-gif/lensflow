# TF-MED-002 镜头规划智能体与 V0 ShotPlan 预览

## 1. 元数据

- ID：TF-MED-002
- 标题：镜头规划智能体与 V0 ShotPlan 预览
- 状态：defined
- 版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：影视工作区
- 直接依赖：V0：TF-STY-001、TF-WF-005、TF-WF-010；V1 Core 增加 TF-STY-006、TF-AGT-001、TF-WF-008
- 责任域：影视产品/AI
- 个人 DRI：待指派

## 2. 背景与问题

创作者需要把 idea、Creative Brief 或结构化剧本转成可编辑镜头计划，但逐镜头主画布节点会让 51 镜头项目不可管理。镜头规划应作为一个业务能力输出有序、可追溯的 ShotPlan，而逐镜编辑进入领域工作台。

V0 必须先验证“输入到 ShotPlan 预览”的价值；V1 Core 再加入固定 AgentRevision、剧本 span 覆盖、补问和 Human Gate。

## 3. 目标与非目标

- 目标：生成内容完整、顺序稳定、来源可追踪的 `ShotPlanRevision` 与其 `ShotSpecRevision` 集合，并在计划层显式表达 sequence、同一故事 Beat 的 coverage 和计划切镜关系。
- 目标：V0 支持逐镜文本、缩略图、排序、选择和人工修订，且不依赖专业控制层。
- 目标：V1 Core 对 51 镜头固定样本完成覆盖率、连续性和 TF-QLT-001 镜头规划 rubric 评测。
- 非目标：镜头规划不直接生成最终视频、音频或成片。
- 非目标：不把每个镜头、beat 或宫格单元注册成主业务画布节点。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V0 | CreativeBrief/剧本文本到 ShotPlan，逐镜预览、排序、选择、基础修订 | 已使用 ShotPlanRevision/ShotSpecRevision 的公共 ResourceRevision 模型 | 输入到至少 12 镜头计划的保存恢复 E2E |
| V1 Core | 固定 AgentRevision、结构化剧本 span、补问、51 镜头、连续性建议与 Human Gate | V0 镜头缺失的高级字段按可选默认解释，不重写旧修订 | 51 镜头覆盖、暂停恢复、并发修订和质量回归 |

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可创建、修订、排序和冻结 ShotPlan，也可在移动端执行明确允许的有限修改。
- 非 owner 不获得项目内查看、生成或提交能力；平台审核员仅按平台审核授权读取必要证据，不成为项目成员。
- 跨 owner 的世界、角色、剧本引用必须固定 revision 并携带适用 GrantSnapshot。
- Agent 只能读取编译计划授权的输入快照，不能访问其他项目或运行时 latest。

## 5. 用户场景与主流程

1. 用户在模板入口提交 Creative Brief，或在 V1 Core 选择一个固定剧本修订。
2. 系统验证 typed input、目标时长、画幅、节奏、镜头数上限、预算和授权。
3. 镜头规划能力输出 typed `ShotPlanCandidate` ArtifactVersion；信息不足时 V1 Core 由 workflow 的 RequestInput 持久等待用户，Agent 自身不创建 Gate 或工作台任务。
4. workflow-owned WorkbenchTask 从候选 Artifact 创建或更新 ShotPlan/ShotSpec ResourceDraft；用户在预览中检查 `SourceSpanCoverageReport`、逐镜叙事、StoryBeat/sequence/coverage 归属、预计时长、缩略图、排序和选择状态。
5. 修改写入各自 ResourceDraft；V0 以 compare-and-swap 冻结计划，V1 Core 通过 `ShotPlanEditSession` 的 `EditContextSnapshot` 版本向量与原子 `ShotPlanCommitManifest` 同时冻结受影响 ShotSpecRevision 和新 ShotPlanRevision。
6. 主画布只接收一个计划输出引用，下游由镜头工作台继续精修。

## 6. 功能需求

- FR-1：V0 接受 `CreativeBrief` ArtifactRef 或合法剧本文本 ArtifactRef；V1 Core 接受固定结构化剧本 ResourceRef。
- FR-2：输入约束至少含目标时长、画幅、受众、节奏、镜头数范围、内容等级、视觉方向和成本上限。
- FR-3：Agent typed output 必须是 `ShotPlanCandidate` ArtifactVersion；workflow-owned WorkbenchTask/ResourceCommit 接受候选后才产生 `ShotPlanRevision`，其 `ordered_shot_revision_refs` 只包含固定 `ShotSpecRevision`，计划级 `ShotSequenceGroup`、`CoverageGroup` 和 `CutRelation` 也只引用固定镜头修订。
- FR-4：每个 ShotSpec 至少包含 shot_id、source_spans、duration_ms、shot_role、narrative、composition 与 continuity_intent；单镜只拥有抽象意图，不复制计划顺序/关系、镜内 Beat 时间或 DirectorScene 精确空间参数，顺序只由 ShotPlanRevision.ordered_shot_revision_refs 权威表达。
- FR-5：计划总时长与镜头时长求和误差不得超过 1 帧对应时长；排序不得复制或遗漏 shot_id。
- FR-6：`SourceSpanCoverageReport` 必须列出已覆盖、重复覆盖和未覆盖的剧本 span；Creative Brief 输入则列出目标/约束覆盖。它只证明来源文本覆盖，不得被当作 `CoverageGroup`。
- FR-7：V0 支持逐镜文本、缩略图、选择、拖动排序、删除、复制和基础字段修订。
- FR-8：V1 Core 的 AgentInvoke 必须固定 agent_revision_id，记录内部 step trace、检查点、成本和 typed output 校验；Agent 输出只能是 ArtifactVersion，不能直接创建 ResourceDraft、ResourceRevision、WorkbenchTask 或 Human Gate。
- FR-9：V1 Core 信息不足时只允许 RequestInput，不允许 Agent 内嵌调用其他 Agent、Workflow 或 Recipe。
- FR-10：人工修订保存新内容 ArtifactVersion 并推进各 ResourceDraft 自身 draft_version；V0 冻结使用 compare-and-swap，V1 Core 的多镜修改必须携带 `EditContextSnapshot` 的完整版本向量并由原子 ShotPlanCommitManifest 验证，事务失败不得激活部分 Revision。
- FR-11：对 51 镜头基准运行 TF-QLT-001 的叙事覆盖、节奏、连续性、可拍摄性与重复镜头 rubric；同一 `StoryBeatRef` 的 `CoverageGroup` 必须由多个独立、可剪辑的 ShotSpecRevision 成员组成。
- FR-12：单镜删除、复制或重排必须更新计划级 sequence/coverage/cut 关系和 stale 影响，不改写历史运行；单镜候选设置使用 `ShotSetupVariant`，不得伪装成 coverage。

## 7. 交互与展示

- V0 默认显示镜头列表/故事条带，而非空白 DAG；主画布仅显示“镜头规划”一个业务节点。
- 顶部固定显示总镜头数、总时长、覆盖率、未决警告、成本和当前修订。
- 每镜头稳定显示序号、缩略图、叙事意图、景别、时长、来源 span 和选择状态。
- 桌面端支持拖动排序与多选批量修改；移动端支持查看、选择和有限文本修改。
- 质量、连续性和授权警告分级展示，并可定位到具体 shot_id/source_span。

## 8. 数据、类型与公共接口

- 输入：`ArtifactRef<CreativeBrief>`、`ArtifactRef<ScriptText>` 或 `ResourceRef<ScriptRevision>`，以及 PlanConstraints。
- 输出沿用公共 `ShotPlanRevision`、`ShotSpecRevision`、`StoryBeatRef`、`ShotSequenceGroup`、`CoverageGroup`、`CutRelation`、`ArtifactRef` 和 `ResourceRef`，不得创建平行 ShotPlanVersion。
- `SourceSpanCoverageReport` 为 ArtifactVersion 内容，包含 source_revision_ref、shot_plan_revision_ref、covered_spans、duplicate_spans、uncovered_spans、constraint_results；`CoverageGroup` 则以 story_beat_ref、coverage_objective、required_shot_roles 和 member_shot_revision_refs 表达同一故事 Beat 的多个可剪辑镜头。
- `ShotPlanCandidate` 为 Agent typed output ArtifactVersion，包含 proposed shots、source mappings、constraints、coverage report ref 和 schema revision；它不是可运行或可引用的 ShotPlanRevision。
- `StoryBeatRef` 指向剧本/故事层跨镜叙事 Beat；`BeatOrKeyframe` 只属于单镜内部时间与表演事件。`ShotSetupVariant` 只表达单镜候选设置，三者禁止互换。
- V0 `ShotPlanDraftPatch` 包含 base_revision_id、draft_version、ordered operations 和 client_request_id；V1 Core 沿用公共 ShotPlanEditSession、EditContextSnapshot、DraftVersionVectorEntry、ShotDraftRef 与 ShotPlanCommitManifest。
- 缩略图只是 selected_output_refs 或预览 ArtifactRef，不是 ShotSpec 的身份。
- 所有 Agent 执行记录关联 workflow/node run、agent_revision_id、typed input/output schema、内部 trace 和实际成本。

## 9. 状态机与业务规则

- 规划任务随公共 RunStatus/NodeRunStatus；需要补问时为 `waiting_user`，不得新造 `paused` 状态。
- ResourceDraft 可多次保存；只有 workflow-owned commit 冻结操作生成 RevisionStatus.active 的不可变计划修订，Agent 候选不能直接进入下游。
- shot_id 在同一 Shot Resource 身份内稳定；重排只改变新 ShotPlanRevision.ordered_shot_revision_refs，不改 ShotSpec 内容，新建/复制生成新 resource_id。
- 删除镜头从新计划的 ordered refs 移除，但旧 ShotSpecRevision 和旧计划保持可读。
- 相同 client_request_id 的重复补丁只应用一次；draft_version 冲突必须拒绝最后写入覆盖。

## 10. 失败、降级与恢复

- 输入 schema、授权、预算或镜头数上限不合法时在执行前阻断，并返回字段级安全错误。
- AI 输出缺字段、重复 shot_id 或时长不守恒时自动结构修复最多 2 次，仍失败则保留诊断并等待人工处理。
- 缩略图生成失败不阻断文本 ShotPlan 冻结，但明确显示缺失与重试入口，不用占位图冒充结果。
- RequestInput、刷新、worker 重启后从持久 checkpoint 恢复；重复回答按 TF-WF-008 幂等处理。
- 取消后晚到模型输出隔离为未采用 Artifact，不得覆盖当前草稿或产生下游运行。

## 11. 安全、隐私、内容与授权

- 模型调用只发送完成任务所需的最小剧本片段和允许引用；记录披露范围但不在普通日志保存全文。
- 涉及真人、未成年人、冒充、违法或无权素材的计划在生成及导出前经过 TF-SEC-001 规则。
- 来源修订撤权后阻断新规划、重跑和发布；已合法历史运行只按授权规则受限读取。
- 任何自动生成的导演风格描述不得默认使用未授权姓名作效果承诺。

## 12. 观测与运营

- 事件：shot_plan_requested、input_requested、plan_generated、draft_patched、plan_frozen、coverage_failed、plan_marked_stale。
- 指标：成功率、首次计划时延、平均补问次数、镜头数/总时长误差、覆盖率、人工修改率、每计划成本。
- 质量看板引用 TF-QLT-001 测试集版本、rubric 版本、阈值、回归差异和失败样本。
- 支持链路可从 plan revision 定位 run_id、agent_revision_id、输入修订、内部步骤和 correlation_id。

## 13. 验收标准

- AC-1：Given 合法 Creative Brief，When Agent 产生 typed ShotPlanCandidate 且 workflow-owned WorkbenchTask/ResourceCommit 接受，Then 产生可排序、可选择、可保存且刷新后恢复的 ShotPlanRevision；Agent 本身未创建任何 ResourceRevision。
- AC-2：Given 固定结构化剧本，When 生成 51 镜头，Then 每个镜头有唯一 ID/序号，时长守恒，SourceSpanCoverageReport 无未解释缺口，且 CoverageGroup 的每个成员都是同一 StoryBeatRef 下可独立编辑和剪辑的 ShotSpecRevision。
- AC-3：Given 信息缺失，When Agent 发出 RequestInput 并服务重启，Then Run 保持 waiting_user，回答后从同一 checkpoint 恢复一次。
- AC-4：Given 两个客户端编辑同一 draft_version，When 均提交，Then 一方成功、一方获得冲突 diff，历史修订不被覆盖。
- AC-5：Given TF-QLT-001 固定样本，When 回归规划，Then 所有批准 rubric 达阈值且回归差异在容差内。
- AC-6：Given 无权剧本修订，When 请求规划或重跑，Then 编译/运行前阻断且不向 provider 披露内容。

## 14. 测试场景

- 正常：Brief 到 12 镜头、剧本到 51 镜头、补问、重排、复制、删除、冻结和下游引用。
- 边界：1 镜头、51 镜头、零对白、高密度对白、极短/极长允许时长、空来源 span。
- 失败：结构化输出损坏、重复 ID、时长不守恒、缩略图失败、Agent 超时、预算不足。
- 权限：非 owner 项目访问、跨 owner 无 grant、撤权重跑、敏感剧本最小披露、越权 Agent 输入和平台审核员越界编辑。
- 并发/恢复：双端拖动、重复补丁、重复 RequestInput 回答、取消晚到、刷新/服务重启。

## 15. 交付与回退

- V0 和 V1 Agent 路径使用独立功能开关；关闭 V1 Agent 仍可读取和编辑 V0 ShotPlan。
- V0 Schema 必须向前兼容；新增剧本 span、Agent trace 和高级约束采用可选字段或新 schema version。
- 发布证据包括 V0 真实 E2E、V1 51 镜头基准、TF-QLT-001 报告、权限、并发与恢复测试。
- 回退不删除新修订；旧客户端遇到未知高级字段进入只读，禁止有损保存。

## 16. 已决策事项与开放问题

- 已决策：ShotPlan 是一个有序资源修订并唯一拥有镜头顺序、sequence/coverage 和计划 CutRelation；镜头不成为主画布节点，逐镜操作进入工作台。
- 已决策：V0 已采用正式 Revision 合同；V1 Core 只扩展能力，不进行破坏性迁移。
- 开放问题：真实 provider spike 后冻结默认镜头数建议和模型修复次数上限；不得降低 51 镜头验收边界。
