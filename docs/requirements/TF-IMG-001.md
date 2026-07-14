# TF-IMG-001 产品/品牌广告图片创作工作台

## 1. 元数据

- ID：TF-IMG-001
- 标题：产品/品牌广告图片创作工作台
- 状态：defined
- 版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：图片/广告创作工作区
- 直接依赖：TF-STY-001、TF-WF-010、TF-MED-009、TF-QLT-001、TF-SEC-001
- 责任域：图片广告产品/媒体 AI
- 个人 DRI：待指派

## 2. 背景与问题

广告图片创作不是一次文生图：需要产品/品牌 Brief、可验证素材锚定、多个创意方向、真实候选、人工选择、清晰文案、安全区、多尺寸版式和可交付 manifest。把文字直接生进图会降低可读性，也难以跨尺寸复用。

V0 用真实 provider 验证从产品资料到广告图包的端到端价值，V1 Core 再扩展品牌资产、批量方向和可复用工作流。

## 3. 目标与非目标

- 目标：从 Product/Brand Brief 生成至少三个真实候选、人工选择并输出多尺寸广告图交付包。
- 目标：产品形态、Logo/色彩、文案、安全区、声明和授权均可测、可追踪、可修订。
- 目标：使用 TF-QLT-001 固定商品/品牌集与人工 rubric 判定产品身份、约束、可读性、版式和安全。
- 非目标：不建设完整专业平面设计软件、广告投放系统或自动营销效果归因。
- 非目标：AI 生成候选不得自动作虚假功效、价格或比较性声明。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V0 | 结构化 Brief、产品素材锚定、三候选、人工选择、1:1/4:5/16:9 版式和交付包 | ArtifactVersion/ResourceDraft/Revision 与固定素材 refs | 真实 provider 三候选到三尺寸包 E2E |
| V1 Core | Brand Kit、多创意方向/批量变体、复杂安全区、局部重做、模板化工作流 | V0 brief/layout 可读；Brand Kit/方向字段可选 | 品牌约束回归、批量/并发、权限和恢复 E2E |

## 4. 用户与权限

- V1 私有项目只有项目 owner 可提交 Brief、上传有权产品/品牌素材、生成候选、选择和导出；共享编辑/审阅/只读角色后置到 TF-TEAM-001。
- 品牌资产 owner 控制 Logo、字体、色卡和产品修订；跨 owner 使用必须先固定为 ResourceRevision，并携带有效 grant snapshot 与署名。
- 创作 Human Gate 由项目 owner 提交；平台受控审核员仅处理获分配的广告安全/合规决定，不能替 owner 选择创意候选。
- 运营/模型不能绕过 TF-SEC-001 的商标、真人、未成年人、广告声明与无权素材 Gate。

## 5. 用户场景与主流程

1. 用户填写产品、受众、渠道、卖点、必备/禁用元素、文案、声明、尺寸和预算。
2. 系统验证产品/品牌固定素材、权利、内容安全和 provider 能力，生成 2 至 4 个创意方向建议。
3. 用户选择方向后，底层服务在网络调用前于同一数据库事务持久化 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent，再由真实图片 provider 生成至少三个视觉候选；文案保留为独立版式层。
4. 用户比较产品形态、品牌、构图、文案和质量报告，选择候选或局部修复。
5. 版式引擎为 1:1、4:5、16:9 生成变体，用户调整焦点/文案/安全区并确认。
6. 系统冻结广告创意修订，导出图片、源 manifest、权利/署名和质量报告包。

## 6. 功能需求

