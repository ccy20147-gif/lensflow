# TF-MED-008 角色身份锚定与一致性审查

## 1. 元数据

- ID：TF-MED-008
- 标题：角色身份锚定与一致性审查
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：影视工作区/平台内核
- 直接依赖：TF-STY-003、TF-MED-001、TF-MED-003、TF-MED-006、TF-MED-009、TF-QLT-001
- 责任域：媒体 AI/质量
- 个人 DRI：待指派

## 2. 背景与问题

AIGC 连续镜头常发生脸型、发型、服装、体态、道具或年龄漂移。所谓“防变脸”通常来自固定角色修订、多用途参考、多参考顺序、局部编辑、首尾帧和人工审查，而非真正的骨骼绑定，也不能保证绝对一致。

平台需要把身份锚点、provider 实际应用和评分证据串成可审计 Gate，允许重试和人工选择，而不是隐藏失败。

## 3. 目标与非目标

- 目标：从固定 CharacterRevision 与选中视觉资产建立用途明确、顺序稳定的身份锚点集。
- 目标：在单镜、相邻镜头和 51 镜头项目层评估身份/服装/道具一致性并支持 Human Gate。
- 目标：全部阈值、固定样本、人工 rubric 和回归容差由 TF-QLT-001 版本化管理。
- 非目标：不宣称多参考、嵌入、评分、ControlNet 或首尾帧可“保证不变脸”。
- 非目标：不把身份向量当作可公开 OC 资源，也不取代素材权利与真人同意。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可选择身份锚点、用途、允许变体并提交创作 Human Gate；共享项目编辑/审阅角色后置到 TF-TEAM-001。
- 平台受控审核员仅可在获分配的安全/合规任务中查看匿名化必要证据并作对应 PolicyDecision，不能替项目 owner 作创作选择；任何用户均不得下载原始身份向量。
- provider 仅接收本次镜头所需的最小参考；平台管理员不能在无审计目的下浏览私有参考。
- 真人肖像、未成年人、声音联动和冒充使用必须由 TF-SEC-001 Gate 裁决。

## 5. 用户场景与主流程

1. 项目 owner 为 ShotSpec 选择固定 CharacterRevision，系统载入经选择的正面、侧面、表情、服装、体态和道具参考；跨 owner 资产只能通过已授权 ResourceRevision 进入。
2. 用户为每个参考指定 role/用途、优先级和适用镜头/时间范围，形成不可变 AnchorSet Artifact。
3. TF-MED-006 将 ordered references 编译到目标 provider，报告真实应用、转换、降级或忽略。
4. 图片/视频候选生成后，系统运行身份、服装、体态和道具一致性评测并关联证据区域。
5. 低于 Gate 的候选被阻断选中或进入 Human Gate；用户可调整参考、局部编辑或重试。
6. 选中输出、评分、人工决定和引用版本写入 ShotSpec 新修订，历史证据不变。

## 6. 功能需求

- FR-1：身份锚点必须固定 CharacterRevision 与每个资产引用；同 owner 可固定 ArtifactVersion，跨 owner 必须先提升为 ResourceRevision 并使用带 GrantSnapshot 的 ResourceRef，不允许裸 ArtifactRef 或按角色显示名查找 latest。
- FR-2：参考 role 至少支持 face_identity、profile、hair、body_shape、costume、expression、prop_relation、style_context。
- FR-3：保持 ordered_reference_refs；同一图可承担不同 role，但必须作为不同绑定记录保留用途。
- FR-4：`IdentityAnchorSet` 保存适用 shot/beat 范围、强度、变体许可、真人标记、consent evidence ref 和授权快照。
- FR-5：编译报告逐参考只使用 applied/transformed/degraded/ignored_with_warning/blocked，provider 不支持多参考时不得静默只取第一张。
- FR-6：一致性报告至少分 face、hair、body、costume、age presentation、signature props 和 overall，不用单一分数掩盖漂移。
- FR-7：评测必须保存模型/算法版本、阈值版本、输入裁剪/证据 refs、置信度和不确定状态。
- FR-8：低于 hard threshold 或评测不确定时按工作流策略 block 或进入 TF-WF-008 Human Gate，不能自动宣告通过。
- FR-9：允许用户记录剧情许可变体，如换装、受伤、年龄变化；变体必须有范围与来源，不降低其他身份项要求。
- FR-10：支持单镜候选、相邻镜头和 51 镜头汇总审查，定位首次漂移与传播范围。
- FR-11：每个真实外部请求都必须从原始固定输入获取 CapabilitySnapshot、编译身份参考、验证授权并估算成本；重提或 fallback 必须重新获取快照、重新编译、重新授权决策与重新估算，再于网络前同一事务创建 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent。原提交为 unknown 时只对账，不能盲目重试或直接 fallback。
- FR-12：TF-QLT-001 固定角色集包含多性别/年龄/肤色、多人、遮挡、侧脸和换装边界，并经偏差审查。
- FR-13：生成结果发布必须以 execution epoch/fencing token 条件验证当前 Attempt，并在一个事务内写入候选 ArtifactVersion、最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得形成部分候选或部分计费。
- FR-14：ProviderInvocationAttempt 的 attempt_status 必须复用关联 NodeRunAttempt 的公共 AttemptStatus；provider task 内部 submitted/queued/processing 等阶段只作为绑定事件或明细，不建立任何 provider 专用公共状态枚举。

