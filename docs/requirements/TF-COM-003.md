# 工作流模板发布、克隆与派生

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-003 |
| 标题 | 工作流模板发布、克隆与派生 |
| 状态 | defined |
| 版本 | V1 Community |
| 优先级 | P0 |
| 全局位置 | 社区/主画布 |
| 直接依赖 | TF-GOV-002、TF-PLT-001、TF-WF-009、TF-COM-004、TF-COM-006 |
| 责任域 | 社区产品/工作流平台 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

开放创作需要让作者发布可复刻的工作流，而不仅是成品截图。工作流包若遗漏私有资源、schema、Agent、Recipe 或授权，会在克隆后失败；若携带 secret 又会形成严重风险。

## 3. 目标与非目标

- 发布固定 WorkflowRevision 及闭合、typed、可校验的依赖包。
- 支持预览、克隆、replacement slot 解析、派生 lineage 和授权/署名。
- 非目标：不发布可变草稿、不打包 secret、不提供付费市场；V1 不提供 Agent/Recipe 独立 listing。

## 4. 用户与权限

- V1 Community 只有工作流 owner 可创建包、ListingDraft、LicenseOfferRevision 并提交审核；项目成员和共享发布角色仅在 TF-TEAM-001 后生效。
- 用户可按 display entitlement 查看包摘要；clone、execute、redistribute 与派生必须分别满足工作流及全部 reusable/included 依赖的准确 action。
- 审核员处理公开内容，平台执行第三方代码许可与安全校验。

## 5. 用户场景与主流程

1. 作者选择一个不可变 WorkflowRevision 并启动打包。
2. 系统计算依赖闭包，将依赖标为 included、reusable、managed 或 replacement_slot。
3. owner 补齐槽位说明、署名、LicenseOfferRevision 与示例输入，在 ListingDraft 中固定 package；校验通过后冻结不可变 ListingRevision 提交审核。
4. 用户预览节点、I/O、依赖、费用、准确 offer revision 和 actions，按需创建 LicenseAcceptance 后选择克隆。
5. 系统对模板执行 clone entitlement、对 included 依赖执行 redistribute entitlement、对 reusable 依赖保存固定 ResourceRef/执行要求，捕获 GrantSnapshots 后创建 owner-only 私有 WorkflowDraft 与 lineage；运行前再检查 execute。

## 6. 功能需求

- FR-1：发布包必须使用 WorkflowPackageManifest 并固定 workflow_revision_id。
- FR-2：每个 PackageDependency 必须声明 kind、revision/schema、inclusion_mode、所需 action（至少区分 clone/redistribute/execute）、grant_requirement 和可选 provider capability。
- FR-3：依赖闭包必须无循环；缺失、latest、私有不可授权依赖或不兼容 schema 在发布前阻断。
- FR-4：无法合法内嵌/复用的依赖必须成为 typed replacement_slot，声明所需 schema/capability/用途。
- FR-5：V1 Community 只可在取得 redistribute entitlement 后内嵌不可变 Agent/Recipe 修订，或依赖官方 managed preset；不创建其独立可发现 listing。
- FR-6：包及示例不得包含 secret、CredentialBinding、私有 URL、未授权媒体或第三方代码/品牌资产。
- FR-7：克隆必须针对准确 LicenseOfferRevision/direct Grant 完成所需 LicenseAcceptance，并以 clone action 生成当前 EntitlementDecision；获准后捕获 GrantSnapshot，创建新的 owner-only 私有 WorkflowDraft，不修改源 revision。
- FR-8：included 依赖必须有 redistribute action；reusable 依赖不得复制内容，必须保存固定 ResourceRef 与运行所需 execute action；reference/derivative/commercial 不得替代这些 actions。
- FR-9：派生记录 source workflow/listing/package revision、每个 offer/grant revision、LicenseAcceptance、替换映射、GrantSnapshots 和 attribution manifest。
- FR-10：发布更新形成新 package 与不可变 ListingRevision；既有克隆不自动升级，用户查看内容、依赖和条款 diff 后显式迁移。

## 7. 交互与展示

- 发布页显示图预览、公开 I/O、依赖树、槽位、provider 要求、估算成本、ListingRevision、usage/actions、offer revision 和校验问题。
- 模板详情展示实际节点图与依赖，不用宣传图替代可复刻信息。
- 克隆向导逐个解决条款接受、clone/redistribute entitlement、replacement slot、无权依赖、凭证和 provider 配置。
- 派生模板明确显示来源作者、revision、修改摘要和授权范围。

## 8. 数据、类型与公共接口

- 使用主表 WorkflowPackageManifest 与 PackageDependency，不创建字符串依赖列表。
- `WorkflowTemplateListing` 使用公共 Listing 聚合；`ListingDraft` 可变，`ListingRevision` 固定 package_manifest_artifact_version_id、workflow revision、public metadata、offer_revision_refs[]、attribution_manifest[] 和 content hash。
- `CloneResolution` 记录 template/dependency -> included/ref/managed/replacement 的实际解析、required_action、LicenseAcceptance、EntitlementDecision、GrantSnapshot 和目标资源。
- 新 WorkflowDraft 通过 TF-WF-004 创建；包内 Agent/Recipe 调用仍固定 revision。

