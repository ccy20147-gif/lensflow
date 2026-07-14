# 官方世界观智能体与工作台

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-STY-002 |
| 标题 | 官方世界观智能体与工作台 |
| 状态 | defined |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | 小说工作区 |
| 直接依赖 | TF-STY-001、TF-AGT-001、TF-WF-005、TF-WF-008 |
| 责任域 | 小说产品/AI |
| 个人 DRI | 待指派 |

## 2. 背景与问题

故事创作需要共享的世界规则、势力、地点、历史、视觉语言和角色锚点。若这些内容只藏在聊天记录里，后续框架、扩写、资产和社区引用无法固定版本或检查冲突。

## 3. 目标与非目标

- 提供官方世界观 Agent，将 Creative Brief 转成结构化 World 内容并包含初始 OC 集。
- 由主 Workflow 编排工作台编辑、补问、确认、版本化和跨流程引用；官方 Agent 只生成 typed ArtifactVersion。
- 非目标：不在本项生成完整小说、镜头媒体或独立 OC listing；Agent 不嵌套其他 Agent/Workflow/Recipe。

## 4. 用户与权限

- V1 Core 仅当前项目 owner 可运行官方 Agent、回答补问、编辑 WorldDraft 和提交 WorldRevision；项目成员能力仅在 TF-TEAM-001 后生效。
- 当前 owner 决定冻结 revision、引用范围和后续发布；其他账户的查看权不自动包含派生权。
- 跨 owner 输入 ResourceRef 必须固定 revision 并在新运行时通过当前授权。

## 5. 用户场景与主流程

1. 项目 owner 在一个官方 managed 节点中选择 CreativeBrief ArtifactRef，可附加已有 World/Character refs。
2. 编译器把单卡片物化为 `ManagedAgentTaskPlan`；其中 AgentInvoke 校验输入，发现关键缺口时只可通过 RequestInput 持久补问。
3. AgentInvoke 输出 WorldPackage ArtifactVersion，包含规则、势力、地点、时间线、视觉圣经和初始 OC，不创建或修改 ResourceDraft/Revision。
4. workflow-owned WorkbenchTask 冻结该 ArtifactVersion 作为输入快照；owner 在世界观工作台按结构编辑并检查矛盾，保存产生新内容 ArtifactVersion 和递增 WorldDraft。
5. owner 提交后，工作流层以 compare-and-swap 执行 ResourceCommit，生成固定 WorldRevision；下游框架和资产节点只消费该 ResourceRef。

## 6. 功能需求

- FR-1：官方能力在画布上保持一个 managed Agent 节点，但编译器必须生成可展开的 `ManagedAgentTaskPlan`，显式列出 AgentInvoke、可选 RequestInput、workflow-owned WorkbenchTask 和 ResourceCommit 及其 typed I/O。
- FR-2：WorldPackage 至少包含基本设定、物理/魔法/社会规则、势力、地点、历史时间线、术语、主题边界、视觉圣经和初始 OC 集。
- FR-3：每个实体必须有稳定局部 ID、名称、别名、描述、关系和来源字段，禁止仅按显示名关联。
- FR-4：关键输入缺失时可使用 TF-WF-008 RequestInput；答案写入 checkpoint，恢复不得重复已完成步骤。
- FR-5：workflow-owned WorkbenchTask 必须固定 Agent 输出快照，并支持实体级编辑、关系浏览、冲突检查、diff、引用定位、取消和 owner 提交。
- FR-6：Agent 输出只能成为符合 `WorldPackage` schema 的 ArtifactVersion；Agent 不得创建/调用 WorkbenchTask 或 Human Gate，也不得写 ResourceDraft、ResourceRevision 或 ResourceCommit。
- FR-7：世界变更只创建新 revision，并将依赖草稿标记 stale；不得覆盖历史运行和下游 revision。
- FR-8：初始 OC 可保留在 WorldRevision 内，也可由 TF-STY-003 提升为独立 Character Resource。
- FR-9：所有内部步骤、补问、模型调用、成本、输入与输出 lineage 必须可审计。
- FR-10：WorkbenchTask 提交必须以 base_revision/draft_version compare-and-swap 冻结 WorldRevision；只有提交成功后的 ResourceRef 才对下游可见。

## 7. 交互与展示

- 工作台提供概览、规则、势力、地点、时间线、视觉圣经和角色标签页。
- 关系使用可筛选图/表双视图；长内容进入工作台，不塞入主画布节点卡片。
- 冲突项显示涉及实体、规则与来源，允许接受建议或手动修订。
- 主画布节点只显示 Agent 产物、已提交 revision、完成度、待确认项、初始 OC 数量和进入工作台操作；运行详情可展开聚合节点的实际任务计划。

## 8. 数据、类型与公共接口

