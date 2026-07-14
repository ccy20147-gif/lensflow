# 角色 IP/OC 资源与工作台

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-STY-003 |
| 标题 | 角色 IP/OC 资源与工作台 |
| 状态 | defined |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | 小说/影视工作区/资源库 |
| 直接依赖 | TF-STY-002、TF-WF-005 |
| 责任域 | 小说产品/资源平台 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

角色既是世界的一部分，也是需要跨作品、跨文本和媒体生产复用的独立 IP/OC。只把角色写在世界设定文本里会丢失稳定身份、版本、外观/声音锚点、授权和来源谱系。

## 3. 目标与非目标

- 建立独立 Character Resource、工作台、不可变修订和跨作品引用。
- 支持从 WorldRevision 内嵌角色提升，并永久保存 lineage。
- 非目标：本项不承诺参考图保证不变脸，不负责社区 listing，也不生成完整媒体资产流程。

## 4. 用户与权限

- World/Character 所有者可提升、编辑、冻结和管理角色。
- 项目创作者按授权引用固定 CharacterRevision；查看/收藏不自动获得复用或派生权。
- 声音、真人肖像和未成年人相关字段受额外同意与用途限制。

## 5. 用户场景与主流程

1. 用户在 WorldRevision 的初始 OC 中选择一个角色并执行“提升为独立 OC”。
2. 系统创建 Character ResourceDraft，复制结构内容并记录来源 world revision、局部 ID 和提升事件。
3. 用户在角色工作台补充身份、外观、声音、关系、服装/年龄变体和参考素材。
4. 确认后生成 CharacterRevision。
5. 新作品、剧本或资产流程用固定 ResourceRef 引用；角色后续分叉不回写来源 WorldRevision。

## 6. 功能需求

- FR-1：Character Resource 必须有稳定 resource_id，显示名、别名和同名角色均不作为关联键。
- FR-2：角色内容至少包含身份核心、背景、性格、目标、关系、外观锚点、声音描述、表演边界、变体、参考素材和授权元数据。
- FR-3：提升操作必须保存 `source_world_revision_id`、`world_local_character_id`、source content hash 和 promotion event。
- FR-4：相同 world revision/local ID 的重复提升必须提示复用或显式分叉，禁止无提示复制。
- FR-5：工作台支持字段级编辑、参考用途排序、关系浏览、变体管理、diff 和 revision 确认。
- FR-6：每个参考素材必须声明用途，如 identity、face、costume、voice、pose 或 style，不按上传顺序猜测。
- FR-7：后续 CharacterRevision 不修改 WorldRevision；世界更新时仅提示可选同步并要求三方 diff。
- FR-8：跨作品引用固定 revision 与 GrantSnapshot，重跑/派生/发布时重新判断当前 EntitlementDecision。
- FR-9：删除或撤回不得破坏既有合法运行的 lineage 和隔离审计证据。

## 7. 交互与展示

- 角色工作台分为身份、故事、外观、声音、关系、变体、素材、版本与授权。
- 顶部持续显示资源身份、当前 revision、来源世界和是否存在 stale 提示。
- 提升确认页显示将复制内容、不会同步的边界和 lineage。
- 参考素材以缩略图加用途标签、顺序和授权状态展示，不使用模糊“参考图”集合。

## 8. 数据、类型与公共接口

- `Character` 是 `Resource(resource_type=character)`；CharacterRevision 使用专用 schema 的 ResourceRevision。
- 内容扩展 `identity_core`、`biography`、`traits[]`、`goals[]`、`relationship_refs[]`、`appearance_bible`、`voice_bible`、`performance_constraints[]`、`variants[]`、`reference_bindings[]`。
- `PromotionLineage` 含 source World ResourceRef、world_local_character_id、source span/hash、promoted_at 和 actor。
- 工作流/作品通过 ResourceRef 固定 CharacterRevision，跨 owner 时 grant_snapshot_id 强制存在。

## 9. 状态机与业务规则

- 草稿/修订使用 ResourceDraft 与 RevisionStatus；社区状态由 COM 需求管理。
- 提升以 source key 幂等；显式分叉创建新 resource_id 并引用原 promotion event。
- 变体属于 CharacterRevision 内容，不是无版本附件；变体引用稳定 variant_id。
- 当前授权变化不修改历史 revision，只影响新引用、重跑、派生、发布和导出。

## 10. 失败、降级与恢复

- 来源 WorldRevision 或局部 ID 不存在时阻断提升，不创建空角色。
- 某参考素材失效时保留结构内容，标记 reference unavailable 并阻断依赖该 required 用途的新运行。
- 并发编辑返回三方 diff；不得覆盖新 draft_version。
- 提升事务中索引/缩略图失败由 outbox 恢复，Character 身份与 lineage 必须原子创建。

## 11. 安全、隐私、内容与授权

- 真人肖像/声音必须保存同意凭证、用途、期限和撤回规则；无凭证不得用于新生成。
- 角色公开展示、引用、派生和商业使用分别授权，不能从“公开”推断。
- 未成年人和敏感身份内容按最小展示与平台策略处理。
- 导出/复制不得携带无权素材或 CredentialBinding。

## 12. 观测与运营

- 记录创建、提升、分叉、草稿保存、revision、引用、授权决策、stale 和素材失效事件。
- 指标包括提升成功率、重复提升提示、跨作品引用、版本采用率、参考失效率和撤权阻断。
- 审计可从 CharacterRevision 双向追踪来源 WorldRevision 和消费作品/运行。

## 13. 验收标准

- AC-1：Given WorldRevision 内嵌 OC，When 提升，Then 新 CharacterDraft 含完整来源 revision/local ID/promotion event 且来源不被修改。
- AC-2：Given 同名两个角色，When 建立关系和引用，Then 系统按 resource/revision ID 区分，不出现名称串线。
- AC-3：Given CharacterRevision A 被作品使用，When 创建 B，Then 历史运行仍固定 A，新运行不自动升级。
- AC-4：Given 公开展示但未授权 derivative，When 用户尝试派生，Then 被阻断；仅查看仍按 display 权限执行。
- AC-5：Given 真人声音授权撤回，When 新生成，Then 当前 EntitlementDecision 阻断且旧审计证据保留。

## 14. 测试场景

- 正常：空白创建、世界提升、角色编辑、变体、修订、跨作品引用。
- 边界：同名角色、重复提升、无参考素材、多世界关系、最大参考数量。
- 失败：来源缺失、素材失效、schema 错误、索引失败和 stale 合并冲突。
- 权限：只展示、只引用、派生、商业、真人同意撤回和跨 owner 复制。
- 并发/恢复：双编辑 CAS、重复提升、事务重试、outbox 恢复和版本固定。

## 15. 交付与回退

- 先开放项目内角色与世界提升，再由社区需求开放发布/引用。
- 数据回退保持 CharacterRevision 与 PromotionLineage 可读；禁用提升不影响既有角色。
- 交付证据包括 lineage contract tests、同名资源测试、授权矩阵、并发和跨作品 E2E。

## 16. 已决策事项与开放问题

- 已决策：角色 OC 是一等 Resource；世界内初始 OC 可提升但永不覆盖来源 revision。
- 已决策：参考绑定降低身份漂移但不构成“不变脸”保证。
- 开放问题：无阻塞 V1 Core 的开放问题。
