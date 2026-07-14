# TF-SEC-001 权限、内容安全与素材权利 Gate

## 1. 元数据

- ID：TF-SEC-001
- 标题：权限、内容安全与素材权利 Gate
- 状态：defined
- 版本：Foundation -> V0 -> V1 Core
- 优先级：P0
- 全局位置：平台内核/导出
- 直接依赖：Foundation：TF-GOV-001、TF-ARC-001、TF-OPS-005；V0 增加 TF-PLT-001、TF-WF-005、TF-OPS-003
- 责任域：Trust & Safety/安全平台
- 个人 DRI：待指派

## 2. 背景与问题

开放创作平台同时处理私有项目、跨资源引用、真人肖像与声音、未成年人、冒充、受版权/商标保护素材和可能违法内容。只在上传时做一次扫描无法覆盖后续编辑、provider 披露、运行、发布和商业导出。

安全裁决必须在服务端、可版本化、可审计、可撤回并与资源/授权真相相连；前端、LLM 或 provider 返回不能直接决定核心许可状态。

## 3. 目标与非目标

- 目标：统一对象级授权、内容政策、素材权利、同意/撤回、披露和导出 Gate，并在关键动作重新裁决。
- 目标：Foundation 冻结策略合同，V0 覆盖 bootstrap owner 与真实媒体，V1 Core 覆盖多个相互隔离的 owner 账户和高风险人工审核；V1 私有项目仍为单 owner。
- 目标：以 TF-QLT-001 安全固定集和对抗测试评估误放、误杀、群体偏差与解释完整性。
- 非目标：本需求不取代法律意见、社区举报处置 TF-COM-006 或第三方代码许可 TF-GOV-002。
- 非目标：不以“用户已勾选”“内容公开”或“AI 生成”推定拥有引用、派生或商业权。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| Foundation | Policy/Action/Decision、对象授权、错误/审计、权利/同意证据合同与 fail-closed 规则 | 稳定 subject/action/resource revision 标识 | 策略 contract、越权与故障演练 |
| V0 | bootstrap owner 隔离、上传/生成/导出 Gate、素材权利、真人/未成年人/冒充阻断 | 所有 Blob/Artifact/Resource 带 owner/tenant，V1 无破坏迁移 | 真实图片/广告的授权、安全、撤回 E2E |
| V1 Core | 多个独立 owner 账户、跨 owner Resource grant、人工审核、声音/视频、provider 最小披露与持续撤权；不含共享项目角色 | V0 evidence/decision 可读，新主体/动作兼容 | 全媒体权限矩阵、同意撤回和绕过测试 |

## 4. 用户与权限

- subject 包括 bootstrap owner、账户 owner、服务主体、worker、管理员和受控审核员；每次动作绑定 tenant/owner。V1 当前项目创作动作只授权项目 owner，共享项目角色后置到 TF-TEAM-001。
- 资源 owner 只能授予其可授予范围；grant 不能越过来源许可、同意、法律或平台政策。
- 审核员遵循最小权限、任务分配、双人/升级规则和全量访问审计，不拥有资源商业权。
- 管理员可紧急阻断模型/provider/策略路径，但不能篡改历史 GrantSnapshot、PolicyDecision 或运行证据。

## 5. 用户场景与主流程

1. 用户上传素材并声明来源、权利、主体、允许用途、地域/期限和是否含真人/声音/未成年人。
2. 系统验证对象权限、文件、恶意内容、政策分类和证据充分性，生成版本化 PolicyDecision。
3. 编译/运行前再次评估 subject、action、固定 resource revision、当前 grant、consent、policy 和 provider 披露；跨 owner 裸 ArtifactRef 在此直接拒绝。
4. 高风险请求 block 或创建人工审核任务；通过后仍只允许决定中列明的 action/scope。
5. 发布和商业导出重新计算 EntitlementDecision/PolicyDecision，并生成 disclosure/attribution manifest。
6. 同意/授权撤回或法律处置立即阻断新行为，标记受影响草稿并启动清理；隔离历史审计仍保留。

## 6. 功能需求