- FR-1：ProductBrandBrief 至少包含产品 ref、品牌 ref、受众、渠道、objective、claims、required/prohibited elements、copy、locale、sizes、budget。
- FR-2：产品/Logo/包装参考固定版本、role、顺序和不可变授权快照，不按文件名绑定；同 owner 可直接使用 ArtifactRef，跨 owner 必须先提升为 ResourceRevision 并使用带 GrantSnapshot 的 ResourceRef。
- FR-3：创意方向结构化表达 concept、composition、palette、lighting、product placement、copy hierarchy、risk notes 和 reference roles。
- FR-4：V0 每个选中方向生成至少三个真实候选；每个保存独立 ArtifactVersion、ProviderOutputBinding、质量与安全状态。一个外部请求的多个候选共享 InvocationRecord/实际成本，只有真实多次请求才保存多条记录。
- FR-5：产品锚定按 provider 能力使用多参考/编辑；不支持 required 控制时按 blocked、degraded 或 ignored_with_warning 明示。
- FR-6：广告文案默认作为独立文本/版式层渲染，禁止把不可校对的生成图内文字直接标为最终交付。
- FR-7：版式至少支持 1:1、4:5、16:9，包含主体焦点、Logo 区、标题/副文案、CTA、法律声明和渠道安全区。
- FR-8：文字渲染检查溢出、遮挡、最小字号、对比度、语言换行和禁用词；最长合法文案不得越出画布。
- FR-9：人工选择/驳回记录理由；局部修复与变体生成新 ArtifactVersion，不覆盖原候选。
- FR-10：交付包含每尺寸最终图片、可复现 layout spec、copy、source refs、checksums、attribution、权利/安全和质量 manifest。
- FR-11：V1 Core 支持 Brand Kit ResourceRevision、批量方向、模板 WorkbenchTask 和候选 Human Gate。
- FR-12：TF-QLT-001 评测产品轮廓/关键细节、Logo、品牌色、文案 OCR/可读性、安全区、广告声明和人工审美 rubric。
- FR-13：所有底层 provider 请求遵循 TF-MED-009：网络副作用前原子持久化 NodeRunAttempt、ProviderInvocationAttempt 与 provider_dispatch OutboxEvent，使用稳定 provider idempotency key；ProviderInvocationAttempt 复用公共 AttemptStatus，unknown 只查询/回调/账单/人工对账而不盲目重提。
- FR-14：结果发布必须以 execution epoch/fencing token 条件更新，并在一个事务内写入候选 ArtifactVersion、每 Attempt 最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得形成部分候选或部分计费。
- FR-15：fallback 必须从原始固定 Brief/方向/素材输入重新获取 CapabilitySnapshot、重新编译产品控制、重新验证授权并重新估算成本；确认需要新外部请求后创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，原 attempt/record 不覆盖。

## 7. 交互与展示

- 首屏直接进入 Brief 与素材，不先要求搭画布；进度按 Brief、方向、候选、版式、交付呈现。
- 产品/品牌参考显示用途 swatch、修订、授权和 provider 应用状态；缺失 required 参考不可继续。
- 候选用稳定网格 A/B 比较，突出产品差异、品牌/安全问题、成本和选择理由。
- 版式画布显示渠道安全区、焦点、Logo/文案 bounding box，使用文本输入和尺寸控件而非自由提示词。
- 移动端可填 Brief、比较和确认；精细版式与多尺寸调整保留桌面端。

## 8. 数据、类型与公共接口

- `ProductBrandBrief` 为 ArtifactVersion 内容，引用固定 Product/Brand ResourceRef 与约束，不创建可变外链；跨 owner 品牌/产品内容不得以裸 ArtifactRef 写入 Brief。
- `CreativeDirectionSet` 含 direction_id、structured direction、reference bindings、risk/quality report refs 和 selection decision。
- `AdLayoutSpec` 含 canvas size、safe zones、layers、asset refs、text styles、focal point、render settings 和 locale。
- `AdCreativeRevision` 是专用 ResourceRevision，内容引用 selected visual、brief/direction/layout Artifact 和 lineage。
- `AdDeliveryManifest` 为 ArtifactVersion，含 final refs、sizes、copy、checksums、source/grant/attribution、quality/moderation refs。
- 底层生成沿用 TF-MED-009/主表 8.4 的 ProviderInvocationAttempt、OutboxEvent、ProviderInvocationRecord 与 ProviderOutputBinding；一个 ProviderInvocationAttempt 最多一条 ProviderInvocationRecord，一条 ProviderInvocationRecord 可通过一个或多个 ProviderOutputBinding 关联多个候选。工作台不绕过编译/安全直接调用 provider，也不假设候选与调用记录一一对应。

## 9. 状态机与业务规则

- Brief/方向/候选/版式是独立 Artifact 与工作台任务状态，不复用 RevisionStatus 或 ListingStatus。
- 草稿保存以 draft_version compare-and-swap；同一尺寸并发修改显示 layer 级三方 diff。
- 选中候选或修改 copy/layout 产生新内容 Artifact；确认才冻结 AdCreativeRevision。
- 产品/品牌修订变化时所有关联 layout/交付草稿标 stale，历史包不改写。
- 一个交付包只有在全部 required 尺寸、权利、安全、质量和 checksum 通过后标 completed。
- ProviderInvocationAttempt 直接复用关联 NodeRunAttempt 的公共 AttemptStatus；provider task submitted/queued/processing 等仅为事件或绑定明细，unknown 在对账前不是可重试终态。只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord。

## 10. 失败、降级与恢复

- 素材无权、声明高风险、预算不足或 required 产品锚定不受支持时运行前阻断。
- 候选部分失败时保留成功结果；已有输出可重新拉取，需要补齐至三个时仅在原 attempt 已收敛到公共终态后创建显式新请求。unknown 对账完成前不得补提，失败占位不能计数或进入选择。
- 自动多尺寸构图裁切产品/Logo/文案时标记失败并要求调整焦点/布局，不能静默输出。
- 刷新、断网和服务重启从 ProviderInvocationAttempt、dispatch/result OutboxEvent、Artifact/ResourceDraft/RunEvent 恢复；已完成候选不重复计费。
- provider fallback 重新固定能力快照、编译产品参考、验证授权并估算成本；原提交为 unknown 时必须先对账，只有确认新外部请求时才创建新的 NodeRunAttempt/ProviderInvocationAttempt/dispatch OutboxEvent。版式渲染失败可独立重试，不重生底图。
- execution epoch/fencing 条件失败时晚到结果只进入隔离审计，不得写候选 Artifact、InvocationRecord、OutputBinding、实际成本或 result_publish OutboxEvent。

