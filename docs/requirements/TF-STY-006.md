# 小说/框架到影视剧本改编

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-STY-006 |
| 标题 | 小说/框架到影视剧本改编 |
| 状态 | defined |
| 版本 | V1 Core |
| 优先级 | P1 |
| 全局位置 | 小说/影视工作区 |
| 直接依赖 | TF-STY-004、TF-STY-005、TF-WF-005 |
| 责任域 | 小说/影视产品 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

小说正文、叙事框架和影视剧本的结构不同。改编必须将叙述转为可拍摄场景、动作、对白和表演意图，同时保持来源 span、角色身份与世界硬约束，才能继续进入镜头规划。

## 3. 目标与非目标

- 将固定 FrameworkRevision 或小说内容 revision 转成可编辑、强类型、可追溯的 Screenplay。
- 保存场景/节拍到来源 span、WorldRevision 和 CharacterRevision 的关系。
- 非目标：不直接生成 ShotPlan、分镜图片、视频、配音或摄影参数。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可查看来源、发起改编、编辑 ScreenplayDraft 和确认 revision；项目成员、共享编辑、审阅和只读角色统一后置 TF-TEAM-001。
- 来源小说、框架、世界和角色均需当前查看及相应用途权限；同 owner ArtifactRef 可直接使用，跨 owner 内容必须先提升为 ResourceRevision 并以带授权证据的固定 ResourceRef 使用。
- owner 可保存仅自己可见的私人批注；批注不产生来源资源的 reference、derivative 或 commercial 权利。

## 5. 用户场景与主流程

1. owner 选择小说 revision 或 FrameworkRevision，并绑定固定 World/Character refs。
2. 系统生成改编计划，列出目标时长、场次、取舍和合并建议。
3. owner 确认计划后，受编译器管理的运行生成结构化场景、动作节拍、对白、旁白和来源映射 ArtifactVersion。
4. 工作台将该不可变输出装载到 ScreenplayDraft，显示剧本与原文对照，由 owner 修订并运行约束检查。
5. owner 提交后由 workflow-owned ResourceCommit 冻结 ScreenplayRevision，供 TF-MED-002 镜头规划消费；生成 Agent 本身不创建 Revision。

## 6. 功能需求

- FR-1：输入必须固定同 owner 的 Framework/小说 ArtifactRef，或固定 ResourceRef 指向的 Framework/小说、WorldRevision 和 CharacterRevision，不读取 latest；跨 owner 输入禁止使用裸 ArtifactRef。
- FR-2：输出至少包含场次、内外景、地点、时间、参与角色、场景目标、动作节拍、对白/旁白、表演意图、转场和预计时长。
- FR-3：每个 scene/beat/dialogue 必须有稳定 ID，并保存一个或多个 source_spans；原创桥段须显式标记 adaptation_generated。
- FR-4：角色只通过固定 Character ResourceRef 或来源稳定 local ID 关联，禁止按显示名合并；跨 owner Character ResourceRef 必须携带 `grant_snapshot_id` 并通过当前 entitlement 校验。
- FR-5：改编计划必须在生成正文前展示删除、合并、新增桥段和目标时长影响，并由用户确认。
- FR-6：工作台支持原文/剧本对照、场次重排、对白编辑、来源跳转、角色/世界约束检查和 diff。
- FR-7：世界硬规则、角色身份和用户锁定内容的违规必须阻断确认；建议性节奏问题可带警告确认。
- FR-8：确认后形成不可变 ScreenplayRevision；上游变化仅标 stale 并提供重算范围。
- FR-9：输出不得包含图片二进制或 provider 调用；镜头媒体生产由 MED 需求承担。

## 7. 交互与展示

- 工作台以场次列表、剧本编辑器、来源对照和约束侧栏组成。
- 删除/合并来源段落在改编计划中明确标记，用户可锁定必须保留片段。
- 角色、地点和世界规则以引用芯片展示 revision 与授权状态。
- 主画布节点只显示场次数、预计时长、待确认/冲突和进入工作台操作。

## 8. 数据、类型与公共接口

- `Screenplay` 是生成输出使用的注册 Artifact schema；`ScreenplayRevision` 是 `Resource(resource_type=screenplay)` 的专用 ResourceRevision，不建立平行版本源。
- `Scene` 扩展 `scene_id`、heading、location_ref、time_of_day、character_refs[]、goal、estimated_duration_ms、source_spans[]。
- `ScriptBeat` 扩展 beat_id、kind、action/dialogue、speaker_ref、performance_intent、source_spans[]、adaptation_generated。
- 生成运行只输出同 owner_scope 的 Screenplay ArtifactVersion；工作台写入 ResourceDraft，最终由 workflow-owned ResourceCommit 冻结 ScreenplayRevision。
- 下游通过固定 ResourceRef 获取 ScreenplayRevision。跨 owner 使用必须携带授权证据，禁止把 ArtifactRef 附加 grant 后作为跨 owner 引用。