## 7. 交互与展示

- 锚点编辑器以角色为中心展示参考缩略图和用途 swatch/标签，支持拖动顺序并显示 provider 上限。
- 报告先显示通过/阻断/需人工与分项，再展开证据区域、阈值、算法版本和 provider 实际应用。
- 相邻镜头可用 A/B、闪烁和局部叠加比较；不得用美化滤镜掩盖差异。
- 低置信度明确显示“无法判定”，不能用绿色通过；人工决定需选择理由。
- 移动端支持查看报告与 Human Gate 决定，精细锚点排序/局部蒙版保留桌面端。

## 8. 数据、类型与公共接口

- `IdentityAnchorSet` 为 ArtifactVersion 内容：character_revision_ref、ordered_bindings、scope、allowed_variants、consent_ref、grant_snapshot_refs。
- `IdentityAnchorBinding` 含 reference_ref、role、order、strength、shot_or_beat_scope、region_ref 可选值；跨 owner reference_ref 只能是已授权 ResourceRef。
- `IdentityConsistencyReport` 为 ArtifactVersion：source/output refs、metric_revision、rubric_ref、component_scores、uncertainties、evidence_refs、decision_suggestion。
- ShotSpec 通过 ControlLayer(style/look reference、first/last/keyframe 等) 或 generation_policy 引用 AnchorSet，不内嵌可变引用。
- 真实生成调用严格沿用主表 8.4 的 ProviderInvocationAttempt、OutboxEvent、ProviderInvocationRecord 与 ProviderOutputBinding；ProviderInvocationRecord 保存 ordered_reference_refs、compilation_report_ref 和 output_bindings，必须与 AnchorSet/报告可对账。一个 ProviderInvocationAttempt 最多一条 ProviderInvocationRecord，一条 ProviderInvocationRecord 可通过一个或多个 ProviderOutputBinding 关联多个候选。
- IdentityAnchorSet 或其他控制 Artifact 需要跨 owner 复用时，必须先提升为专用 ResourceRevision；不得直接授权裸 ArtifactVersion 或敏感身份向量。
- 人工决定使用 HumanTaskStatus 与审计记录，不新增 `identity_passed` 公共运行状态。

## 9. 状态机与业务规则

- 自动审查执行使用公共 RunStatus/NodeRunStatus/AttemptStatus；需要人工裁决时创建使用 HumanTaskStatus 的 Human Gate，uncertain 只能打开 pending 人工任务，不能自定义审查状态族或直接 accepted。
- 资源/输出修订或 metric/rubric revision 改变时旧报告标 stale，新选择必须重新评测。
- 相同 output ref/anchor set/metric revision 评测幂等；重复回调不能生成相互矛盾的当前决定。
- 自动阈值决定只能阻断或建议，最终可选策略由版本化工作流/Human Gate 确定。
- 授权或真人同意撤回后阻断新评测披露、新生成和新导出；历史报告按隔离保留策略处理。
- ProviderInvocationAttempt 只使用公共 AttemptStatus；unknown 在对账前不是可重试终态，只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord；晚到或过期 execution epoch 的结果不得进入身份审查或候选选择。

## 10. 失败、降级与恢复

- 无可用正脸、严重遮挡或检测失败时返回 uncertain 和证据缺口，不给虚假高分。
- provider 无多参考能力时按 ControlLayer 策略 blocked/degraded/ignored_with_warning；degraded 必须说明选取/合成方法。
- 指标服务失败不丢生成候选；候选保持“未审查”且不能通过强制 Gate，恢复后可单独重评。
- worker 重启从 output/anchor/metric fingerprint 恢复；已完成分项不重复计费或覆盖。
- 取消或超时保留部分报告，Human Gate 未接受前不得写入 selected_output_refs。
- provider 提交不确定时只通过查询、回调、账单或人工方式对账；对账未收敛前不得为相同固定输入盲目新建请求，fallback 也不得绕过重新快照、编译、授权与估算。

