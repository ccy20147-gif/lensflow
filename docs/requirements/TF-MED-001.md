# TF-MED-001 角色/场景/道具视觉资产生产

## 1. 元数据

- ID：TF-MED-001
- 标题：角色/场景/道具视觉资产生产
- 状态：defined
- 版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：影视工作区/资源库
- 直接依赖：TF-WF-005、TF-WF-010、TF-MED-009、TF-QLT-001；V1 Core 增加 TF-WF-008
- 责任域：影视资产产品/媒体 AI
- 个人 DRI：待指派

## 2. 背景与问题

影视生成不能只依赖一次性提示词。角色、场景和道具需要先形成可选候选、明确用途的参考资产和不可变修订，否则镜头生成无法解释参考顺序、身份漂移或上游变更。

公开专业流程普遍包含多模型候选、人工选择、角色多视图、道具细节、场景气氛与衍生控制图。本需求将其收敛为资源生产流程，而不是把每个候选铺成主画布节点。

## 3. 目标与非目标

- 目标：从资产计划生成候选、完成比较选择、冻结资源修订，并向镜头提供用途明确的固定引用；ArtifactRef 只在同一 owner_scope 内直用，跨 owner 内容统一通过 ResourceRef。
- 目标：保留输入、模型、参考顺序、选择理由、成本、授权和 lineage。
- 目标：用 TF-QLT-001 固定样本与 rubric 评测身份、产品形态、场景一致性和控制遵循度。
- 非目标：V1 不提供完整角色建模、材质、骨骼动画或 DCC 资产生产。
- 非目标：不承诺参考图或评分可以保证角色永不变脸。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V0 | 角色/场景/道具计划、至少三候选、人工选择、冻结修订 | 使用公共 ArtifactVersion、ResourceDraft、ResourceRevision；V1 可原样读取 | 真实图片 provider 完成计划到选中修订 E2E |
| V1 Core | 衍生视图、批量候选、用途绑定、Human Gate、stale 传播 | V0 修订不改写；新增字段可选且 schema version 可迁读 | 多参考、重选、并发编辑和下游 stale E2E |

## 4. 用户与权限

- 项目 owner 可创建计划、上传有权素材、生成候选、选择和冻结修订。
- V1 私有项目不提供任何共享项目角色，所有当前项目的生成、重选和草稿变更仅由 owner 执行；共享能力后置到 TF-TEAM-001。
- 同 owner 的固定 ArtifactRef 可直接作为参考；跨 owner 媒体或控制内容必须先由来源方提升为 ResourceRevision，再以含 `revision_id`、`grant_snapshot_id` 的 ResourceRef 使用，并在新生成、导出时重算当前权限。
- 真人肖像、未成年人、品牌或受保护素材进入 TF-SEC-001 Gate；前端选择不能替代服务端裁决。

## 5. 用户场景与主流程

1. 用户从角色 OC、剧本实体或 Creative Brief 创建资产计划并选择资产类型与目标用途。
2. 系统验证来源修订、素材权利、预算和 provider 能力，通过 TF-MED-009 在网络调用前持久化 invocation attempt/dispatch outbox，再生成至少三张可比较候选。
3. 用户查看提示与参考差异、质量评分和成本，选择一个候选或要求局部编辑/新一轮变体。
4. 选中内容写入 `ResourceDraft`；用户确认后以 compare-and-swap 冻结 `ResourceRevision`。
5. 工作台把固定 `ResourceRef` 交给 ShotSpec；上游修订变化只将下游标记 stale，不改写历史结果。

## 6. 功能需求

