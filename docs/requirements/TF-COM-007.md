# Agent 与 Media Recipe 独立发布和安装

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-007 |
| 标题 | Agent 与 Media Recipe 独立发布和安装 |
| 状态 | defined |
| 版本 | V1.5 |
| 优先级 | P1 |
| 全局位置 | 社区/Agent Studio/Media Recipe Lab |
| 直接依赖 | TF-AGT-001、TF-AGT-002、TF-AGT-006、TF-MR-001、TF-WF-009、TF-COM-003、TF-COM-004、TF-COM-005、TF-COM-006 |
| 责任域 | 生态产品/Agent 与媒体平台 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

V1 Community 工作流可以携带合规 Agent/Recipe 修订，但用户无法单独发现、审查、安装或替换它们。V1.5 才开放独立 listing，且不能因此放松 V1 的调用矩阵、工具安全和任意代码禁令。

## 3. 目标与非目标

- 为不可变 AgentRevision、MediaRecipeRevision 提供 `ListingDraft -> ListingRevision` 独立发布、发现、entitled 安装、固定调用、升级与下架。
- 保持 V1 工作流内嵌包向前兼容，并把安装依赖、授权和替换显式化。
- 非目标：不提供付费、分成、任意插件代码、自动升级或 Agent/Recipe 嵌套。

| 切片 | 功能 | 数据兼容 | 验收证据 |
| --- | --- | --- | --- |
| V1 Community 基线 | 工作流可内嵌合规固定 Agent/Recipe 或引用 managed preset；无独立 listing/搜索/安装 | WorkflowPackageManifest 保留 included/reusable/managed 依赖，revision ID 不变 | TF-COM-003 包校验与克隆 E2E |
| V1.5 本项 | 增加独立 listing、搜索、安装、升级、替换和下架 | listing 只是固定 revision 的新包装；既有内嵌包无需迁移且可选择关联 listing | 独立发布到安装调用全链路、下架和兼容测试 |

## 4. 用户与权限

- 本项不依赖 TF-TEAM-001；Agent/Recipe owner 配置 ListingDraft、LicenseOfferRevision 和公开文档并提交审核，平台审核角色不成为项目成员。
- 用户可按 display 查看；install、execute、redistribute、clone、派生与商业使用分别判断当前权限，任何 scope 不互相推导。
- 工具、模型、provider、skills 和素材依赖仍由各自所有者/平台授权，listing 不代发凭证。

## 5. 用户场景与主流程

1. owner 选择 active 的固定 AgentRevision 或 MediaRecipeRevision 创建独立发布包与 ListingDraft。
2. 系统解析 typed I/O、依赖、工具/模型要求、actions、示例和安全扫描；owner 冻结不可变 ListingRevision 后提交审核。
3. 审核只针对该 ListingRevision/content hash；通过且 listed 后可在社区按 Agent/Recipe 类型发现。
4. 用户查看能力、风险、依赖、准确 LicenseOfferRevision 与估算成本；需要时创建 LicenseAcceptance，以 install action 取得 entitlement 和 GrantSnapshot，将 snapshot-backed ResourceRef 安装到个人资源库。
5. 画布调用前以 execute action 重新决策；新版本只提示升级，用户查看内容/条款 diff、重新接受并显式替换。

## 6. 功能需求

- FR-1：独立 ListingDraft 必须选择固定 AgentRevision 或 MediaRecipeRevision；提交审核时冻结 ListingRevision，固定 target revision、package manifest、public metadata、offer revision refs、attribution 与 content hash，不得指向 latest、草稿或可变配置。
- FR-2：发布包必须声明 typed I/O、Agent/Skill/Recipe 依赖闭包、managed/reusable/included/replacement slots、工具 scopes、provider capability，以及 install/execute/redistribute 等准确授权要求。
- FR-3：Agent 包必须通过 AGT-005 工具/凭证策略；Recipe 包必须保持 MR-001 的有限算子 DAG 与禁用项。
- FR-4：V1 调用矩阵不变：Agent 禁止 Agent/Workflow/Recipe；Recipe 禁止 Agent/Workflow/Recipe/Human Gate/RequestInput。
- FR-5：安装必须针对准确 LicenseOfferRevision/direct Grant 完成所需 LicenseAcceptance，以 install action 解析当前 EntitlementDecision 并捕获 GrantSnapshot；LibraryEntry/InstallationRecord 保存固定 snapshot-backed ResourceRef，不得复制 CredentialBinding 或 secret。
- FR-6：缺失依赖可由 typed replacement slot 显式解决；未解决时可安装为配置中但禁止编译运行。
- FR-7：每次编译/调用必须按 execute action 生成当前 EntitlementDecision；历史 install GrantSnapshot 不能授权 execute，reference/derivative/commercial 也不能替代 install/execute。
- FR-8：升级必须展示 I/O、SOP/图、tool/provider、actions、offer terms、成本和行为 diff；必要时重新 LicenseAcceptance，显式确认后创建新固定安装记录。
- FR-9：unlisted 退出发现但 canonical URL 可访问且新 install 仍按当前 entitlement；withdrawn 返回 tombstone 并阻断新 acceptance/install/clone；suspended 隐藏内容并阻断新行为；grant revoked 与展示独立；resource deleted 返回 tombstone。
- FR-10：从 V1 内嵌依赖关联独立 listing 不改变 package hash、原 revision 或既有克隆 lineage；若新包内嵌该 capability，仍必须单独取得 redistribute action。

## 7. 交互与展示

