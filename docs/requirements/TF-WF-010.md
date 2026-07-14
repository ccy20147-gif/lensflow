# 通用业务节点目录与 WorkbenchTask

## 1. 元数据

- ID：TF-WF-010
- 标题：通用业务节点目录与 WorkbenchTask
- 状态：in_delivery
- 目标版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：主画布/领域工作台
- 直接依赖：TF-WF-002、TF-WF-005、TF-WF-006；V1 Core 增加 TF-WF-008
- 责任域：核心产品/工作流平台
- 个人 DRI：main-agent

## 2. 背景与问题

仅有领域 Agent 和媒体 Provider 无法复刻多模型候选、人工选择、结构化审查、手绘构图和交付打包等专业流程。若每个模板都写隐藏 API，又失去开放工作流价值。

平台需要一组跨小说、广告和影视复用的业务节点，以及能让用户进入领域工作台产出 typed 结果的 WorkbenchTask。

## 3. 目标与非目标

目标：

- 提供 Brief、Constraint、Structured Generate、Model Router、Variants、Select/Rank、Review、Transform 和 Package Export。
- 提供持久、typed、可审计的 WorkbenchTask。
- 用公共节点和领域工作台复刻两个专业基准工作流。
- 让节点保持业务粒度，不泄露模型原子。

非目标：

- 不实现任意脚本、HTTP 或系统命令节点。
- 不把每个镜头、宫格 cell 或候选变成主画布节点。
- 不让 Review 模型直接替代用户或 policy Gate。

## 4. 用户与权限

- V1 当前项目 owner 可以组合有权使用的通用节点；项目成员/协作作者能力仅在 TF-TEAM-001 后生效。
- 项目 owner 可以完成 Select/Rank 和 WorkbenchTask。
- Model Router 只能选择项目已启用的 Provider policy。
- Review 节点只获得最小输入并输出结构化报告。
- Package Export 必须通过安全和授权 Gate。

## 5. 用户场景与主流程

1. Brief 与 Constraint 节点整理用户目标、素材和禁止项。
2. Structured Generate 生成严格 schema 的创意计划。
3. Model Router 和 Variants 产生多个独立候选。
4. Select/Rank 保留全部候选并记录选择依据。
5. workflow-owned WorkbenchTask 固定输入 ArtifactVersion/ResourceRevision，要求 owner 进入分镜或图片工作台手工调整；保存产生新 ArtifactVersion/Draft，提交再冻结 ResourceRevision。
6. Review 输出问题清单，Package Export 汇总固定结果。

## 6. 功能需求

- FR-1：Brief 节点输出版本化 CreativeBrief Artifact。
- FR-2：Constraint 节点合并品牌、风格、预算、画幅、授权和禁止项，冲突需显式报告。
- FR-3：Structured Generate 必须绑定 JSON Schema 并拒绝无法解析的输出。
- FR-4：Model Router 依据固定 ProviderSelectionPolicyRef 解析候选模型。
- FR-5：Variants 为每个候选创建独立 ArtifactVersion，并保存成本分摊；一个 `ProviderInvocationAttempt/Record` 可通过多个 `ProviderOutputBinding` 绑定多个候选，不得假定每个候选各有一次外部调用。
- FR-6：Select/Rank 保存候选全集、排序、选择者、rubric 和 selected refs。
- FR-7：Review 输出 typed review report，不直接改输入或决定 policy 状态。
- FR-8：Transform 只执行注册、版本化、无任意代码的转换。
- FR-9：WorkbenchTask 必须由 Workflow 拥有，携带输入快照、目标工作台、output schema、base_revision/draft_version、ResourceCommit 策略和 HumanTaskStatus；仅在 CAS 成功后发布 committed ResourceRef。
- FR-10：Package Export 只打包固定 Revision/ArtifactVersion 和 attribution。
- FR-11：每个节点必须有 typed I/O、失败类别、成本和 lineage。
- FR-12：两个专业基准流程不得依赖模板专属隐藏 API。
- FR-13：官方 managed Agent 节点可视觉聚合为一个卡片，但编译器必须显式物化 AgentInvoke、可选 RequestInput、workflow-owned WorkbenchTask/Human Gate 和 ResourceCommit，并允许高级运行视图展开。
- FR-14：Agent 不得创建或调用 WorkbenchTask/Human Gate/ResourceCommit；自定义 Agent 需要富内容编辑时，工作流作者必须显式连接 WorkbenchTask。

### 逐版本切片矩阵

| 能力 | V0 | V1 Core |
| --- | --- | --- |
| 基础节点 | Brief、Constraint、Structured Generate、Variants、Select、Transform、Export | 完整 Model Router、Rank、Review 和可配置节点 |
| WorkbenchTask | owner 提交/取消、typed Artifact output 和基础 ResourceCommit | 接入 WF-008、强制 Gate、超时和通知 |
| 模型 | 单一或简单策略 | 版本化路由与多 Provider |
| 流程基准 | V0 广告图与 ShotPlan 模板 | 两个专业公开工作流 |
| 审计 | 输入、输出、选择和成本 | 完整 step、rubric、fallback 和权限 |

## 7. 交互与展示

