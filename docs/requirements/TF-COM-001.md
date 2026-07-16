# 创作作品发布与创作者主页

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-001 |
| 标题 | 创作作品发布与创作者主页 |
| 状态 | defined |
| 版本 | V1 Community |
| 优先级 | P0 |
| 全局位置 | 社区 |
| 直接依赖 | TF-PLT-001、TF-WF-005、TF-MED-012、TF-COM-004、TF-COM-006 |
| 责任域 | 社区产品 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

创作者需要发布成片、图片或小说作品并形成主页，但公开作品不能被误解为开放了其工作流、世界观、OC 或商业权。发布对象必须固定作品修订，展示来源与署名，并经过审核。

## 3. 目标与非目标

- 从可变 `CreativeWorkDraft` 冻结不可变 `CreativeWorkRevision`，再以 `ListingDraft -> ListingRevision` 发布，提供作品详情、创作者主页、撤回和来源展示。
- 将作品展示、来源引用和复用授权明确分开。
- 非目标：不自动公开源工作流，不提供付费、分成、复杂推荐或 Agent/Recipe 独立 listing。

## 4. 用户与权限

- V1 Community 只有项目/作品 owner 可查看私有项目内容、创建作品草稿、冻结 revision、创建 listing、提交审核和撤回；V1 不存在项目成员、共享发布、项目审阅或只读角色，这些能力仅在 TF-TEAM-001 后生效。
- 社区访客仅按 display entitlement 查看；reference、clone、install、execute、redistribute、派生和商业使用分别判断，不从公开状态推导。
- 审核员按 TF-COM-006 处理内容，平台管理员的处置必须被审计。

## 5. 用户场景与主流程

1. owner 从时间线、小说或图片结果创建/更新 `CreativeWorkDraft`，确认内容与来源后冻结为 `CreativeWorkRevision`。
2. owner 为该 revision 创建可变 `ListingDraft`，填写公开标题、摘要、封面、标签、来源、署名和可选 `LicenseOfferRevision` 引用。
3. 系统校验媒体、权利和当前 entitlement，将 draft 冻结为不可变 `ListingRevision` 后提交审核；审核只针对该 revision/content hash。
4. 审核通过后 listing 进入 listed，详情页和创作者主页展示审核通过的固定 ListingRevision。
5. owner 可 unlist 或 withdraw；后续作品或公开信息变化必须创建新的 CreativeWorkRevision/ListingRevision 并重新走适用审核。

## 6. 功能需求

- FR-1：创作编辑必须发生在 `CreativeWorkDraft`；发布前以 CAS 冻结为固定 `CreativeWorkRevision`，listing 不得指向 draft、latest 或可变时间线。
- FR-2：作品内容使用 CreativeWorkContent 合同，至少含 title、summary、cover_ref、primary_media_refs、source_revision_refs、attribution_manifest 和 public_metadata。
- FR-3：公开信息只在可变 `ListingDraft` 中编辑；每次提交审核必须生成不可变 `ListingRevision`，固定 target CreativeWorkRevision、public metadata ArtifactVersion、offer revision refs、attribution manifest 与 content hash。
- FR-4：发布前必须验证所有媒体可读、当前 `EntitlementDecision`、署名完整性、素材权利和审核策略；moderation decision 必须绑定准确 listing_revision_id/content hash。
- FR-5：作品详情展示当前可公开的固定 ListingRevision、作者、媒体、摘要、来源/署名和明确动作权限，不暴露私有 prompt/工作流；创作者主页只发现 listed + approved 版本。
- FR-6：作品公开只表示 listing 可展示；任何复用必须来自 `LicenseOfferRevision`/direct Grant，并按 reference、clone、install、execute、redistribute、derivative 或 commercial 的准确 action 判定。
- FR-7：新 CreativeWorkRevision、公开元数据或 offer 变化不得静默替换已 listed 版本；必须创建新 ListingRevision、显示 diff 并重新走适用审核。
- FR-8：unlisted 退出搜索和创作者主页发现但 canonical URL 仍可访问；新行为继续按当前 entitlement 判定。
- FR-9：withdrawn 仅公开 tombstone 并阻断新 LicenseAcceptance、install 和 clone；suspended 隐藏内容并阻断新行为；grant revoked 与 listing 展示独立，resource deleted 必须转 tombstone 且保留隔离审计。
- FR-10：作品发布不等于工作流模板发布；源工作流只在 owner 另行通过 TF-COM-003 发布且用户取得 clone entitlement 时可克隆。

