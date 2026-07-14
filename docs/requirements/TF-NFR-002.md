# TF-NFR-002 数据导出、删除、保留与灾难恢复

## 1. 元数据

- ID：TF-NFR-002
- 标题：数据导出、删除、保留与灾难恢复
- 状态：defined
- 版本：V1 Core
- 优先级：P1
- 全局位置：设置/平台内核
- 直接依赖：TF-PLT-001、TF-WF-005、TF-OPS-003、TF-OPS-005、TF-SEC-001
- 责任域：数据平台/SRE/法务
- 个人 DRI：待指派

## 2. 背景与问题

平台保存不可变资源修订、媒体 Blob、运行、授权、同意和派生谱系。用户需要可携带导出和删除权，但直接物理删除被他人合法引用的 Revision、授权快照或安全审计会破坏历史真相与争议证据。

同时，媒体资产体量大、provider 可重生性有限，必须按数据类别定义保留、软/硬删除、法律保留、RPO/RTO 和可验证恢复，而不是只声明“有备份”。

## 3. 目标与非目标

- 目标：提供用户/项目级机器可读导出、可验证 manifest、删除预览、软删/硬删和 Blob 垃圾回收。
- 目标：删除个人可删数据的同时，最小化保留合法引用、授权、财务/安全和法律证据，并限制其用途/访问。
- 目标：以明确 RPO/RTO、季度恢复演练和 TF-QLT-001 数据完整性固定集证明灾难恢复。
- 非目标：删除不等于立即抹除法定/争议/安全保留数据，也不允许利用删除破坏他人合法历史成果。
- 非目标：导出不包含明文 secret、CredentialBinding、provider 密钥或无权第三方内容。

## 4. 用户与权限

- 账户/项目 owner 可请求本人数据、私有项目导出与账户删除；V1 私有项目不提供任何共享项目角色，非 owner 无权导出或删除当前项目，共享能力后置到 TF-TEAM-001。
- 资源 owner 可删除/撤回自有资源，但跨 owner 合法引用和历史证据按主表第 8.5 节与本需求的最小封存规则处理；V1 Community 再由 TF-COM-004 接入具体授权流程。
- 法务/安全可设置有范围、期限、原因和审批的 legal hold；访问与解除全量审计。
- SRE 可执行备份/恢复演练但不能浏览明文用户内容，恢复访问使用临时最小权限。

## 5. 用户场景与主流程

1. 用户请求项目导出，系统重新鉴权并计算资源、Revision、Artifact、工作流、运行、媒体、授权和署名闭包。
2. 异步任务生成 JSON/JSONL、媒体文件与 checksums manifest；完成后提供 24 小时短期下载链接。
3. 用户请求删除时先看到影响预览：将软删、可硬删、因引用/授权/法律需封存及将失效的新行为。
4. 确认后账户/资源立即不可用于新运行/发布/导出，进入 30 天恢复期并发出授权/stale 事件。
5. 到期后清除可删元数据/Blob/敏感派生数据；最小封存证据加密隔离、去标识并仅限合法用途。
6. SRE 按同一 consistent restore point 从备份恢复隔离环境，先在 4 小时内恢复 metadata 服务，再在 8 小时内恢复该点完整受保护媒体闭包，并校验 Blob checksum、Revision/lineage 和租户边界。

## 6. 功能需求

- FR-1：导出覆盖账户/项目元数据、Workflow/Resource/Revision、Artifact metadata/content、Run/NodeRun/event、授权/署名、用户可访问媒体及 schema manifest。
- FR-2：每项导出记录稳定 ID、schema/version、source revision、checksum、相对路径和是否因权限/法律被省略的 reason code。
- FR-3：导出按请求时固定 snapshot，运行中变化进入下次导出；禁止一半读取 old、一半读取 latest。
- FR-4：导出包加密传输、短期签名 URL 默认 24 小时、下载可重试且记录审计，不含永久 Blob URL 或 secret。
- FR-5：删除预览对每项分类为 delete、anonymize、retain_sealed、blocked_by_hold，并说明范围/理由/期限。
- FR-6：用户确认删除后 60 秒内阻断新登录/运行/引用/发布/商业导出；异步物理清理不延迟逻辑阻断。
- FR-7：默认软删恢复期 30 天；到期后硬删无引用 Blob，失败任务幂等重试并可对账。
- FR-8：存在他人合法历史引用时保留最小不可变 Revision/content/GrantSnapshot/lineage/attribution 证据，禁止新使用并限制普通 owner 访问。
- FR-9：Grant/consent 撤回、账号删除和内容下架分别建模；删除不能伪造历史从未授权，撤回也不自动删除所有法定证据。
- FR-10：默认保留策略：一般应用日志 30 天，安全/授权/删除审计 365 天；legal hold/地域要求可覆盖并记录 policy revision。
- FR-11：备份覆盖关系数据库、对象元数据、所有被 committed ArtifactVersion 引用的 Blob、schema/配置与加密密钥恢复材料，按租户边界和引用闭包验证；不得以“可重新生成”排除已提交 Blob。
- FR-12：生产 metadata 及所有被 committed ArtifactVersion 引用的 Blob 均满足 RPO <=5 分钟；metadata RTO <=4 小时，同一 consistent restore point 的完整受保护媒体闭包 RTO <=8 小时。
- FR-13：每日自动备份/恢复探针、每季度完整隔离恢复演练；恢复必须使用同一 restore_point_id，校验 checksum、引用闭包、metadata/media 时间一致性和权限隔离。
- FR-14：TF-QLT-001 数据完整性固定集覆盖 51 镜头项目、跨 owner 引用、撤权、删除、导出和恢复前后等价。
- FR-15：ArtifactVersion metadata 对外提交前必须通过 durability barrier：所有被引用 Blob 已完成校验、耐久写入/复制，并取得能证明满足 5 分钟 RPO 的 BlobDurabilityReceipt；任一 receipt 缺失或失败时不得提交 ArtifactVersion。

