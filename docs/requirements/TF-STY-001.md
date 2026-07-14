# Idea、Creative Brief 与约束输入

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-STY-001 |
| 标题 | Idea、Creative Brief 与约束输入 |
| 状态 | defined |
| 版本 | V0 |
| 优先级 | P0 |
| 全局位置 | 默认入口/小说工作区 |
| 直接依赖 | TF-WF-005、TF-WF-010 |
| 责任域 | 小说产品/AI |
| 个人 DRI | 待指派 |

## 2. 背景与问题

用户常从一句灵感、零散资料或明确创作任务开始，不应先理解 DAG。系统需要把自然语言与可选约束转成可版本化、可供后续世界观、框架、剧本和广告流程消费的强类型输入。

## 3. 目标与非目标

- 提供快速 idea 输入和渐进式 Creative Brief 表单。
- 生成合法、可编辑、有来源与权利声明的 ArtifactVersion。
- 非目标：不在本项生成世界观、框架、正文、镜头或媒体，也不强迫用户打开画布。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可查看、创建、修改和确认 Brief；项目成员、共享编辑、审阅和只读角色统一后置 TF-TEAM-001。
- 同一 `owner_scope` 的上传素材可用固定 ArtifactRef；跨 owner 内容必须先提升为 ResourceRevision，再以带 `grant_snapshot_id` 的固定 ResourceRef 引用，并在每次新行为前校验当前 EntitlementDecision。

## 5. 用户场景与主流程

1. 用户在默认入口输入一句 idea，选择小说/剧本等目标。
2. 系统解析出建议字段，并把不确定内容标为“待确认”而非事实。
3. 用户展开高级约束，补充受众、篇幅、类型、语气、禁用项和参考资料。
4. 用户确认后生成 CreativeBrief ArtifactVersion。
5. 模板或主画布在同一 `owner_scope` 内以 ArtifactRef 将其传给世界观、框架或其他业务节点；需要跨 owner 使用时，先完成 ResourceRevision 提升与授权引用。

## 6. 功能需求

- FR-1：必须支持只填 idea 与目标类型即可生成最小合法 Brief。
- FR-2：结构化字段至少包含标题占位、目标媒介、类型、主题、受众、语气、语言、篇幅目标、硬约束、偏好、禁用项和 source refs；source refs 必须区分同 owner ArtifactRef 与固定 ResourceRef。
- FR-3：解析建议必须区分用户原文、系统推断和用户已确认值，并保留 source span。
- FR-4：用户可随时切换快速/高级视图，切换不得丢失字段或改变已确认值。
- FR-5：确认时校验 schema、互斥约束、长度边界、资源权限和素材权利声明。
- FR-6：每次确认生成新的不可变 ArtifactVersion；后续编辑不得覆盖已启动运行的输入。
- FR-7：下游可按字段读取，禁止依赖显示标签或拼接整段 prompt 猜测语义。
- FR-8：必须提供从模板、已有 Brief 或空白创建，以及复制时去除无权引用的能力；禁止把跨 owner 裸 ArtifactRef 或仅附带 grant 字段的 ArtifactRef 写入 Brief。

## 7. 交互与展示

- 首屏优先显示 idea 输入、目标类型与“继续创作”，高级约束按需展开。
- 推断字段显示来源和置信状态，用户确认后有明确视觉区别。
- 冲突约束在对应字段就地解释，并提供保留哪项的操作。
- 确认页摘要展示将传给下游的内容、引用、权利声明和版本。

## 8. 数据、类型与公共接口

- `CreativeBrief` 使用注册 schema，保存为 ArtifactVersion；同一 `owner_scope` 内引用使用 ArtifactRef。
- 内容字段扩展为 `idea_text`、`target_medium`、`genre_tags[]`、`themes[]`、`audience`、`tone`、`language`、`length_target`、`constraints[]`、`negative_constraints[]`、`source_refs[]`、`field_provenance[]`。
- `field_provenance` 记录字段路径、来源类型、source span 和 confirmed_by_user。
- 若提升为持久 Brief Resource，必须遵循 ResourceDraft/Revision，不建立平行版本源。
- 跨 owner 来源只能保存固定 ResourceRef；其 `grant_snapshot_id` 是历史授权证据，当前行为仍必须重新计算 EntitlementDecision。