- FR-1：支持 character、scene、prop 三类资产计划及目标用途 `identity`、`look`、`composition`、`texture`、`control`。
- FR-2：计划必须固定来源 OC/世界/剧本/Brief 修订、参考素材顺序、宽高比、风格约束和负面约束；跨 owner 参考只接受已提升且授权的 ResourceRevision，不接受裸 ArtifactRef。
- FR-3：一次候选批次默认生成 3 个、允许 1 至 8 个；每个候选保存独立 ArtifactVersion 和 ProviderOutputBinding。一个外部请求返回多个候选时共享同一 ProviderInvocationRecord，只有真实拆成多次外部请求时才有多条记录。
- FR-4：候选比较必须展示 provider、模型版本、seed、引用顺序、成本、质量分项及安全状态。
- FR-5：支持从选中候选生成正侧背视图、表情、服装、尺度参照、道具细节或场景光照变体；不伪装为 3D 模型。
- FR-6：选择、驳回和再生成必须记录操作者、理由、基准候选及时间；自动评分不得自行冻结修订。
- FR-7：保存草稿必须生成新内容 ArtifactVersion；确认时创建专用 schema 的 ResourceRevision。
- FR-8：引用用途必须随下游 ControlLayer 或 ordered reference 传递，禁止按文件名或显示名称绑定角色。
- FR-9：上游来源、授权或选中修订变化时，系统计算受影响下游并标记 stale，提供重算建议。
- FR-10：V1 Core 的强制选择点使用 TF-WF-008 Human Gate，可取消、超时和恢复。
- FR-11：质量判断必须调用 TF-QLT-001 固定测试集，保存 rubric 分项、阈值版本和人工复核结果。

## 7. 交互与展示

- 工作台按“计划、候选、比较、衍生、确认”渐进披露，不展示底层配方节点图。
- 候选网格保持稳定尺寸，支持放大、A/B、差异叠加和拒绝原因；不得以颜色作为唯一状态信号。
- 每张资产显示资源类型、修订号、来源、授权、当前/过期质量报告和 stale 徽标。
- 移动端支持查看、比较和确认；精细蒙版编辑保留桌面端。
- 真实生成前展示估算成本，完成后展示实际成本和 provider 降级警告。

## 8. 数据、类型与公共接口

- 输入：同 owner 可使用固定 `ArtifactRef`；任意 owner 可使用经授权的固定 `ResourceRef`，并携带目标用途、生成约束与候选策略。跨 owner 裸 ArtifactRef 在入口即拒绝。
- `AssetPlanArtifact` 扩展 ArtifactVersion 内容，包含 entity_ref、asset_kind、intended_uses、ordered_reference_refs、generation_constraints。
- `AssetCandidateSet` 包含 batch_id、candidate_artifact_refs、quality_report_refs、invocation_record_refs、output_binding_refs 和 selection_state；不得假设 candidate 与 InvocationRecord 一一对应。
- 角色资产可写入 Character Resource 的视觉资产字段；场景/道具使用各自专用 schema 的 ResourceRevision，均不另建版本系统。
- 输出：选中 `ResourceRef`、同 owner 候选/控制 `ArtifactRef`、选择记录和 lineage；内容跨 owner 前必须先提升为 ResourceRevision，所有运行固定 revision，禁止读取 latest。
- Provider 请求由 TF-MED-009 执行，必须关联 CapabilitySnapshotRef、ProviderCompilationReport、ProviderInvocationAttempt、ProviderInvocationRecord 与 ProviderOutputBinding，并遵守稳定 provider idempotency key、unknown 只对账不盲重提的提交合同。

## 9. 状态机与业务规则

- 计划状态：draft -> generating -> review_ready -> selected -> frozen；failed 可回到 draft/retry，cancelled 为终态。
- 资源修订只使用公共 RevisionStatus；工作台选择状态不得复用 `active` 或 `published`。
- 同一批次的重复回调以 invocation_attempt_id/provider_request_id 幂等，不能生成重复 OutputBinding、候选或重复计费。
- 冻结使用 `draft_version` compare-and-swap；冲突时保留双方内容并要求用户合并。
- 重新选择产生新草稿内容与新 Revision，不修改旧 Revision；历史 ShotSpec 仍引用旧版本。

## 10. 失败、降级与恢复

- 素材失权、内容 Gate 阻断、预算不足或 required 控制不受支持时停止调用并返回安全错误与 correlation_id。
- 部分候选失败时保留成功候选；已有 provider_output_id 的下载失败可重新拉取。需要补生成时必须在原调用已结算或 unknown 对账完成后创建显式新请求，不得把失败占位图计作候选或盲重放原提交。
- provider fallback 必须重新编译控制项、重算成本，并只在真实发起新外部请求前创建新的 ProviderInvocationAttempt/dispatch outbox；原提交为 unknown 时先对账，不能以 fallback 名义盲目重提。
- 页面刷新或服务重启后从持久批次、RunEvent 和 ArtifactVersion 恢复，不依赖浏览器内存。
- 取消只阻止后续提交；晚到结果按运行 fencing 规则隔离并保留审计，不自动加入当前候选集。