## 7. 交互与展示

- 设置页分开“导出”“删除项目/资源”“删除账户”“保留/法律限制”，避免危险操作相邻误触。
- 删除前用明确数量和资源类型展示影响，要求重新认证与文本确认；不使用含糊“清理”命令。
- 进度显示扫描、打包、校验、可下载/失败；下载过期可重新签名，不能重新生成不同 snapshot 冒充同一包。
- retained sealed 项说明为何不能立即物理删除、访问限制和申诉/联系通道，不泄露其他用户身份。
- 恢复期内显示剩余天数和恢复命令；到期后不承诺可恢复已硬删内容。

## 8. 数据、类型与公共接口

- `DataExportRequest` 含 requester、scope、snapshot_at、include_media、format、encryption_recipient 可选值和 idempotency_key。
- `DataExportManifest` 含 request/snapshot、entries、schema catalog、checksums、omissions、attribution、generated_at 和 package checksum。
- `DeletionPlan` 含 subject/scope、base snapshot、classified entries、dependency/reference graph、holds、effective_at、purge_after。
- `RetentionPolicyRevision` 含 data_class、duration、trigger、legal_basis、region、disposition 和 exceptions。
- `SealedEvidenceRecord` 含 original refs、retention reason/expiry、access policy、anonymization state、checksum 和 audit refs。
- `BlobDurabilityReceipt` 含 blob_id、checksum、durability_class、replica/journal checkpoint、protected_at、restore_point eligibility 和验证结果；ArtifactVersion 提交记录引用对应 receipt。
- `RecoveryReport` 为 ArtifactVersion，含 restore_point_id、metadata/media backup refs、RPO/RTO measured、protected media closure、checks、failures 和 approvals。

## 9. 状态机与业务规则

- 导出任务：requested -> snapshotting -> packaging -> verifying -> ready | failed | expired | cancelled。
- 删除任务：previewed -> confirmed -> access_blocked -> grace_period -> purging -> completed；legal_hold 可暂停 purging 但不恢复新使用。
- 业务 Resource/Revision/Grant 使用各自公共状态；删除任务状态不得写入 RevisionStatus/GrantStatus。
- 同一 idempotency_key 的导出/删除确认只执行一次；snapshot/plan 变化必须重新预览确认。
- 删除与新运行竞态通过 owner/auth epoch 和编译/提交前双检查阻断；晚到 worker 不能创建新可用产物。
- ArtifactVersion 的 committed 仅表示 metadata 事务已引用全部有效 BlobDurabilityReceipt；Blob 仍在暂存、校验、复制或保护 checkpoint 未确认时只能保留为未提交 staging 数据。

## 10. 失败、降级与恢复

- 导出中 Blob 丢失或 checksum 错时包不得 ready；manifest 列问题并重试修复，不能静默省略用户可访问内容。
- durability barrier 超时或 receipt 校验失败时，ArtifactVersion metadata 不得提交；恢复任务只能重试 Blob 保护步骤，不能先暴露元数据再承诺事后补备份。
- 大包分卷并支持断点下载；部分分卷失败不改变固定 snapshot/manifest。
- 清理失败进入隔离重试队列并告警，逻辑访问仍保持阻断；重复清理对已删对象幂等。
- 恢复失败不得切换生产；按 runbook 保持现有只读/降级服务并记录未达 RTO 事故。
- 加密密钥或备份损坏时执行批准的灾难流程，不能用未验证旧备份覆盖较新生产数据。

## 11. 安全、隐私、内容与授权

