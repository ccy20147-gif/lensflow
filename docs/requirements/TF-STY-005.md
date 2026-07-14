# 官方扩写智能体

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-STY-005 |
| 标题 | 官方扩写智能体 |
| 状态 | defined |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | 小说工作区 |
| 直接依赖 | TF-STY-004、TF-AGT-001、TF-WF-008、TF-QLT-001 |
| 责任域 | 小说产品/AI |
| 个人 DRI | 待指派 |

## 2. 背景与问题

框架到正文不是无状态批量生成。章节顺序、人物状态、设定事实、伏笔和前文摘要必须持续更新，失败后还要从正确章节恢复。把每章铺成主画布节点会失控，藏成不可审计黑盒同样不可接受。

## 3. 目标与非目标

- 以一个官方扩写 Agent 内部完成章节规划、顺序扩写、记忆、连续性检查和 checkpoint。
- 支持单章重跑、RequestInput、由 Workflow 拥有的人工确认、恢复及可编辑正文工作台。
- 非目标：V1 不做多季/多卷生产调度、跨项目全局连续性或无限章节；这些归 TF-LNG-001。

## 4. 用户与权限

- V1 Core 仅当前项目 owner 可选择固定 FrameworkRevision、World/Character refs、运行、暂停、回答问题、重跑章节并提交正文 revision；项目成员能力仅在 TF-TEAM-001 后生效。
- 其他账户对来源资源的查看权不自动授予扩写、重跑或提交权。
- 所有输入、模型、工具和来源内容按固定 revision 与当前权限判断。

## 5. 用户场景与主流程

1. 项目 owner 用 FrameworkRevision 启动一个“扩写智能体”managed 节点；编译器生成可展开 `ManagedAgentTaskPlan`。
2. 第一段 AgentInvoke 生成 ChapterPlan ArtifactVersion；预定义的 workflow-owned Human Gate 固定该产物并由 owner 确认篇幅、顺序和目标，Gate 不是 Agent 内部步骤。
3. Gate 接受后，后续 AgentInvoke 以 concurrency=1 顺序写章，每章后提取记忆、人物状态、事实和伏笔账本并输出不可变 ArtifactVersion。
4. Agent 内部连续性 validator 通过后写 checkpoint，再进入下一章；阻断冲突只能 RequestInput 或以 typed failure 返回工作流，不能在 Agent 内创建 Human Gate。
5. Agent 最终输出 NovelDraftPackage ArtifactVersion；workflow-owned WorkbenchTask 固定快照，owner 在扩写工作台查看、编辑或显式请求单章重跑。
6. 工作台保存推进小说 ResourceDraft，owner 提交后由 ResourceCommit compare-and-swap 冻结小说内容 ResourceRevision。

## 6. 功能需求

- FR-1：主画布只暴露一个扩写 managed Agent 卡片；编译器必须显式物化 ChapterPlan AgentInvoke、workflow-owned Human Gate、扩写 AgentInvoke、WorkbenchTask 和 ResourceCommit，运行详情可展开全部 typed I/O。
- FR-2：V1 单次运行限一个有界作品单元：最多 30 章、总目标不超过 300,000 个 CJK 字符、单章目标 1,000–12,000 字符。
- FR-3：章节按稳定 chapter_id 和 index 顺序执行，固定 concurrency=1；每章完成后必须持久化 checkpoint。
- FR-4：每次模型调用输入预算上限为 96k tokens；超限时按固定优先级压缩，并记录保留/省略清单，禁止静默截断。
- FR-5：每章最多 3 个自动生成 attempt；耗尽后失败或 RequestInput，不得无限自迭代。
- FR-6：MemorySnapshot 至少包含当前摘要、人物状态、世界事实、关系变化、未回收伏笔、时间线和风格约束。
- FR-7：Agent 内部连续性 validator 必须在进入 N+1 章前完成；blocking 冲突不得自动批准，只能 RequestInput 或返回 typed blocking result，advisory 冲突可由固定策略记录后继续。
- FR-8：单章重跑固定其前置 checkpoint，产生新章节版本，重算后续 memory 并将受影响章节标 stale。
- FR-9：输出、内部 step、模型调用、attempt、checkpoint、RequestInput、成本和 lineage 全部可审计。
- FR-10：Agent 只能输出 ChapterPlan、ChapterVersion、MemorySnapshot、ContinuityReport 与 NovelDraftPackage 等 ArtifactVersion；不得创建/调用 Human Gate 或 WorkbenchTask，也不得写 ResourceDraft、ResourceRevision 或 ResourceCommit。
- FR-11：单章重跑必须由工作台提交显式 `WorkbenchActionRequest`，交给 Workflow 编译为新 AgentInvoke；工作台和 Agent 均不得绕过编译器直接调用模型/provider。

## 7. 交互与展示

- 画布节点显示整体进度、当前章、等待/阻断、预算和预计剩余成本。
- 工作台提供章节树、正文编辑器、人物/事实/伏笔侧栏、连续性问题和版本 diff。
- 重跑前显示固定前置上下文、将被标 stale 的后续范围和费用估算。
- 超出 V1 数量/总字数时明确引导拆分作品单元，不伪装为 V2 批量生产。

## 8. 数据、类型与公共接口

