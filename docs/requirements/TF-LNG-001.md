# 生产级长篇、长剧集与批量调度

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-LNG-001 |
| 标题 | 生产级长篇、长剧集与批量调度 |
| 状态 | deferred |
| 版本 | V2 |
| 优先级 | P1 |
| 全局位置 | 小说/影视工作区/运行内核 |
| 直接依赖 | TF-STY-005、TF-STY-006、TF-WF-007、TF-MED-002、TF-MED-012、TF-NFR-001、TF-NFR-002 |
| 责任域 | 长内容产品/运行时 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

V1 支持有界单作品单元，但生产级长篇/剧集需要跨卷、季、集、章的连续性、变更传播、批量审批、资源调度与长周期恢复。把无限单元塞进一个 Run 会使预算、状态和恢复不可治理。

## 3. 目标与非目标

- V2 建立分层生产单元、跨单元连续性、批量调度、影响分析、审批与恢复。
- 以多个有限子运行组合长内容，保持每个 revision/运行/成本可追踪。
- 非目标：本项不进入 Foundation/V0/V1/V1.5 交付门，不允许 V1 以“未来兼容”为由实现无界循环；不建设完整制片财务或 DCC。

## 4. 用户与权限

- 制片/主创创建生产计划、锁定共享圣经、排批、暂停和批准批次。
- 作者/分镜师处理被分配单元；审阅者按权限批准或退回。
- 所有批量动作在项目/团队权限、资源授权、预算和内容 Gate 交集内执行。

## 5. 用户场景与主流程

1. 用户创建一季 12 集计划，固定 World/Character/Style/Script 基线 revision。
2. 系统按集拆出有界 ProductionUnit，每集包含剧本、ShotPlan、镜头与时间线子流程。
3. 用户先跑试点集，确认质量/成本后分批调度其余单元。
4. 第 3 集角色设定更新时，系统计算第 3 集之后的影响图，只标 stale 并生成重算提案。
5. 用户批量批准重算范围；worker/服务重启后从各单元 checkpoint 恢复。

## 6. 功能需求

- FR-1：V2 内容层级至少支持 Production -> Volume/Season -> Chapter/Episode -> Scene/Shot，并有稳定 unit_id、顺序和父子关系。
- FR-2：每个 ProductionUnit 必须固定输入 revisions、WorkflowRevision、预算、负责人、交付状态和输出 refs，禁止运行中读取 latest。
- FR-3：跨单元 ContinuityLedger 至少跟踪人物状态、关系、世界事实、时间线、伏笔、资产/风格和已发生事件。
- FR-4：共享上游 revision 变化必须计算受影响单元/字段/成本，只标 stale；任何批量重算需用户确认。
- FR-5：BatchPlan 必须有最大单元数、并发、优先级、预算、重试、窗口和暂停/取消策略，不允许无限队列扩张。
- FR-6：审批支持按单元、批次和门槛聚合，但每个决定必须可下钻到固定产物和 reviewer。
- FR-7：调度器支持配额公平、provider 限流、成本预留、依赖就绪、checkpoint 恢复和 attempt fencing。
- FR-8：失败单元可独立恢复/替换，不重跑已批准且输入未变的其他单元。
- FR-9：V2 验收基准至少包含 12 集 × 每集 51 镜头，以及 3 卷 × 每卷 30 章的独立测试项目。

## 7. 交互与展示

- 生产控制台用层级树、批次表、连续性问题、影响图、成本/进度和审批队列组织信息。
- 主业务画布仍显示业务能力节点，不展开 612 个镜头或 90 章节点。
- 批量操作在确认前展示选择范围、stale 原因、预计成本、不可取消任务和授权缺口。
- 单元可深链到小说、剧本、分镜或时间线工作台，返回时保留筛选与批次上下文。

## 8. 数据、类型与公共接口