## 11. 安全、隐私、内容与授权

- 验证产品/品牌/Logo/字体/模特素材的所有权或许可；公开网页图片不默认可商用。
- 同 owner ArtifactRef 可直接使用；跨 owner 产品、品牌、Logo、字体或控制内容必须经固定 ResourceRevision、GrantSnapshot 与当前 entitlement，签名 URL 不构成授权。
- 价格、功效、比较、环保、医疗/金融等声明按地域/渠道规则阻断或人工审核，保存证据与披露。
- 真人模特、未成年人、声音/肖像、冒充和敏感商品按 TF-SEC-001 处理；撤回后阻断新变体/导出。
- 导出 manifest 不含 secret/永久私有 URL；交付图按政策保留必要 AI 生成/编辑披露。

## 12. 观测与运营

- 事件：ad_brief_created、direction_selected、candidates_completed、ad_candidate_selected、layout_rendered、delivery_exported/blocked。
- 指标：Brief 完成率、三候选成功率、选择轮次、每最终图成本、版式失败率、stale 率和交付耗时。
- 质量看板引用 TF-QLT-001，并按产品类型/provider/model/layout/locale 分层产品身份、OCR、品牌和人工 rubric。
- 支持信息含 brief/direction/layout/revision、provider invocation、security decision、checksum 和 correlation_id。

## 13. 验收标准

- AC-1：Given 合法产品/品牌 Brief，When V0 单个真实 provider 请求返回三候选，Then 存在一条 InvocationRecord、三个 OutputBinding/ArtifactVersion、owner 人工选择及 1:1/4:5/16:9 三尺寸包。
- AC-2：Given 固定产品样本，When TF-QLT-001 回归，Then 产品关键细节、品牌色/Logo 和构图分项达批准阈值。
- AC-3：Given 最长批准中文/英文文案，When 渲染三尺寸，Then 无溢出/遮挡，安全区与对比度检查全部通过。
- AC-4：Given provider 不支持 required 产品多参考，When 编译，Then 阻断或显式降级确认，不静默生成泛化产品。
- AC-5：Given 产品修订更新，When 打开旧草稿，Then 显示 stale 和 diff，历史 AdCreativeRevision/交付包不变。
- AC-6：Given 无权 Logo 或高风险虚假声明，When 生成/导出，Then 服务端 Gate 阻断并保留安全审计。
- AC-7：Given 单个真实 provider 请求返回三候选，When 当前 epoch 发布，Then 一个 ProviderInvocationAttempt、最多一条 InvocationRecord、三个 OutputBinding/ArtifactVersion、一笔实际成本和一个同事务 result_publish OutboxEvent；事务故障或过期 epoch 时全部不提交。

## 14. 测试场景

- 正常：产品/品牌 Brief、方向、三候选、选择、局部修复、三尺寸、文案和交付包。
- 边界：透明产品、反光包装、长品牌名、多语言、极端横竖比、无 Logo、最大允许素材数。
- 失败：provider 部分失败、产品漂移、OCR 错误、版式溢出、Blob/字体缺失、预算耗尽。
- 权限：跨 owner 裸 ArtifactRef/Brand Kit、无权 Logo/字体/模特、撤权后导出、非 owner 选择、敏感商品声明。
- 并发/恢复：双端改版式、重复生成、取消晚到、刷新、断网、服务重启和 stale 合并。

## 15. 交付与回退

- 方向生成、图片 provider、各尺寸、Brand Kit 和批量能力独立功能开关；关闭生成仍可编辑已存版式。
- V0 brief/layout/delivery schema 向 V1 兼容；旧客户端对未知 Brand Kit 层只读。
- 发布证据包括真实 provider、三候选三尺寸、TF-QLT-001、文案视觉回归、权利/安全与恢复 E2E。
- 回退不删除 AdCreativeRevision/包；不能解释的新 layout layer 保留原文并阻止有损重存。

## 16. 已决策事项与开放问题

- 已决策：V0 广告闭环包含真实三候选、人工选择和多尺寸包；文案是可校对版式层。
- 已决策：广告工作台是领域体验，底层图片调用统一走 TF-MED-009。
- 已决策：跨 owner 素材必须提升为 ResourceRevision；候选通过 OutputBinding 关联真实调用，提交安全遵循 attempt/outbox/idempotency/unknown 对账合同。
- 开放问题：渠道研究后冻结首批安全区 preset、最小字号和声明规则地域范围。