- `WorldPackage` 是注册 Artifact schema；每次 AgentInvoke 只返回 ArtifactRef。workflow-owned WorkbenchTask/ResourceCommit 才能形成 `Resource(resource_type=world)`、WorldDraft 与 WorldRevision。
- World 内容扩展 `rules[]`、`factions[]`、`locations[]`、`timeline_events[]`、`glossary[]`、`visual_bible`、`embedded_characters[]`。
- embedded character 使用 `world_local_character_id`；关系只引用稳定 ID。
- 调用使用 AgentInvoke；`ManagedAgentTaskPlan` 由 WorkflowRevision/编译器拥有，不属于 AgentRevision。跨流程使用 ResourceRef，运行固定 revision_id。

## 9. 状态机与业务规则

- Agent RunStatus、RequestInput/WorkbenchTask HumanTaskStatus、World Draft/RevisionStatus 分离；Agent 成功不等于 WorldRevision 已提交。
- RequestInput 重复回答以 task_id/answer hash 幂等；过期任务不可静默恢复。
- WorldDraft 保存递增 draft_version；确认使用 compare-and-swap。
- 冲突检查结果是派生报告，不自行修改 World 内容；owner 保存产生新 ArtifactVersion，成功提交后才产生新 WorldRevision。

## 10. 失败、降级与恢复

- Agent 输出缺字段或引用损坏时有限重试，仍失败则保留诊断 Artifact，不生成可确认 WorldRevision。
- 模型不可用时保留已完成 checkpoint 和用户回答，可更换允许模型后从边界恢复。
- 工作台保存冲突显示三方 diff，禁止最后写入覆盖。
- 输入 revision 撤权后阻断新运行；历史合法运行和快照按授权规则留存。

## 11. 安全、隐私、内容与授权

- 用户私有世界内容默认仅项目可见；官方 Agent 不改变其发布或训练授权。
- 引用外部世界/OC 时最小披露，并记录 revision、role 和 GrantSnapshot。
- 若官方 Agent 使用工具，只能走平台批准的 ToolInvocation 与凭证 broker；工作台不得直接调用 provider 或改写 Run/NodeRun。
- 暴力、未成年人、真人影射和受保护素材按平台内容/权利 Gate 处理。

## 12. 观测与运营

- 记录 AgentRevision、输入 refs、内部步骤、RequestInput、WorldPackage、草稿/修订和冲突报告。
- 指标包括首次完成率、补问率、schema 合格率、冲突密度、工作台修改率和版本确认率。
- 质量回归以固定 Brief 样本检查结构完整、约束遵循、实体一致和初始 OC 可用性。

## 13. 验收标准

- AC-1：Given 合法 Brief，When Agent 完成、owner 在 workflow-owned WorkbenchTask 提交，Then Agent 先产生 WorldPackage ArtifactVersion，ResourceCommit 再生成含全部必需域及至少一个稳定局部 ID 初始 OC 的 WorldRevision。
- AC-2：Given 关键设定缺失，When Agent RequestInput 后服务重启，Then 用户回答可恢复同一运行且不重复前置模型调用。
- AC-3：Given 用户编辑规则造成实体冲突，When 检查，Then 报告定位双方 ID/来源且未经确认不改内容。
- AC-4：Given WorldRevision A 已被运行引用，When 创建 B，Then A 的历史输出不变，依赖草稿只标 stale。
- AC-5：Given 跨 owner WorldRef 当前无授权，When 新运行，Then 被阻断；历史 GrantSnapshot 不被误当当前权限。
- AC-6：Given 打开 managed 节点高级运行视图，When 编译完成，Then 可见 AgentInvoke、RequestInput（如有）、WorkbenchTask 和 ResourceCommit，且 Agent trace 中不存在 Gate、Workbench 或 Revision 写入。

## 14. 测试场景

- 正常：Brief 生成、补问、工作台编辑、确认、二次修订和下游引用。
- 边界：大型实体关系、同名角色、多语言术语、零势力/多个时间线、空视觉参考。
- 失败：坏 schema、模型超时、冲突检查失败、引用损坏和保存 CAS 冲突。
- 权限：非 owner 编辑/提交、跨 owner 引用、撤权重跑、私有字段展示和发布权隔离。
- 并发/恢复：重复回答、同一 owner 双标签编辑、Agent checkpoint、服务重启和事件重放。

## 15. 交付与回退

- 先对内置模板启用官方 Agent，再开放自由 Brief；工作台和 Agent 可分别受功能开关控制。
- 回退 AgentRevision 不改变已生成 WorldRevision；旧 schema 通过只读兼容层展示。
- 交付证据包括固定样本质量报告、补问恢复、lineage、并发编辑和权限 E2E。

## 16. 已决策事项与开放问题

- 已决策：官方世界观 Agent 必须包含初始 OC 设计，世界内容以 WorldRevision 固定。
- 已决策：官方节点是视觉聚合；Agent 只产出 ArtifactVersion，工作台任务和 Revision 提交由 Workflow 拥有。
- 已决策：独立 OC 提升由 TF-STY-003 承担，提升不回写历史 WorldRevision。
- 开放问题：无阻塞 V1 Core 的开放问题。