## 9. 状态机与业务规则

- 改编运行状态、计划 HumanTask、Screenplay RevisionStatus 和 stale 标记分离。
- 计划确认以 plan hash 幂等；来源 revision 或计划变更后旧确认失效。
- 场次重排改变 sequence_index，不改变稳定 scene_id。
- 上游更新不自动重写剧本；重新改编产生新 Artifact/草稿并要求三方合并。

## 10. 失败、降级与恢复

- 来源 span 无法解析、角色引用歧义或硬约束违规时阻断确认并定位字段。
- 生成中断保留已完成场次 checkpoint，但恢复后必须使用同一固定输入和计划。
- 工作台并发保存冲突显示 base/current/local diff。
- 来源撤权后阻断新改编和新发布；历史合法产物按授权规则保持审计读取。

## 11. 安全、隐私、内容与授权

- 私有小说和剧本默认项目内可见，原文对照不向无权用户展示。
- 来源许可、角色/世界授权和署名 manifest 随 Screenplay lineage 保存。
- 工作台只编辑草稿和提交 revision；任何重新改编都显式请求受编译器管理的运行，不直接调用 provider 或改写 Run/NodeRun。
- 改编、发布和商业导出分别基于当前 EntitlementDecision，不以历史 GrantSnapshot 代替。

## 12. 观测与运营

- 记录来源 refs、计划/确认、场次生成、source span 覆盖、约束报告、编辑量和 revision。
- 指标包括改编完成率、来源覆盖率、原创桥段比例、硬约束违规、用户改动率和目标时长偏差。
- 支持审计可从任一 scene/beat 跳回来源版本与具体 span。

## 13. 验收标准

- AC-1：Given 固定小说 revision，When 完成改编，Then 每个场次/节拍有稳定 ID，来源内容有可解析 span，原创桥段被明确标记。
- AC-2：Given 两个同名角色，When 改编对白，Then speaker 按 CharacterRef 区分且约束不串线。
- AC-3：Given 用户锁定片段，When 计划拟删除该段，Then 确认前阻断并指出 source span。
- AC-4：Given 上游 revision 更新，When 打开既有剧本，Then 仅显示 stale，不改变已确认 ScreenplayRevision。
- AC-5：Given 用户请求直接生成分镜图，When 执行本节点，Then 输出仍限 typed screenplay，不发生媒体 provider 调用。
- AC-6：Given 改编输入包含跨 owner 裸 ArtifactRef，When 编译或确认计划，Then 请求被阻断；替换为带有效 `grant_snapshot_id` 且当前 entitlement 允许的固定 ResourceRef 后才可运行。

## 14. 测试场景

- 正常：框架改编、小说改编、计划确认、对照编辑、场次重排和下游引用。
- 边界：无对白场景、原创桥段、多来源 span、同名角色、超长来源章节。
- 失败：span 损坏、角色歧义、硬约束冲突、生成中断和保存 CAS。
- 权限：非 owner 项目访问、无权原文、跨 owner 裸 ArtifactRef、授权 OC ResourceRef、撤权后重跑、查看与派生权分离。
- 并发/恢复：重复计划确认、checkpoint 恢复、owner 双标签合并和事件重放。

## 15. 交付与回退

- 先对短篇与单集改编灰度，长篇生产调度不随本项进入 V1。
- 关闭生成能力时保留 ScreenplayRevision 查看、下游消费，以及基于固定 Revision 创建新 ScreenplayDraft 的手工编辑路径。
- 交付证据包括 source span contract tests、角色约束、stale、权限和改编 E2E。

## 16. 已决策事项与开放问题

- 已决策：本项输出结构化剧本而非镜头或媒体；来源 span、人物和世界约束必须保留。
- 已决策：新桥段允许存在，但必须显式标记并经用户确认。
- 已决策：V1 项目 owner-only；生成运行只产出 ArtifactVersion，ScreenplayRevision 由工作台提交后的 workflow-owned ResourceCommit 创建。
- 已决策：V1 Screenplay 完全私有，不提供社区 Listing、搜索、引用、派生，也不得包装为只读 CreativeWork 或通过 Workflow Template 内嵌泄漏；固定版本的私人创作入口遵守 TF-PLT-003。
- 开放问题：无阻塞 V1 Core 的开放问题。