- FR-1：每个授权请求包含 subject、tenant、action、固定 target、project、purpose、provider/recipient 和 request context；ArtifactRef target 仅允许同 owner_scope，跨 owner target 必须是 ResourceRevision。
- FR-2：受控 action 至少含 upload/read/edit/reference/derive/generate/provider_disclose/publish/commercial_export/delete/admin_review。
- FR-3：策略决策为 allow、deny、review_required，包含 policy revision、reason codes、evidence refs、obligations、expires_at 和 correlation_id。
- FR-4：所有数据库查询和 Blob 签名访问执行 tenant/owner/object-level 检查，不能仅依赖前端隐藏或项目 ID。
- FR-5：素材权利证据记录来源、权利人、许可范围(reference/derivative/commercial/display)、期限、地域、条款 hash 与文件证据。
- FR-6：真人肖像/声音同意记录主体/代理验证、允许用途、模型/provider、期限、撤回通道和披露义务。
- FR-7：未成年人默认禁止克隆、冒充、性化和高风险商业用途；合法例外要求监护同意与人工审核。
- FR-8：冒充/欺诈风险结合主体、内容、声音/肖像、文案、受众与披露判断；合成内容水印/标签义务进入 decision。
- FR-9：编译、运行、发布和商业导出均重新计算当前 EntitlementDecision；GrantSnapshot 只保存历史动作证据。
- FR-10：撤回 consent/grant 后在 60 秒内使新授权判定失效，阻断新运行/派生/发布/导出并发出受影响事件。
- FR-11：高风险 policy 服务不可用时 fail closed；低风险只读历史可按版本化应急策略降级且完整告警。
- FR-12：人工审核任务保存输入最小视图、决定、理由、审核员、时间、策略版本、复核/申诉和访问审计。
- FR-13：provider 披露清单列出发送的数据类型/数量/地域/保留政策；超出最小必要或不合规 provider 时阻断。
- FR-14：TF-QLT-001 固定安全集覆盖越权、真人、声音、未成年人、冒充、版权/商标、提示注入和多语言规避。
- FR-15：不得向裸 ArtifactVersion 发放跨 owner grant；跨 owner 媒体、控制稿、关键帧、提示或知识内容必须先提升为 ResourceRevision，再以含 GrantSnapshot 的 ResourceRef 使用并重算当前 entitlement。
- FR-16：provider 披露授权必须在 ProviderInvocationAttempt/dispatch outbox 创建前评估，并由 dispatcher 在网络调用前按授权 epoch 再检查；撤权竞态不得通过已排队 outbox 披露素材。

## 7. 交互与展示

- 上传表单以来源、权利范围、真人/声音/未成年人和用途结构化输入，避免一个泛化“我有权”复选框。
- 阻断消息使用安全 reason code 与可行动说明，不泄露检测规则、他人资源或内部敏感证据。
- 高风险审核显示等待状态、预计范围、可撤销操作和申诉入口；前端弹窗不是审核事实源。
- 资源详情展示当前授权/同意状态、到期、用途和撤回影响；历史运行证据与当前可用性分开。
- 导出前展示素材/主体清单、署名、AI/合成披露、缺失权利和阻断项，不能默认折叠强制义务。

## 8. 数据、类型与公共接口

- 沿用公共 `GrantSnapshot`、`EntitlementDecision`、ResourceRef/ArtifactRef 和状态族，不创建可变历史快照。ArtifactRef 只在同一 owner_scope 内直接授权；跨 owner 授权对象只能是固定 ResourceRevision，ResourceRef 的 grant 可覆盖其修订固定内容。
- `PolicyDecision` 含 decision_id、subject/action/target、policy_revision_id、decision、reason_codes、obligations、evidence_refs、evaluated_at/expires_at。
- `RightsEvidence` 含 evidence_id/revision、claimant、source、rights_scopes、territory/term、terms_hash、document_refs、verification_status。
- `ConsentEvidence` 含 evidence_id/revision、subject/guardian、verified_identity_ref、allowed_uses、provider/model scope、term、withdrawal_state、document refs。
- `DisclosureManifest` 含 synthetic/AI labels、provider disclosures、attributions、rights/consent decision refs 和 render requirements。
- 敏感证据正文加密并与普通 Resource 内容分离；Artifact/Run 只保存最小引用和决策摘要。

## 9. 状态机与业务规则

- PolicyDecision 是某一请求时点的不可变结果，不等同 GrantStatus/ModerationStatus；新动作必须新评估。
- Rights/Consent evidence 修订不可原地改写；撤回生成新状态/修订与事件，旧证据隔离保留。
- 人工任务使用 HumanTaskStatus；accepted 只允许对应 decision scope，不能升级为永久 grant。
- 同一 subject/action/target/context fingerprint 在有效期内可缓存；撤回/策略更新必须主动失效。
- deny/review_required 不能被前端参数、LLM 输出、provider 成功或管理员数据库手改绕过。

## 10. 失败、降级与恢复

- policy、身份、grant、consent、证据或审计依赖异常时，高风险生成/披露/发布/导出 fail closed。
- 内容分类器不可用时按风险分层：高风险 block/review，低风险可排队等待；不得直接 allow。
- 重复审核/撤回请求按幂等键处理；撤回与运行竞态由授权 epoch/fencing 在 dispatch outbox 提交前及真正 provider 网络调用前双检查。检查失败时取消未发送 attempt，不能重放披露。
- 服务重启后从 evidence、decision、task 和 outbox 恢复通知/清理，不依赖前端状态。
- 错误对外只给安全码/correlation_id；内部保留规则、证据、trace 并进行敏感清理。