- 导出前重新验证 requester 与每个条目访问权，防 IDOR、路径穿越、压缩炸弹、跨租户 manifest 和签名 URL 重放。
- 导出包不含密钥、CredentialBinding、内部风控规则、他人私有内容或敏感证据正文；只含允许的用户可携带数据。
- sealed evidence 加密、去标识、用途限制、访问审批和全量审计；到期自动复核/删除。
- 人脸/声纹/证件/同意样本按更短专用政策清理；同意撤回立即阻断，不等待普通 30 天恢复期。
- 备份与恢复环境隔离、最小权限、密钥轮换和销毁证明；测试恢复数据不得进入开发共享环境。

## 12. 观测与运营

- 事件：export_requested/ready/downloaded/expired/failed、deletion_previewed/confirmed/blocked/purged、hold_applied/released、restore_drill_completed。
- 指标：导出完成时延/失败率/包校验、删除阻断时延、清理 backlog、sealed 数量/到期、durability barrier 时延/失败、未保护 committed Blob 数、备份成功率、同 restore point 实测 RPO/RTO。
- <=10GB 项目导出 P95 在 60 分钟内 ready；超出时显示分卷进度和容量级 SLA，不伪造完成。
- RPO/RTO 超标、跨租户恢复、checksum 差异、过期 hold 和清理逾期均为告警/事故并纳入发布 Gate。

## 13. 验收标准

- AC-1：Given 含 51 镜头、媒体、运行和授权的项目，When 导出，Then manifest 闭包完整、checksums 全通过且无 secret/永久 URL。
- AC-2：Given <=10GB 固定项目，When 请求导出，Then P95 60 分钟内 ready，链接 24 小时后失效且可重新鉴权签发。
- AC-3：Given 资源被其他 owner 合法历史引用，When owner 删除，Then 新使用 60 秒内阻断，历史最小证据可审计且不会泄露 owner 私有数据。
- AC-4：Given 无 hold 的账户删除，When 30 天到期，Then 可删元数据/Blob 清除，重复任务幂等，sealed 项均有 reason/expiry。
- AC-5：Given 灾难恢复演练，When 从同一 restore_point_id 恢复 metadata 与完整受保护媒体闭包，Then 两者实测 RPO 均 <=5m，metadata RTO <=4h、完整媒体 RTO <=8h，且引用/checksum/租户隔离通过。
- AC-6：Given TF-QLT-001 完整性固定集，When 导出-删除-恢复，Then 不可变 Revision、lineage、GrantSnapshot 和当前 entitlement 语义无错配。
- AC-7：Given Blob 仅上传成功但复制/保护 checkpoint 未完成，When 尝试提交引用它的 ArtifactVersion，Then durability barrier 拒绝提交；receipt 有效后提交成功，随后灾备清单能在 5 分钟 RPO 内定位该 Blob。

## 14. 测试场景

- 正常：账户/项目导出、分卷下载、资源/账户软删恢复、到期硬删、sealed evidence 和恢复演练。
- 边界：空项目、10GB 阈值、51 镜头多媒体、跨 owner 多级引用、grant 到期、legal hold 到期。
- 失败：Blob/checksum/receipt 缺失、durability barrier 超时、打包 worker 崩溃、下载中断、清理部分失败、备份/密钥损坏、restore point 不一致、RPO/RTO 超标。
- 权限：跨租户导出/下载、非 owner 导出/删除、管理员越权看 sealed、路径穿越、签名 URL 重放。
- 并发/恢复：删除与运行/授权竞态、重复确认、hold 与 purge 竞态、服务重启、outbox 重放。

## 15. 交付与回退

- 导出、软删、硬删、Blob GC、sealed evidence 和自动恢复分别有功能开关；关闭清理不恢复已阻断访问。
- schema/retention policy/backup format 版本化；应用回退仍能读取新 deletion/export 状态，未知类默认保留并阻断清理。
- 发布证据包括完整导出包、删除/引用保护、durability barrier 故障注入、全部 committed Blob 的 5 分钟 RPO 证明、TF-QLT-001、跨租户、每日探针和同 restore point 季度恢复报告。
- 回退/事故期间可暂停 purge 和新导出，但不能撤销已生效的权限/同意阻断或篡改历史证据。

## 16. 已决策事项与开放问题

- 已决策：删除不得破坏合法历史引用与授权证据；保留必须最小化、隔离、限用途和可到期。
- 已决策：所有被 committed ArtifactVersion 引用的 Blob 与 metadata 均为 RPO <=5 分钟；metadata RTO <=4 小时，同一 restore point 的完整受保护媒体 RTO <=8 小时。提交前 durability barrier、季度恢复演练与 checksum/权限验证是交付条件，不以“有备份”代替。
- 开放问题：法务在 V1 Core 上线前按首发地域冻结各数据类最终保留期限和 legal hold 审批链。