## 9. 状态机与业务规则

- 编辑态与 ArtifactVersion 分离；只有“确认”产生可供运行固定的版本。
- 同一确认请求以 draft_version 和内容 hash 幂等。
- 硬约束优先于偏好；冲突未解决时禁止确认。
- 用户未确认的推断可随解析重算，已确认字段除非用户编辑不得静默变化。

## 10. 失败、降级与恢复

- AI 解析不可用时保留手工表单，用户仍可创建最小合法 Brief。
- 自动保存失败显示本地未同步状态；重连后基于 draft_version 合并。
- 引用失效时不删除输入文本，标出失效 ref 并要求移除或替换。
- 确认写入成功但事件投递失败时由 outbox 重试，不重复创建版本。

## 11. 安全、隐私、内容与授权

- 上传内容默认项目私有，不用于社区展示或模型训练授权推断。
- 外部素材记录来源、权利声明和允许用途；声明不替代平台 Gate。
- 输入预览和错误日志必须清理个人信息、secret 与未授权正文。

## 12. 观测与运营

- 记录入口来源、草稿保存、解析、字段确认、冲突、ArtifactVersion 创建和下游绑定事件。
- 指标包括从 idea 到确认完成率、用时、字段冲突率、手工降级成功率和引用失效率。
- 运营只查看聚合字段使用率，不默认读取用户创意正文。

## 13. 验收标准

- AC-1：Given 仅一句 idea，When 用户确认最小输入，Then 生成 schema 合法且可由下游节点读取的 ArtifactRef。
- AC-2：Given 系统推断了类型与语气，When 用户未确认，Then 输出明确标记推断来源且不得伪装成用户事实。
- AC-3：Given 两项硬约束冲突，When 确认，Then 阻断并定位冲突字段，未生成新 ArtifactVersion。
- AC-4：Given AI 解析服务失败，When 用户改用手工表单，Then 可完成同一 schema 的合法 Brief。
- AC-5：Given 已有运行固定版本 A，When 用户确认版本 B，Then 既有运行仍读取 A，新运行可显式选择 B。
- AC-6：Given Brief 引用跨 owner 裸 ArtifactRef，When owner 确认，Then 系统拒绝该引用；只有先提升为 ResourceRevision 且提供有效授权证据的固定 ResourceRef 才可通过。

## 14. 测试场景

- 正常：一句 idea、完整高级表单、模板复制、素材引用和下游绑定。
- 边界：最长允许 idea、多语言、零参考、极短/极长篇幅、重复标签。
- 失败：解析超时、schema 校验失败、引用失效、自动保存冲突和事件重试。
- 权限：非 owner 查看/确认被拒绝、跨 owner 裸 ArtifactRef 被拒绝、授权 ResourceRef、撤权素材复制和私有正文日志检查。
- 并发/恢复：双标签编辑、离线重连、重复确认、服务重启后版本唯一。

## 15. 交付与回退

- V0 以默认入口和内置模板开放；AI 解析置于独立功能开关，手工路径始终可用。
- schema 需向前兼容；回退 UI 后保留所有 ArtifactVersion 与来源字段。
- 交付证据包括表单 E2E、解析降级、冲突校验、版本固定和权限测试。

## 16. 已决策事项与开放问题

- 已决策：非技术用户不必先搭画布，Brief 是后续能力共享的 typed 输入。
- 已决策：推断值和用户确认值必须分开，不把自然语言 prompt 当公共合同。
- 已决策：V1 私有项目 owner-only；团队编辑、评论与审阅能力由 TF-TEAM-001 提供。
- 开放问题：无阻塞 V0 的开放问题。