- 输入为固定 Framework ResourceRef、World/Character ResourceRefs 和扩写配置；调用使用 AgentInvoke。
- `ChapterPlan` 含 chapter_id、index、目标、POV、source stage refs、目标长度和必须覆盖 beats。
- `ChapterPlan`、`ChapterVersion`、`MemorySnapshot`、`ContinuityReport` 和 `NovelDraftPackage` 均保存为不可变 ArtifactVersion，并由 refs 组成作品内容；人工裁决另存 workflow-owned DecisionRecord，不伪装为 Agent 输出。
- 小说资源遵循 ResourceDraft/Revision；workflow-owned WorkbenchTask 冻结 Agent 输出快照并负责提交边界，编辑不覆盖运行输入或历史章节版本。

## 9. 状态机与业务规则

- Agent RunStatus、章节内部状态、RequestInput/Workflow Gate/WorkbenchTask HumanTaskStatus 和小说 RevisionStatus 分离；Gate 与 WorkbenchTask 不进入 Agent SOP。
- 章节仅按 index 推进；checkpoint 成功提交后才可调度下一章。
- 重试创建新 attempt；late result 受 epoch/attempt fencing 拒绝。
- 章节计划变更后，受影响章节和下游 memory 置 stale，不原地改历史 ArtifactVersion。

## 10. 失败、降级与恢复

- worker/模型失败从最近已提交 checkpoint 恢复，不能跳过 continuity validator 或 workflow-owned Gate。
- 上下文超限时先压缩已归档章节，再按相关性选取原文；仍超限则阻断并显示报告。
- 用户取消后停止新章调度，尽力取消在途调用，已完成章与成本保留。
- 输入撤权或安全阻断时停止新调用，隔离未完成输出并保留审计。

## 11. 安全、隐私、内容与授权

- Agent 只访问显式输入和内部生成的项目产物，不搜索未连接资源。
- 来源文本与社区 World/OC 的授权、署名和 revision 固定贯穿到小说 lineage。
- 内部工具只能走平台批准的 ToolInvocation 与凭证 broker；工作台的单章重跑必须显式创建受编译器管理的新运行。
- 用户正文、记忆和 prompt 默认私有；日志只存必要 fingerprint 与脱敏诊断。

## 12. 观测与运营

- 指标包括每章成功率/时长/成本、重试率、checkpoint 恢复率、连续性阻断率、stale 范围和上下文压缩率。
- trace 可从作品到章节、memory、模型 invocation、来源 framework/world/character 双向追踪。
- 质量按 TF-QLT-001 固定样本评估约束遵循、人物一致、伏笔闭环、重复度和人工修改率。

## 13. 验收标准

- AC-1：Given ChapterPlan ArtifactVersion 已由 workflow-owned Human Gate 确认，When 扩写 10 章，Then 严格顺序执行，每章都有版本、memory、continuity report 和 checkpoint。
- AC-2：Given 第 6 章后服务重启，When 恢复，Then 从第 7 章开始且前 6 章不重复调用/计费。
- AC-3：Given blocking 连续性冲突，When 内部 validator 返回 blocking，Then 第 N+1 章不启动，Agent 通过 RequestInput 或 typed blocking result 等待外部工作流处理，并从同一 checkpoint 恢复。
- AC-4：Given 重跑第 4 章，When 新版本完成，Then 第 5 章起相关 memory/章节标 stale，历史版本仍可读取。
- AC-5：Given 请求 31 章或超过 300,000 字符，When 提交计划，Then V1 在调用前阻断并说明 V2 范围。
- AC-6：Given 检查 AgentRevision/SOP 与运行 trace，When 完成长篇扩写和正文提交，Then Agent 内不存在 Human Gate、WorkbenchTask 或 Revision 写入，全部人工任务和 ResourceCommit 可归属到 Workflow。

## 14. 测试场景

- 正常：章节计划外部 Gate 确认、10 章顺序扩写、编辑、显式单章重跑和作品提交。
- 边界：1/30 章、1,000/12,000 字符、96k token 临界、最大总字数和零伏笔类型。
- 失败：模型超时、三次 attempt 耗尽、上下文仍超限、continuity 服务失败和取消。
- 权限：来源撤权、非 owner 重跑/提交、社区 OC 商业用途和私有正文 trace。
- 并发/恢复：重复回调、late worker、服务重启、重复回答和双编辑 CAS。

## 15. 交付与回退

- 先以 10 章内部基准灰度，再验证 30 章上限；运行上限由服务端策略强制，UI 仅作提示。
- 回退 AgentRevision 不删除章节 Artifact、checkpoint 或作品 revision；可关闭新扩写并保留编辑/导出。
- 交付证据包括长运行恢复、连续性质量报告、限制测试、单章重跑和成本对账。

## 16. 已决策事项与开放问题

- 已决策：扩写是一个官方 Agent，内部顺序执行并在每章 checkpoint，不拆成主画布章节节点。
- 已决策：扩写 Agent 内部不含 Human Gate 或工作台；单卡片由 Workflow 编译成显式 AgentInvoke、人工任务和 ResourceCommit。
- 已决策：V1 只承诺有界单作品单元；多卷、多季、跨单元调度属于 deferred TF-LNG-001。
- 开放问题：具体模型可在不改变 96k 最大输入合同的前提下使用更小能力窗口并明确报告。