- 详情页分别展示 Agent 的 SOP/skills/tools/I/O 与 Recipe 的算子摘要/provider/control/I/O。
- 安装前集中显示数据披露、工具 scopes、依赖、替换槽、成本范围、LicenseOfferRevision、install/execute/redistribute 状态和审核 revision。
- Studio/Lab 与节点选择器显示“已安装固定版本”，升级徽标不会自动改变调用。
- 失败页定位 ListingRevision、acceptance、install/execute entitlement 或依赖问题，并提供接受新条款、替换/卸载操作。

## 8. 数据、类型与公共接口

- `CapabilityListing` 使用公共 Listing 聚合；ListingDraft 可变，ListingRevision 固定 capability_kind、resource_id/revision_id、package_manifest_ref、public metadata ArtifactVersion、offer_revision_refs[]、attribution_manifest[] 和 content hash。
- `InstallationRecord` 含 owner_scope、source_listing_revision_id、snapshot-backed ResourceRef、license_acceptance_id?、install_grant_snapshot_id、dependency_resolutions[]、installed_at 和 status。
- Agent 调用仍使用 AgentInvoke；Recipe 调用仍使用 MediaRecipeInvoke。
- 工作流包继续使用 WorkflowPackageManifest；独立 listing 不创建第二套 revision 真相。

## 9. 状态机与业务规则

- Agent/Recipe RevisionStatus、ListingRevision、ListingStatus、ModerationStatus、Offer/GrantStatus 和安装状态分离；moderation decision 只覆盖不可变 ListingRevision/content hash。
- 安装以 owner + listing revision + offer/grant revision 幂等；显式升级创建新 acceptance/snapshot/record 并保留旧安装引用。
- listing 上架不自动激活 capability revision；revision active 也不自动 listed。
- 当前授权/依赖/ListingStatus 改变不修改历史安装记录，只影响新的安装、编译、调用、派生、发布或导出；unlisted/withdrawn/suspended/deleted 的 URL 语义遵循 TF-COM-004。

## 10. 失败、降级与恢复

- 包校验、审核、acceptance 或授权服务失败时不能 listed/安装，保留 ListingDraft 和问题清单。
- 安装事务中部分依赖失败则回滚，或保存为明确 `needs_configuration`，不得伪装可运行。
- 安全 suspension/deletion 传播失败由带 listing_revision_id 的 outbox 重放；编译前仍检查权威状态与 execute entitlement 以 fail-closed。
- 升级不兼容时保留旧安装和工作流引用，提供替换分支而非覆盖。

## 11. 安全、隐私、内容与授权

- 发布包扫描 secret、任意代码/网络、恶意 prompt、越权 tools、无权模型/素材和第三方许可。
- CredentialBinding 始终由安装者在本地 owner_scope 重新绑定，不进入 listing 或安装包。
- display/reference/derivative/commercial 与 clone/install/execute/redistribute 分权；LicenseAcceptance/GrantSnapshot 不替代新调用时的当前 EntitlementDecision。

## 12. 观测与运营

- 记录包校验、ListingDraft/Revision、审核、offer acceptance、install/execute/redistribute decision 与 snapshot、依赖解析、升级、状态变化和撤权阻断。
- 指标包括发布通过率、安装完成率、首次调用成功率、槽位失败、升级采用、不兼容率和安全下架传播。
- 审计可从工作流节点追踪 installation、listing、revision、授权、依赖和实际调用。

## 13. 验收标准

- AC-1：Given 合法 AgentRevision 和需接受的 install/execute offer，When 独立上架、接受并安装，Then 产生 LicenseAcceptance、install Decision/Snapshot、固定 ResourceRef，节点选择器经 execute decision 可调用且不含作者凭证。
- AC-2：Given Recipe 含嵌套 Recipe/Agent 或任意代码，When 发布校验，Then 上架被阻断并定位违规算子。
- AC-3：Given V1 工作流已内嵌 revision A，When A 在 V1.5 关联 listing，Then 原 package hash、克隆和运行解释完全不变。
- AC-4：Given revision B I/O 或 offer terms 不兼容，When 用户查看升级，Then 不自动替换 A，显示依赖/条款影响、要求重新接受并可保留旧安装。
- AC-5：Given listing unlisted/withdrawn/suspended、grant revoked 或 resource deleted，When 搜索、直链、安装/调用，Then 分别满足直链可读、tombstone/禁新安装、隐藏/全阻断、展示独立但 entitlement 阻断、deleted tombstone，既有合法运行/Acceptance/Snapshot 可审计。

## 14. 测试场景

- 正常：Agent/Recipe 发布、发现、安装、配置依赖、画布调用、升级和卸载。
- 边界：零工具 Agent、provider 专属 Recipe、多个 replacement、旧内嵌包关联 listing。
- 失败：secret、嵌套调用、任意代码、依赖撤权、不兼容升级和审核中断。
- 权限：usage/action 矩阵、跨 owner、acceptance revision、install 与 execute 分离、凭证重绑、各 listing 状态后新调用和历史证据访问。
- 并发/恢复：重复安装、升级与调用竞态、outbox 重放、服务重启和下架传播。

## 15. 交付与回退

- V1.5 先对官方/受邀作者开放；Agent 与 Recipe listing 分别受开关控制。
- 回退时关闭新 listing/安装，保留既有固定调用及权威授权检查；V1 内嵌包路径持续可用。
- 交付证据包括 V1.5 版本切片兼容、ListingDraft/Revision 审核、offer/acceptance、install/execute/redistribute 权限矩阵、包安全、独立安装、URL 状态、首次调用和升级 E2E。

## 16. 已决策事项与开放问题

- 已决策：独立 Agent/Recipe listing 只在 V1.5；V1 Community 仅允许工作流内嵌合规修订或 managed preset。
- 已决策：独立发布不改变调用矩阵；install 与 execute 独立授权，不开放任意代码、嵌套或凭证打包。
- 开放问题：无阻塞 V1.5 的开放问题。
