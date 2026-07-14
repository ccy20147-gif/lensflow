# 内容审核、举报与社区治理

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-006 |
| 标题 | 内容审核、举报与社区治理 |
| 状态 | defined |
| 版本 | V1 Community |
| 优先级 | P0 |
| 全局位置 | 社区/运营后台 |
| 直接依赖 | TF-PLT-001、TF-SEC-001、TF-OPS-005 |
| 责任域 | Trust & Safety/社区运营 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

用户发布作品、世界、OC 和工作流会带来违法/有害内容、侵权素材、冒充、恶意包与社区纠纷。审核模型可辅助筛查，但不能成为无审计、不可申诉的最终责任主体。

## 3. 目标与非目标

- 建立发布前审核、发布后巡检、举报、紧急暂停、处置、申诉和审计闭环。
- 将 ListingDraft/ListingRevision、ListingStatus、ModerationStatus、资源 revision 和 offer/grant 状态分离，确保审核决定只覆盖不可变公开内容。
- 非目标：不保证自动模型识别全部违规，不建设 V2 市场反操纵/交易争议系统，也不公开敏感审核规则细节。

## 4. 用户与权限

- V1 Community 由资源 owner 提交审核、查看原因并在允许时新建 ListingDraft/Revision 或申诉；共享项目提交角色仅在 TF-TEAM-001 后生效。
- 登录用户可举报；高风险匿名举报可受限支持并防滥用。
- 审核员按队列与最小权限查看必要内容；高级处置、申诉和法律限制需分级角色。
- 所有运营动作必须关联 actor、理由、证据和政策版本。

## 5. 用户场景与主流程

1. owner 将 ListingDraft 冻结为不可变 ListingRevision 后提交审核，系统对 listing_revision_id/content hash 执行自动安全、权利和包扫描。
2. 风险结果进入相应人工队列；审核员批准、拒绝或要求修订。
3. 已 listed 内容被用户举报，举报固定当前 ListingRevision，系统确认接收并按严重度排队。
4. 紧急风险可先将 ModerationStatus 置 suspended，使公开读取即时 fail-closed。
5. 作者可在时限内申诉；另一授权审核员复核，结果和理由完整留痕。

## 6. 功能需求

- FR-1：所有社区 listing 在公开前必须有针对准确 listing_revision_id/content hash 的 moderation decision；审核不得直接针对可变 ListingDraft，也不得沿用到任何新 ListingRevision。
- FR-2：自动检查至少覆盖内容安全、真人/声音同意、未成年人、侵权/无权素材、secret、恶意工作流包和冒充信号。
- FR-3：模型结果只产生风险标签、建议和证据，不得伪造人工审核 actor；硬性机器 Gate 可阻断但必须可解释与申诉。
- FR-4：举报必须包含 listing_id、不可变 listing_revision_id、target resource revision、类别、说明和可选证据；服务端签发 report_id 并防重复/滥用。
- FR-5：处置支持批准、拒绝、要求修订、临时 suspended、withdrawn/法律隔离建议，并明确影响固定 ListingRevision、整个 listing、resource 或主体的范围；运营不得原地修改被审核内容。
- FR-6：申诉必须固定原 decision、理由和新证据，由非原处理者或更高权限复核。
- FR-7：rejected/suspended 内容不得通过搜索、缓存、直接 URL、reference、clone、install、execute 或 redistribute 绕过；suspended 必须隐藏内容并阻断所有新行为。
- FR-8：处置不删除 ArtifactVersion、Revision、GrantSnapshot 或审计；展示与访问按政策隔离。
- FR-9：政策版本、模型版本、证据 refs、人工理由、listing_revision_id/content hash 和所有状态转换必须可追踪。
- FR-10：治理行为必须遵循公共状态语义：unlisted 只退出发现且 canonical URL 可访问；withdrawn/deleted 返回 tombstone 并阻断规定的新动作；grant revoked 与 listing 展示独立，不能被审核系统隐式合并。

## 7. 交互与展示

- 创作者发布页同时显示 ListingRevision、ListingStatus 与 not_reviewed/pending/approved/rejected/suspended，不用“发布失败”掩盖原因类别。
- 举报入口提供明确类别、证据上传和处理编号，不展示举报人给被举报方。
- 运营后台按严重度、类型、等待时间和资源类型组织队列，内容预览默认脱敏。
- 申诉页展示可公开原因、适用政策、截止时间和可提交材料。

## 8. 数据、类型与公共接口

- `ModerationCase` 引用 listing_id、listing_revision_id/content_hash、target resource revision、policy_revision、risk_findings[]、evidence_refs[]、assignee 和不可变 decision。
- `Report` 引用 reporter、target listing_revision_id/resource revision、category、evidence refs、dedupe key 和 case_id。
- ModerationStatus 严格使用主表枚举；ListingStatus 和 RevisionStatus 由各自聚合管理。
- ListingDraft 不进入案件证据真相；要求修订时 owner 新建 draft 并冻结新的 ListingRevision，原 case/decision 永久保留。
- 隔离媒体使用受控 ArtifactRef/Blob policy，不改变原 content hash。