## 9. 状态机与业务规则

- WorkflowRevision、package 校验、ListingRevision、ListingStatus、ModerationStatus 与 Offer/GrantStatus 分离；moderation decision 仅绑定不可变 ListingRevision/content hash。
- 包内容 hash + workflow revision 保证发布幂等；任何依赖变化产生新 manifest。
- clone、redistribute 与 execute 是三个独立新动作，始终按当前授权复核；历史 GrantSnapshot 不自动许可再次克隆、打包或运行。
- replacement slot 未全部解析前可保存草稿但禁止编译运行。
- unlisted 退出发现但 canonical URL 可访问且新 clone 仍按当前 entitlement；withdrawn 返回 tombstone 并阻断新 acceptance/install/clone；suspended 隐藏内容并阻断新动作；grant revoked 不自动改变 listing 展示；resource deleted 转 tombstone。

## 10. 失败、降级与恢复

- 包校验失败返回依赖路径、错误类型和修复建议，不允许“带病上架”。
- 克隆中某依赖撤权时事务回滚或保留不可运行草稿与明确缺口，禁止偷偷替换。
- 索引/审核事件失败由 outbox 恢复；listing 不得在 moderation 未批准时公开。
- unlisted/withdrawn/suspended/deleted 不删除既有合法克隆；新运行仍按其自身依赖的 execute entitlement 与安全状态判断。

## 11. 安全、隐私、内容与授权

- 发布前执行 secret、恶意配置、第三方许可、素材权利、任意代码和网络引用扫描。
- included 依赖必须有 redistribute action；reusable 依赖不复制内容，只固定 snapshot-backed ResourceRef 与 execute requirement。
- 模板 display/reference/derivative/commercial 与 clone/install/execute/redistribute 分别授权；公开或 commercial 不得推出 clone/redistribute。

## 12. 观测与运营

- 记录包构建/校验、ListingDraft/Revision、审核、offer acceptance、clone/redistribute/execute decision、快照、槽位解析、派生和升级事件。
- 指标包括发布通过率、依赖闭合率、克隆成功率、槽位失败、首次运行成功率和断链率。
- 审计可从克隆 workflow 追溯源 package、每个依赖决策与替换映射。

## 13. 验收标准

- AC-1：Given 闭合合法包且模板有 clone、included 依赖有 redistribute，When 用户接受准确 offer 并克隆，Then 创建 owner-only 可编译私有 WorkflowDraft，Acceptance/Decision/Snapshot/lineage/署名完整。
- AC-2：Given 包含 secret、latest 或不可授权私有依赖，When 发布校验，Then 阻断并定位依赖路径。
- AC-3：Given Agent/Recipe revision 有 redistribute entitlement，When V1 发布并克隆，Then 可合法内嵌且社区不存在该依赖的独立 listing；仅有 reference/commercial 时发布被阻断。
- AC-4：Given replacement slot 未解析，When 用户运行，Then 编译前阻断且不发起 provider/Agent 调用。
- AC-5：Given listing unlisted/withdrawn/suspended、grant revoked 或 resource deleted，When 搜索、直链、克隆和运行，Then 分别满足直链可读、tombstone/阻断、隐藏/阻断、展示独立但 entitlement 阻断、deleted tombstone；既有合法克隆与证据不被改写。

## 14. 测试场景

- 正常：内置节点包、含 managed preset、含内嵌 Agent/Recipe、槽位克隆和派生发布。
- 边界：最大允许依赖、深依赖树、多个兼容替换、仅展示无 clone、clone 无 redistribute、unlisted 直链。
- 失败：循环、缺失 schema、secret、未授权素材、克隆中撤权和索引失败。
- 权限：usage scopes 与 clone/install/execute/redistribute 矩阵、acceptance revision、跨 owner 依赖和各 listing 状态后的新动作。
- 并发/恢复：重复发布、并发克隆、事务恢复、outbox 重放和版本升级竞态。

## 15. 交付与回退

- 先开放官方和受邀作者模板；包格式以 schema_version 管理并提供只读旧版解析。
- 可关闭新发布/克隆，既有私有 workflow、manifest 和审计保持可用。
- 交付证据包括 ListingDraft/Revision 审核、owner-only、依赖闭包 contract tests、secret 扫描、clone/redistribute/execute 权限矩阵、URL 状态、克隆首跑和 lineage E2E。

## 16. 已决策事项与开放问题

- 已决策：工作流可内嵌合规 Agent/Recipe 固定修订，但 V1 Community 不产生其独立 listing。
- 已决策：包必须闭合依赖或声明 typed replacement slot；clone、redistribute、execute 不可互相推导，凭证永不进包。
- 已决策：Screenplay、其 source span/prompt/ArtifactRef/lineage 和其他 private-only 依赖不得内嵌；必须阻断、移除或转为不携带源内容的 typed replacement slot。
- 开放问题：无阻塞 V1 Community 的开放问题。