## 11. 安全、隐私、内容与授权

- 上传时记录素材来源、权利声明、允许用途和保留策略；未知权利不得用于生成或导出。
- 裸 ArtifactRef 只能在同一 owner_scope 内直接使用；跨 owner 内容必须通过固定 ResourceRevision、GrantSnapshot 和当前 EntitlementDecision，签名 URL 不能替代授权。
- 真人身份参考需要可验证同意与用途范围；撤回后阻断新生成、新派生和新导出，历史证据隔离保留。
- 涉及未成年人、冒充、色情或敏感品牌场景时按 TF-SEC-001 阻断或进入人工审核。
- 日志和提示预览不得暴露签名 URL、凭证、私有素材原文或其他项目的参考。

## 12. 观测与运营

- 事件：asset_plan_created、candidate_requested/completed/failed、candidate_selected、revision_frozen、asset_marked_stale。
- 指标：候选成功率、首候选时延、每选中资产成本、平均轮次、人工选择分布、身份/控制 rubric 通过率。
- 审计必须可从资源修订追到每个输入修订、provider 调用、授权判定、选择人和质量报告。
- 支持信息包括 correlation_id、batch_id、run_id、provider_request_id、capability revision 和安全错误码。

## 13. 验收标准

- AC-1：Given 合法角色修订和预算，When 单个真实 provider 请求返回三候选并选择其一，Then 产生一个 InvocationRecord、三个 OutputBinding、三个不可变 ArtifactVersion 和一个固定 ResourceRevision，lineage 完整。
- AC-2：Given TF-QLT-001 固定角色样本，When 执行回归，Then 身份与约束分项达到该基线当前批准阈值且无超容差退化。
- AC-3：Given 上游 OC 新修订，When 影响分析完成，Then 历史资产不变且所有依赖草稿在 5 秒内显示 stale。
- AC-4：Given 两端基于同一 draft_version 确认，When 第二端提交，Then 返回 409 类冲突且不覆盖首个修订。
- AC-5：Given 无真人肖像同意或授权已撤回，When 请求新生成/导出，Then 服务端阻断且生成审计事件。
- AC-6：Given provider 一半候选失败并重启服务，When 恢复任务，Then 成功候选保留、失败可补齐且无重复计费。

## 14. 测试场景

- 正常：三类资产分别完成计划、三候选、局部编辑、选择、冻结和 ShotSpec 引用。
- 边界：1/8 候选、超长负面约束、重复参考、极端宽高比、51 镜头共享同一角色修订。
- 失败：provider 超时、部分结果损坏、编译阻断、Blob 丢失、预算耗尽、质量报告过期。
- 权限：跨项目窥探、跨 owner 裸 ArtifactRef/缺 grant、非 owner 确认、撤权后重跑、未成年人素材。
- 并发/恢复：双端选择、重复回调、取消后晚到结果、worker lease 过期、刷新和服务重启。

## 15. 交付与回退

- 功能开关按资产类型、provider 和衍生能力分别控制；关闭衍生能力不影响读取已存修订。
- V0 数据必须可由 V1 schema reader 读取；迁移只增加可选字段或生成新 ArtifactVersion，禁止原地改写。
- 发布证据包括真实 provider E2E、TF-QLT-001 报告、权限/恢复测试、成本对账和 51 镜头引用演示。
- 回退到上一应用版本时保持 Revision/Artifact 可读，新字段未知时只读展示并阻止有损保存。

## 16. 已决策事项与开放问题

- 已决策：多候选与人工选择是正式流程；资产通过固定修订和用途绑定进入镜头。
- 已决策：同 owner 可直接消费 ArtifactRef；跨 owner 资产必须先提升为 ResourceRevision。候选按 OutputBinding 关联真实调用，不按候选伪造 InvocationRecord。
- 已决策：参考锚定降低漂移但不作“不变脸”保证；完整 DCC 不在 V1。
- 开放问题：Foundation provider spike 后冻结各资产类型默认候选数、尺寸和 TF-QLT-001 数值阈值，负责人为影视资产产品/媒体 AI，不阻断合同实现。