## 9. 状态机与业务规则

- 每个 ListingRevision 的 ModerationStatus 为 not_reviewed -> pending -> approved/rejected；approved 可因新证据进入 pending 或 suspended；suspended 经复核产生新的 approved/rejected decision，不覆写历史 decision。
- appeal 是独立 case，不能原地覆盖原 decision；最终展示由最新有效处置决定。
- 同一 target/category/evidence 的重复举报合并计数但保留举报记录。
- 状态转换使用 listing_revision_id + case_version CAS 与权限校验；重复处置命令幂等。
- unlisted 不改变 ModerationStatus 且直链继续按 display entitlement 访问；withdrawn/suspended/deleted 的展示规则由权威 listing/resource 状态强制，grant revoked 只影响当前 entitlement。

## 10. 失败、降级与恢复

- 审核依赖不可用时新 listing 保持 pending，绝不默认上架。
- 紧急 suspension 写入数据库后详情和新动作即时检查；索引/CDN 事件携带 listing_revision_id 并由 outbox 重放，旧事件不得恢复被隐藏内容。
- 证据文件损坏时案件不能批准，保留问题并要求重新获取。
- 审核员会话中断后草稿决定不生效，恢复时重新校验 case version。

## 11. 安全、隐私、内容与授权

- 举报人身份、同意凭证、法律文件和未成年人信息按最小访问、加密和保留策略处理。
- 审核员查看高敏内容需理由与审计；禁止下载到非受控环境。
- 防举报轰炸、审核越权、将平台审核员误绑定为项目成员、CSRF、缓存绕过和模型提示注入。
- 人工最终责任、申诉和法律升级路径必须在运营制度中明确。

## 12. 观测与运营

- 紧急 suspension 在权威写入后 60 秒内完成搜索/CDN 传播；举报 API p95 在 2 秒内返回 report_id。
- 指标包括 ListingRevision 审核 SLA、队列积压、批准/拒绝/暂停率、举报有效率、申诉改判率、状态传播延迟、旧 revision 泄漏和权限异常。
- 运营抽样检查模型/人工一致性与不同群体误伤，模型升级需固定回归集。

## 13. 验收标准

- AC-1：Given owner 的可变 ListingDraft，When 提交审核，Then 先冻结不可变 ListingRevision，状态为 review_pending/pending 且任何公开入口均不可访问。
- AC-2：Given 审核批准 ListingRevision A，When ListingDraft 或 target resource 变化并产生 B，Then 批准只对 A/content hash 生效，B 必须重新审核。
- AC-3：Given 已上架内容被紧急 suspended，When 通过详情、搜索、缓存、reference/clone/install/execute/redistribute 访问，Then 详情与新动作即时阻断、搜索/CDN 在 60 秒内移除且旧事件不能复活。
- AC-4：Given listing unlisted/withdrawn 或 resource deleted，When 治理状态未变化且用户访问直链，Then unlisted 直链可读，withdrawn/deleted 返回 tombstone；审核系统不把它们误写为 suspended/rejected。
- AC-5：Given grant revoked 或作者申诉，When 平台复核，Then listing 展示与 entitlement 分别计算，原 decision 不可变、产生新 case/decision、处理者分离且模型输出不被伪造为最终 actor。

## 14. 测试场景

- 正常：预审批准/拒绝、举报、暂停、修订重审、申诉和恢复上架。
- 边界：重复举报、多个类别、新 ListingRevision、unlisted 直链、withdrawn/deleted tombstone、grant revoked、同一作者多 listing。
- 失败：扫描服务中断、证据损坏、索引/CDN 延迟、审核会话失效。
- 权限：非 owner 提交、普通用户处置、平台审核员越级/项目越权、举报人隐私、隔离内容访问和审计导出。
- 并发/恢复：双审核员 CAS、暂停与批准竞态、outbox 重放和服务重启。

## 15. 交付与回退

- 社区公开前先启用审核后台与紧急 kill switch；按资源类型逐步开放队列。
- 审核系统降级时新发布 fail-closed；已上架内容保持实时紧急处置能力。
- 交付证据包括不可变 ListingRevision 审核、URL/状态矩阵、owner 与平台角色隔离、权限红队、缓存绕过、60 秒传播、申诉和审计演练。

## 16. 已决策事项与开放问题

- 已决策：审核模型只辅助，不替代人工责任；审核只针对不可变 ListingRevision，任何固定内容变化必须重新审核。
- 已决策：处置隔离展示/访问，不销毁不可变修订和审计证据。
- 开放问题：无阻塞 V1 Community 的开放问题。