## 7. 交互与展示

- 发布向导分为作品草稿/冻结版本、公开信息、来源署名、版本化 offer、预览和审核；明确显示将提交的 ListingRevision ID。
- 详情页首屏呈现实际作品媒体/正文，不使用空洞营销内容替代。
- 权限按 usage scopes 与 clone/install/execute/redistribute 独立展示，未授权项明确为禁止。
- 创作者主页按作品类型筛选；unlisted 作品不进入主页列表但可从 canonical URL 访问，withdrawn 显示 tombstone，owner 可查看完整处置记录。

## 8. 数据、类型与公共接口

- `CreativeWork` 是 Resource；`CreativeWorkDraft` 是其可变编辑态，`CreativeWorkRevision` 通过 content_artifact_version_id 固定 CreativeWorkContent。
- `Listing` 保存 listing_id/owner_scope/kind；`ListingDraft` 保存 draft_version、target_resource_revision_id 与 public_metadata_artifact_version_id。
- `ListingRevision` 保存 listing_revision_id/content_hash、固定 CreativeWorkRevision、公开元数据 ArtifactVersion、offer_revision_refs[] 与 attribution_manifest[]；公开读取和审核不得直接读取 ListingDraft。
- ListingStatus、ModerationStatus 和 CreativeWork RevisionStatus 分属不同聚合；`LicenseOfferRevision`、`LicenseAcceptance`、`GrantSnapshot` 与当前 `EntitlementDecision` 使用 TF-COM-004 公共合同。
- 同 owner_scope 媒体与来源可使用固定 ArtifactRef 或 ResourceRef；跨 owner source 必须先提升为 ResourceRevision，再使用带 `grant_snapshot_id` 的固定 ResourceRef，禁止给裸 ArtifactRef 附加 grant 后发布。
- attribution_manifest 保存来源 revision、作者、角色、条款 hash 和展示格式，不复制私有内容。

## 9. 状态机与业务规则

- ListingDraft 可反复编辑；提交时冻结新 ListingRevision。ListingStatus：draft -> review_pending -> listed；listed 可 unlisted 或 withdrawn；重新上架或内容变更需按策略以新 ListingRevision 回到 review_pending。
- ModerationStatus 独立为 not_reviewed/pending/approved/rejected/suspended，不能用 listed 表示审核通过。
- CreativeWorkRevision 使用 RevisionStatus，发布动作不修改 revision 状态。
- 提交审核和撤回以 listing_id + listing_revision_id/content_hash 幂等；审核结果只作用于固定 ListingRevision，不沿用到后续 revision。
- unlisted 保留直链；withdrawn 和 resource deleted 返回 tombstone；suspended 内容读取 fail-closed；grant 状态变化不能隐式改写 ListingStatus。

## 10. 失败、降级与恢复

- 媒体缺失、授权不足、署名不全或审核服务不可用时 ListingRevision 保持不可公开，不创建半公开页面；ListingDraft 与问题清单保留。
- 索引或 CDN 失败时 listing 真相保持数据库状态，公开读取采取 fail-closed 并由 outbox 恢复。
- 发布期间权限撤回则重新计算；失败后保留草稿与问题清单。
- 已 listed 作品被 suspended 时媒体和详情隐藏、新动作阻断；withdrawn/deleted 使用不泄露内容的 tombstone，审计证据保留。

## 11. 安全、隐私、内容与授权