## 11. 安全、隐私、内容与授权

- 真人身份锚点必须有可验证同意、用途、期限和撤回通道；名人或公众人物不因公开可见而自动获授权。
- 同 owner ArtifactRef 可直接进入 AnchorSet；跨 owner 参考或控制内容必须通过固定 ResourceRevision、GrantSnapshot 与当前 entitlement，签名 URL 或历史可见性不能替代。
- 未成年人默认阻断身份克隆/冒充用途，允许场景需更严格监护同意和人工审核。
- 身份嵌入、脸部裁剪和证据区域视为敏感派生数据，加密、最小访问并按 TF-NFR-002 限期删除。
- 报告不得推断种族、健康、性取向等非任务敏感属性；公平性评测只使用合规标注聚合。
- 撤回同意后阻断新运行/派生/导出并清理可删敏感数据，同时保留隔离审计证据。

## 12. 观测与运营

- 事件：anchor_set_created、identity_evaluation_started/completed/uncertain、identity_gate_opened/accepted/rejected、consent_revoked。
- 指标：各分项通过率、不确定率、人工推翻率、重试次数、每角色成本、provider 参考降级率和偏差分层差异。
- 质量看板固定 TF-QLT-001 数据集/rubric/metric revision；按 provider/model/version 比较而不混合平均。
- 审计链从 CharacterRevision、AnchorSet、编译报告、InvocationRecord、输出、评测到人工决定。

## 13. 验收标准

- AC-1：Given 固定角色六类参考，When 生成候选，Then 参考顺序/用途在 AnchorSet、编译报告和 InvocationRecord 三处一致。
- AC-2：Given provider 只支持单参考，When 多参考 required，Then outcome 为 blocked；允许 degraded 时明确合成/选取和语义损失。
- AC-3：Given 51 镜头固定角色集，When 审查，Then 报告定位每项漂移、首发镜头和证据，达到 TF-QLT-001 阈值。
- AC-4：Given 指标无法检测被遮挡脸，When 评测，Then 状态为 uncertain 并进入人工 Gate，不自动通过。
- AC-5：Given 真人同意撤回，When 新生成、评测或导出，Then 全部服务端阻断且敏感派生数据进入清理任务。
- AC-6：Given 角色换装已声明范围，When 检查范围内/外镜头，Then 服装变化分别允许/报告，其他身份分项仍评测。
- AC-7：Given 跨 owner 身份参考，When 以裸 ArtifactRef 建立 AnchorSet，Then 服务端拒绝；提升为 ResourceRevision、授权并携带有效 ResourceRef 后才允许编译。
- AC-8：Given 一个真实请求返回三个身份候选，When 当前 epoch 发布结果，Then 只产生一个 ProviderInvocationAttempt、最多一条 InvocationRecord、三个 OutputBinding/ArtifactVersion、一笔实际成本和一个同事务 result_publish OutboxEvent；过期 epoch 发布被完整拒绝。

## 14. 测试场景

- 正常：单人、多角色、正侧脸、表情、换装、道具、多参考编译、评分、人工选择和 51 镜头汇总。
- 边界：遮挡、背影、极端光线、年龄剧情变化、双胞胎、相同服装多人、参考顺序上限。
- 失败：检测器超时、证据裁剪损坏、metric 版本退役、provider 丢参考、报告/调用不一致。
- 权限：跨 owner 裸 ArtifactRef/无 grant、非 owner 创作 Gate、真人无同意、未成年人、冒充、撤回后重跑、身份向量下载。
- 并发/恢复：同候选重复评测、Human Gate 双提交、取消晚到、worker 重启、阈值变更竞态。

## 15. 交付与回退

- 自动评测、硬 Gate 和各分项可独立开关；关闭评测时输出保持“未审查”，不得默认通过。
- metric/rubric/anchor schema 全部版本化；旧报告只读可解释，新阈值不追溯重写历史决定。
- 发布证据包括 TF-QLT-001 多样性固定集、真实 provider、多参考降级、51 镜头、权限和撤回 E2E。
- 出现偏差或误杀回归时可回退指标 revision，并把高风险流量转人工 Gate。

## 16. 已决策事项与开放问题

- 已决策：身份一致性来自资产锚定、强类型引用、多参考、关键帧与审查，不是骨骼绑定保证。
- 已决策：评分可阻断或辅助人工，但不作绝对“不变脸”承诺。
- 已决策：跨 owner 身份/控制内容必须提升为 ResourceRevision；真实 provider 重提遵守 attempt/outbox/unknown 对账合同，多个候选可共享一次调用记录。
- 开放问题：TF-QLT-001 基线评审后冻结各分项 hard/review 阈值与敏感派生数据保留天数。
