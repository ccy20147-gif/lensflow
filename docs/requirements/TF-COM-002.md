# 世界观与 OC 发布、收藏和引用

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-002 |
| 标题 | 世界观与 OC 发布、收藏和引用 |
| 状态 | defined |
| 版本 | V1 Community |
| 优先级 | P0 |
| 全局位置 | 社区/资源库 |
| 直接依赖 | TF-PLT-001、TF-WF-005、TF-STY-002、TF-STY-003、TF-COM-004、TF-COM-006 |
| 责任域 | 社区产品/资源平台 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

世界观和 OC 是可跨作品复用的核心社区资源。用户需要先查看和收藏，再在明确授权下将固定修订加入资源库并引用。若引用跟随 latest 或把公开当许可，会破坏创作与权利证据。

## 3. 目标与非目标

- 支持 WorldRevision、CharacterRevision 通过不可变 ListingRevision 审核发布，并提供收藏、取得 entitlement 后加入资源库、固定引用和显式升级。
- 分离公开展示、usage scopes 与 clone/install/execute/redistribute actions，并保存 acceptance、当前决策和历史快照。
- 非目标：不提供交易、自动追随 latest、默认派生许可或复杂推荐。

## 4. 用户与权限

- V1 Community 只有资源 owner 可管理 ListingDraft、冻结 ListingRevision 和配置 LicenseOfferRevision；共享项目发布角色仅在 TF-TEAM-001 后生效。
- 访客可按 display entitlement 查看并收藏非可执行 locator；加入资源库和用于创作必须分别满足 install/reference 等准确 action。
- 审核员处置 listing；撤权与法律限制由授权/治理能力执行并留痕。

## 5. 用户场景与主流程

1. owner 选择固定 WorldRevision 或 CharacterRevision，在 ListingDraft 中配置公开字段和 LicenseOfferRevision，并冻结 ListingRevision 提交审核。
2. 审核只批准该 ListingRevision；listed 后详情可查看，收藏只保存 `executable=false` 的 ResourceLocator。
3. 用户点击“加入可用资源库”，系统展示准确 offer revision；需要接受时创建 LicenseAcceptance，再以 install action 计算 EntitlementDecision、捕获 GrantSnapshot 并创建固定 ResourceRef。
4. 在创作中引用时以 reference action 重新判断 EntitlementDecision，并为该次引用捕获新的 GrantSnapshot。
5. owner 发布新 resource/listing revision 后用户收到可升级提示；升级需查看内容/条款 diff、重新接受/授权并显式确认。

## 6. 功能需求

- FR-1：ListingDraft 只能选择不可变 WorldRevision 或 CharacterRevision；提交审核必须冻结不可变 ListingRevision，固定资源 revision、公开字段 ArtifactVersion、offer revision refs、署名与 content hash，不得指向 draft/latest。
- FR-2：公开字段由 owner 选择，但最少展示标题/名称、作者、resource/listing revision、授权摘要和必要署名；moderation decision 只绑定该 ListingRevision。
- FR-3：收藏只保存 user + `ResourceLocator(executable=false)`，不复制内容、不接受条款、不创建 GrantSnapshot，不能被画布/Agent/Workflow 当作 ResourceRef 消费。
- FR-4：“加入可用资源库”必须对固定 resource revision 解析 install entitlement；需要接受时创建 LicenseAcceptance，获准后创建含 source_listing_revision_id、GrantSnapshot 和固定 ResourceRef 的 LibraryEntry。
- FR-5：在创作中引用必须按 reference 重新生成 EntitlementDecision 与 GrantSnapshot；跨 owner ResourceRef 必须记录 grant_snapshot_id、role 和固定 revision，历史库快照不得代替本次检查。
- FR-6：display/reference/derivative/commercial 与 clone/install/execute/redistribute 分权；UI/API 不得用 `is_public`、commercial 或任一 scope 推导其他动作。
- FR-7：新 resource/listing/offer revision 只产生升级提示；升级显示内容与条款 diff，必要时重新 LicenseAcceptance，并创建新的 snapshot-backed ResourceRef，不改历史运行。
- FR-8：unlisted 不可发现但 canonical URL 可访问；withdrawn 返回 tombstone 并阻断新 acceptance/install/clone；suspended 隐藏内容并阻断新行为。
- FR-9：grant revoked 与 listing 展示独立，但新引用/重跑/派生按当前 EntitlementDecision 阻断；resource deleted 使 listing 为 tombstone，已合法运行与隔离审计不删除。
- FR-10：Character 提升 lineage 和 World 来源必须在 ListingRevision、详情、LibraryEntry 与引用 attribution manifest 中保留。

## 7. 交互与展示

- 世界详情展示规则、地点、势力、时间线和 OC 摘要；角色详情展示身份、外观/声音与来源世界。
- “收藏”“加入可用资源库”“在创作中引用”是三个独立操作，分别标明 locator、install entitlement 和 reference entitlement 的影响。
- 授权面板分组展示 usage scopes 与 clone/install/execute/redistribute，并显示 offer revision、是否需接受和用途示例，不以模糊“免费使用”代替。
- 升级入口显示当前/新 revision、内容 diff、条款变化和受影响草稿。

## 8. 数据、类型与公共接口