- 节点卡显示业务动作和候选/选择数量，不展示内部模型原子。
- Select/Rank 使用候选对比、筛选、放大和选择理由。
- WorkbenchTask 节点展示目标工作台、处理状态和打开命令。
- Review 报告按严重度、定位和建议展示，不自动覆盖作品。
- Package Export 展示包含项、授权、署名和阻断项。

## 8. 数据、类型与公共接口

节点输入输出使用注册 schema 的 ArtifactRef/ResourceRef。CandidateSet 保存 ordered candidate refs，不复制候选内容。

SelectionRecord 包含 candidate_set_ref、ranking、selected_refs、actor_or_model、rubric_revision 和 rationale。

WorkbenchTask 遵循主表第 8.2 节，至少保存 `owner_workflow_revision_id`、固定 `input_snapshot_refs`、`base_revision_id`、`expected_draft_version`、`resulting_artifact_refs` 和 `committed_resource_refs`。Provider 节点使用主表第 8.4 节的 Attempt/Record/OutputBinding 模型记录。

`ManagedAgentTaskPlan` 是 Workflow 编译产物，不是 AgentRevision 内容；每一步必须有 typed bindings、owner layer、状态和失败归属。

## 9. 状态机与业务规则

节点执行使用 NodeRunStatus。WorkbenchTask 使用 HumanTaskStatus，并使对应父 NodeRun 进入 waiting_user；只有没有其他必需工作可推进时，Run 才聚合为 waiting_user。

WorkbenchTask accepted、ResourceCommit committed 和下游 ref published 是可区分状态；AgentInvoke succeeded 或 Draft 保存均不能提前发布 ResourceRef。

模型排名不能覆盖人工选择；重新选择创建新 SelectionRecord。

Package Export 只能消费固定 refs；有 stale 或无权输入时按策略阻断。

## 10. 失败、降级与恢复

- Structured Generate schema 失败按策略重试，仍失败则产生安全错误。
- 部分 Variants 失败时保留成功候选并报告失败比例。
- Rank 模型不可用时允许人工选择，不伪造模型分数。
- WorkbenchTask 中断后从 Draft 恢复，重复提交按 task version、draft_version 和 idempotency key 幂等；CAS 冲突显示三方 diff。
- Export 中单项无权时阻断或按明确用户选择排除，并重算 manifest。

## 11. 安全、隐私、内容与授权

- Constraint 与 Export 强制携带素材权利和内容安全结果。
- Model Router 不能选择未启用 Provider 或泄露密钥。
- Review 和 Rank 输入做最小披露与 prompt injection 隔离。
- WorkbenchTask 按 owner_scope 访问固定输入。
- 导出 manifest 保留 attribution，客户端不能无痕删除。

## 12. 观测与运营

- 记录节点使用、schema 失败、候选数、选择时长、人工覆盖和导出阻断。
- 对 Variants 成本、Router 选择和 Review 命中做版本化统计。
- WorkbenchTask 监控等待时间、取消和恢复。
- 两个专业基准流程保存逐节点 trace 与复现报告。

## 13. 验收标准

- AC-1：V0 广告图和 ShotPlan 模板只使用公共节点、领域节点和工作台即可运行。
- AC-2：Structured Generate 无法满足 schema 时不发布伪造 ArtifactVersion。
- AC-3：三个候选中一个失败时，成功候选、失败记录、成本和人工选择均完整保存。
- AC-4：WorkbenchTask 刷新后仍可进入目标工作台并提交符合 schema 的结果。
- AC-5：Package Export 遇到无权 ResourceRef 时阻断并定位具体输入。
- AC-6：两个专业基准工作流可由注册节点复刻，主画布不展开逐镜或逐候选原子。
- AC-7：managed Agent 单卡片编译后可展开 AgentInvoke、人工任务和 ResourceCommit；Agent trace 不含这些 workflow task，且下游只在 ResourceCommit 成功后收到 ResourceRef。

## 14. 测试场景

- 正常：Brief 到候选、人工精修、Review 和 Export。
- 边界：空候选、最大候选数、冲突 Constraint、大包和 51 镜头集合。
- 失败：schema、Provider、部分候选、WorkbenchTask 和 Export 失败。
- 权限：无权模型、跨 owner ResourceRef、敏感 Review 和导出授权。
- 并发/恢复：重复候选回调、同一 owner 重复选择、非 owner 提交、工作台 Draft 冲突和重启恢复。

## 15. 交付与回退

- V0 先交付最小节点集合和简单 WorkbenchTask。
- V1 Core 增加完整路由、审查、强制人工任务和专业基准。
- 每个节点独立功能开关，关闭时编译器提供替换诊断。
- 回退节点 revision 不修改历史 WorkflowRevision 或 ArtifactVersion。

## 16. 已决策事项与开放问题

已决策：通用业务节点是开放模板的公共积木；富内容编辑进入 workflow-owned WorkbenchTask；Agent 只产出 ArtifactVersion，ResourceRevision 在工作流提交边界冻结。

开放问题：首批 Rank/Review rubric 由 TF-QLT-001 基线确定，不能在节点内部硬编码未版本化评分标准。