- 默认只公开 public_metadata 与选择的媒体，不公开项目文件、运行 trace、成本、prompt 或未选候选。
- 真人肖像/声音、未成年人和第三方素材必须通过同意/权利 Gate。
- LicenseAcceptance 和 GrantSnapshot 证明历史发布/复用依据，不赋予新动作；收藏不产生快照，每次新 reference/clone/install/execute/redistribute/派生/导出重算当前 EntitlementDecision。

## 12. 观测与运营

- 记录 CreativeWorkDraft 冻结、ListingDraft/ListingRevision、校验、审核提交/结果、listed、unlisted、withdrawn、suspended、tombstone、查看和授权阻断。
- 指标包括提交到上架转化、审核时长、媒体加载成功率、撤回率、署名缺失和授权阻断率。
- 支持人员可按 correlation_id 查看公开版本、审核和权利决策，不默认访问项目私有内容。

## 13. 验收标准

- AC-1：Given owner 完成 CreativeWorkDraft，When 冻结作品并提交发布，Then 生成 CreativeWorkRevision 与不可变 ListingRevision，审核、详情页和主页展示同一固定 content hash。
- AC-2：Given 作品仅有 display offer，When 访客查看并尝试 clone/execute，Then 查看允许、受控动作被拒绝且没有伪造 acceptance/snapshot。
- AC-3：Given owner 创建作品 revision B 或修改公开信息，When A 正在 listed，Then A 不被静默替换，B 对应的新 ListingRevision 未审核前不可公开替代。
- AC-4：Given listing 依次 unlisted、withdrawn、suspended，When 访问搜索与 canonical URL，Then unlisted 不可发现但直链可读，withdrawn 返回 tombstone 且禁止新 acceptance/install/clone，suspended 隐藏内容并阻断新行为。
- AC-5：Given grant revoked 或 resource deleted，When 访问 listing，Then revoked 不自动改变展示状态但新动作按 entitlement 阻断，deleted 返回 tombstone 且历史审核/GrantSnapshot/合法运行证据仍存在。
- AC-6：Given CreativeWorkDraft 包含跨 owner 裸 ArtifactRef，When owner 冻结或提交 listing，Then 发布被阻断；只有提升后的固定 ResourceRef 带有效授权证据并通过当前 entitlement 校验时才可进入审核。

## 14. 测试场景

- 正常：图片、小说、视频作品发布，主页展示、版本升级、unlist/withdraw。
- 边界：多媒体作品、空可选来源、最长标题、多个作者署名、旧浏览缓存。
- 失败：媒体缺失、审核超时、索引失败、CDN 失败、授权变化。
- 权限：非 owner 查看私有项目/发布、跨 owner 裸 ArtifactRef、只 display、action scope 混淆、派生/商业尝试、私有 prompt 检查和平台审核角色隔离。
- 并发/恢复：重复提交审核、撤回与审核竞态、outbox 重放和服务重启。

## 15. 交付与回退

- 先对受邀创作者开放发布；listing、搜索索引和主页分别受功能开关控制。
- 回退公开 UI 时可统一 unlist 新展示但不得删除作品 revision、审核和授权证据。
- 交付证据包括 CreativeWorkDraft 冻结、ListingDraft/Revision、固定 revision 审核、URL 状态矩阵、owner-only、权限矩阵、媒体加载、撤回和审计报告。

## 16. 已决策事项与开放问题

- 已决策：发布 CreativeWorkRevision，不等同公开源工作流或授予任何复用权。
- 已决策：创作草稿、作品修订、listing 草稿、listing 修订、上架与审核使用独立状态族；审核只针对不可变 ListingRevision。
- 已决策：V1 私有项目 owner-only；跨 owner 来源只接受带授权证据的固定 ResourceRef，平台审核员不是项目成员。
- 已决策：Screenplay 不能包装为 CreativeWork 绕过 TF-PLT-003/TF-STY-006 的完全私有边界。
- 开放问题：无阻塞 V1 Community 的开放问题。