- `ResourceListing` 使用公共 Listing 聚合；`ListingDraft` 可变，`ListingRevision` 固定 resource_type/resource_id/revision_id、public_field_manifest ArtifactVersion、offer_revision_refs[]、attribution_manifest[] 与 content_hash。
- `Favorite` 含 user_id、ResourceLocator 和 added_at；locator 强制 executable=false，不复制资源内容、acceptance、grant 或 snapshot。
- `LibraryEntry` 含 owner_scope、snapshot-backed ResourceRef、source_listing_revision_id、license_acceptance_id?、added_at 和 last_entitlement_hint；hint 不是授权真相。
- 实际入库/引用严格使用 LicenseOfferRevision、LicenseAcceptance、ResourceRef、GrantSnapshot 和 EntitlementDecision 公共合同。

## 9. 状态机与业务规则

- ResourceRevision、ListingRevision、ListingStatus、ModerationStatus、Offer/GrantStatus 独立演进；审核不沿用到新的 ListingRevision。
- 收藏/取消收藏以 user + locator 幂等；入库以 owner + resource revision + offer/grant revision 幂等，并保留实际 acceptance/snapshot。
- unlisted 不自动删除收藏或库 ref；withdrawn/suspended/deleted 也不改写历史库记录。withdrawn 阻断新 acceptance/install/clone，suspended/deleted 阻断内容访问与新行为，其余动作继续按当前 entitlement 和治理状态判定。
- revision 升级永不原地替换 LibraryEntry 的固定 ref；采用新 ref 是显式动作。

## 10. 失败、降级与恢复

- 当前授权/acceptance 服务不可用时新入库与引用 fail-closed；收藏可在不泄露内容条件下只创建非可执行 locator。
- 资源媒体暂不可用时详情显示安全占位，不退回 latest 或其他 revision。
- 索引延迟不影响 canonical URL 对 unlisted/withdrawn/suspended/deleted 的数据库判定；审核、状态与撤权事件由 outbox 重放。
- 升级并发冲突保留原库 ref，提示重新加载 diff。

## 11. 安全、隐私、内容与授权

- 只公开 public_field_manifest；私有 world notes、参考素材、同意凭证和未选变体不暴露。
- 真人/声音、未成年人及第三方素材继续受内容与权利 Gate；资源作者声明不替代验证。
- 署名按固定 resource/listing/offer revision 与 GrantSnapshot 渲染，派生作品必须保留可追踪 lineage。

## 12. 观测与运营

- 记录 ListingDraft/Revision、收藏 locator、LicenseAcceptance、入库 entitlement/snapshot、引用、升级、状态迁移、撤权阻断和条款确认事件。
- 指标包括详情到收藏/库/引用转化、授权阻断、升级采用率、撤权后新动作和断链率。
- 审计可从使用动作回溯 ResourceRevision、GrantSnapshot、当前 decision 和创作运行。

## 13. 验收标准

- AC-1：Given 仅有 display 的 OC，When 用户收藏，Then 只创建 executable=false ResourceLocator；When 加入资源库或引用，Then 因无 install/reference entitlement 被阻断。
- AC-2：Given WorldRevision 的 install offer 需接受且 reference 允许，When 跨 owner 入库并引用，Then LicenseAcceptance、两次 EntitlementDecision/GrantSnapshot 可审计，LibraryEntry 与引用均固定 revision。
- AC-3：Given owner 发布 resource/listing/offer revision B，When 用户未升级，Then 既有 LibraryEntry 和运行继续指向 A；升级必须显示 diff 并在需要时重新接受。
- AC-4：Given listing unlisted/withdrawn/suspended 或 grant revoked，When 搜索、直链和新动作发生，Then unlisted 直链可读，withdrawn 为 tombstone，suspended 隐藏，revoked 不改展示但新动作被当前决策阻断。
- AC-5：Given Character 来自 World 内嵌 OC 且源资源后来 deleted，When 查看历史引用，Then listing 返回 tombstone、promotion lineage 与合法快照可审计且未回写来源 WorldRevision。

## 14. 测试场景

- 正常：世界/OC 上架、收藏、入库、引用、升级和来源展示。
- 边界：同名 OC、仅 display、收藏与入库分离、条款不变内容升级、内容不变条款升级、unlisted/withdrawn/suspended/deleted。
- 失败：授权服务中断、资源媒体丢失、索引延迟、Grant 过期和 diff 冲突。
- 权限：八类 scope/action 矩阵、跨 owner、acceptance 版本、撤权、新发布/导出和法律限制。
- 并发/恢复：重复收藏/入库、升级竞态、outbox 重放和服务重启。

## 15. 交付与回退

- 先对官方/受邀世界与 OC 开放，再按审核容量扩大。
- 关闭公开入口时保留项目资源库、固定引用和历史审计；不得自动删除用户产物。
- 交付证据包括 ListingDraft/Revision 审核、locator/ResourceRef 隔离、offer acceptance、权限矩阵、URL 状态、固定 revision、升级、撤权和 lineage E2E。

## 16. 已决策事项与开放问题

- 已决策：展示、收藏、入库、引用、clone/install/execute/redistribute、派生与商业使用是不同动作和权限。
- 已决策：引用固定 revision；GrantSnapshot 不是当前 Entitlement，也不是永久授权。
- 开放问题：无阻塞 V1 Community 的开放问题。