## 11. 安全、隐私、内容与授权

- 系统遵循最小权限、默认拒绝、服务身份隔离、密钥托管、审计不可抵赖和敏感数据最少保留。
- 真人脸/声纹、证件、监护证明、同意录像与未成年人数据加密，访问需任务原因并记录。
- 防止 IDOR、批量枚举、签名 URL 重放、SSRF、提示注入、策略参数篡改、回调伪造和跨租户缓存。
- 安全模型不得推断不必要敏感属性；偏差按 TF-QLT-001 分层评估并提供人工复核/申诉。
- 法律下架/法院命令可限制历史内容访问，但审计证据隔离、访问受控且不可由普通删除清除。

## 12. 观测与运营

- 事件：policy_evaluated/denied/review_required、rights_verified/expired、consent_verified/revoked、export_blocked、security_override_used。
- 指标：授权 P50/P95、deny/review 率、误放/误杀、人工 SLA、撤回生效时延、越权尝试、跨租户拒绝和审计缺失数。
- 同步对象授权 P95 目标 <= 150ms（不含外部人工/分类）；撤回缓存失效和新行为阻断 <= 60 秒。
- 高风险 allow 无证据、跨租户成功、审计缺失、强制 Gate 静默降级四类指标目标均为零并告警。

## 13. 验收标准

- AC-1：Given 用户 A 猜测用户 B 的 resource/blob/run ID，When 读写或签名访问，Then 全部拒绝且不泄露对象是否存在。
- AC-2：Given 无权素材或缺 commercial scope，When 运行/发布/商业导出，Then 相应动作在 provider 披露前阻断。
- AC-3：Given 真人声音/肖像同意撤回，When 60 秒后新生成、派生或导出，Then 全部拒绝并产生清理/影响事件。
- AC-4：Given 未成年人克隆/冒充高风险样本，When 多语言/编码规避请求，Then TF-QLT-001 安全集达到批准拦截阈值且进入人工审计。
- AC-5：Given policy 服务宕机，When 高风险媒体请求，Then fail closed；恢复后幂等重评且无重复 provider 调用。
- AC-6：Given 合法历史运行后 grant 撤回，When 查看历史与尝试重跑，Then 历史按策略可读/隔离，重跑被当前 entitlement 阻断。
- AC-7：Given 用户 A 获得用户 B 的裸 ArtifactRef/签名 URL，When 尝试引用或向 provider 披露，Then 服务端拒绝；B 将内容提升为 ResourceRevision 并授予有效 ResourceRef 后，A 才能按 scope 使用。

## 14. 测试场景

- 正常：owner 私有操作、同 owner ArtifactRef、跨 owner ResourceRef、合规真人同意、人工审核、披露、发布/导出和撤回。
- 边界：grant 到期瞬间、主体代理、跨地域条款、同一素材多权利人、同 owner grant 可空、历史法律限制。
- 失败：policy/分类/审计/缓存故障、证据损坏、回调伪造、撤回外部 provider 失败、清理重试。
- 权限：IDOR、跨租户缓存、签名 URL 重放、管理员越权、审核员批量浏览、服务身份混淆。
- 并发/恢复：撤回与 dispatch/网络提交竞态、重复审核、策略热更新、服务重启、outbox 重放和晚到 worker。

## 15. 交付与回退

- 每类风险、provider、动作和人工队列独立开关；未知/关闭策略对高风险默认 block。
- Policy/Rights/Consent schema 和规则均版本化；回退应用仍能读取新证据，未知 obligation 必须阻断有损动作。
- 发布证据包括权限矩阵、TF-QLT-001 对抗集、真人/声音/未成年人/冒充、撤回、故障和跨租户演练。
- 紧急回退可关闭新上传/生成/导出但不能删除历史 decision/evidence/audit；恢复需重新评估排队动作。

## 16. 已决策事项与开放问题

- 已决策：公开展示不等于允许复用；历史 GrantSnapshot 不赋予新行为，关键动作重算当前权限。
- 已决策：真人肖像/声音、未成年人、冒充和同意撤回是 Foundation/V0/V1 的硬安全场景。
- 已决策：V1 私有项目保持 owner-only；同 owner 可直接使用 ArtifactRef，跨 owner 必须提升为 ResourceRevision，且 provider 网络披露前执行授权 epoch 双检查。
- 开放问题：法务与 Trust & Safety 在 Foundation 冻结首发地域政策、人工 SLA、证据保留期和合成披露格式。