- `Production`/`ProductionUnit` 使用 Resource/ResourceRevision；可变计划使用 ResourceDraft。
- `BatchPlanRevision` 含 ordered unit refs、dependency graph、concurrency/budget/retry policy 和 approval policy。
- `ContinuityLedgerRevision` 通过实体/事实稳定 ID 与 source unit/revision refs 关联。
- 单元输出继续使用 ArtifactRef/ResourceRef、ShotPlan/ShotSpec 和 Timeline 合同，不建立 V2 平行版本真相。

## 9. 状态机与业务规则

- ProductionUnit、BatchRun、NodeRun、HumanTask 与 Revision 状态必须分离；聚合进度由子状态派生。
- 批次确认固定 plan hash；计划或授权变化使确认失效。
- stale 传播不修改已批准 revision；重算生成新 revision 并允许比较/回滚式选择。
- 批次取消停止未启动单元，对在途任务尽力取消，已完成结果与实际成本保留。

## 10. 失败、降级与恢复

- 调度器不可用时不丢任务；恢复后基于 lease/epoch 重新认领，晚到结果隔离。
- provider 容量下降时按公平/优先级降低并发并重估 ETA，不静默更换质量策略。
- 连续性服务不可用时阻断依赖单元批准，已完成生成可保存为未批准候选。
- 灾难恢复目标沿用 TF-NFR-002；V2 上线前必须以完整批次做恢复演练。

## 11. 安全、隐私、内容与授权

- 每个单元在调度与发布时重新计算当前授权；历史 GrantSnapshot 只证明旧动作。
- 批量操作不得扩大操作者对未分配单元、敏感素材或团队资源的可见范围。
- 法律/安全暂停可冻结受影响单元并重算影响图，不删除隔离审计。

## 12. 观测与运营

- 指标包括单元吞吐、排队/运行/等待时长、并发利用、预算偏差、stale 范围、恢复成功和跨单元连续性缺陷。
- 12×51 基准在一次 worker 重启与一次 provider 降级注入后必须完成，且无重复接受 attempt/重复计费。
- 运营可按 production/batch/unit/run/provider 追踪，不跨 owner 暴露正文。

## 13. 验收标准

- AC-1：Given 12 集 × 51 镜头计划，When 分批执行，Then 每集独立版本/成本/审批可追踪，主画布不展开为镜头节点。
- AC-2：Given 第 3 集共享角色 revision 改变，When 计算影响，Then 仅相关后续单元标 stale，历史产物未被改写。
- AC-3：Given 批次执行中 worker 重启，When 恢复，Then 从各自 checkpoint 继续且旧 epoch 结果不覆盖新 attempt。
- AC-4：Given 用户批量取消，When 部分单元已完成，Then 未启动任务不再调度，已完成产物/成本保留并可审计。
- AC-5：Given V1 环境，When 请求创建无界季/集批次，Then 功能不可用且不影响 V1 有界单项目流程。

## 14. 测试场景

- 正常：12 集生产、90 章项目、试点后扩批、批量审批和局部重算。
- 边界：单单元、最大批次、依赖菱形、零变更影响和预算临界。
- 失败：worker/provider/连续性服务故障、批次部分失败、恢复和取消。
- 权限：跨团队单元、撤权、批量越权、法律暂停和审计访问。
- 并发/恢复：调度公平、重复回调、late attempt、计划 CAS 和灾难恢复。

## 15. 交付与回退

- 本项保持 deferred，必须另行通过 V2 立项、容量测试、迁移 ADR 和运营 Gate 才可进入 delivery。
- V2 以独立生产调度开关启用；关闭时单个 V1 workflow/作品仍可运行和编辑。
- 交付证据包括两类规模基准、影响传播、批量审批、成本和灾难恢复报告。

## 16. 已决策事项与开放问题

- 已决策：V1 只支持有界单项目/作品单元；V2 长内容由多个固定、有限单元组成，禁止无界单 Run。
- 已决策：跨单元变更只标 stale 并经确认重算，不追溯改写历史。
- 开放问题：V2 立项时按真实 provider spike 冻结最大批次与并发，不影响本范围封套。
