# 社区发现、搜索与个人资源库

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-005 |
| 标题 | 社区发现、搜索与个人资源库 |
| 状态 | defined |
| 版本 | V1 Community |
| 优先级 | P1 |
| 全局位置 | 社区/资源库 |
| 直接依赖 | TF-PLT-001、TF-WF-005、TF-COM-001、TF-COM-002、TF-COM-003 |
| 责任域 | 社区产品/搜索 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

社区资源只有能被按类型、作者、标签和授权发现，才能形成复用网络。同时搜索结果必须只投影审核通过的不可变 ListingRevision；收藏必须是非可执行定位，个人资源库必须保存取得当前 entitlement 后的固定快照引用，不能成为绕过授权的内容副本。

## 3. 目标与非目标

- 提供作品、世界、OC、工作流模板的基础浏览、关键词搜索、筛选与稳定分页。
- 提供收藏和个人资源库入口，明确 `ResourceLocator`、snapshot-backed `ResourceRef`、固定版本与当前权限提示。
- 非目标：不做个性化推荐、热度操纵排名、交易排序、Agent/Recipe 独立发现或自动 latest 升级。

## 4. 用户与权限

- 未登录访客只能搜索允许匿名 display 的 listing。
- 登录用户可收藏、在取得准确 entitlement 后加入自己 owner_scope 的资源库，并查看其有权访问的固定资源；V1 不存在项目成员共享资源库写权限。
- 作者可查看自己未公开对象的管理入口，但不得混入公共搜索结果。
- 运营可按审核权限排查索引，不因此获得资源复用权。

## 5. 用户场景与主流程

1. 用户进入社区，选择“世界观”并输入关键词。
2. 系统只返回当前 ListingStatus=listed、ModerationStatus=approved 且可 display 的不可变 ListingRevision。
3. 用户按标签、作者、usage scope 或 clone/install/execute/redistribute action hint 筛选，打开该 ListingRevision 的 canonical URL。
4. 用户收藏时只保存非可执行 ResourceLocator；加入可用资源库时按 COM-002/003/004 接受准确 offer、取得 entitlement 并保存 snapshot-backed ResourceRef。
5. owner 在项目资源选择器中找到该固定 ref；真正引用、克隆或运行时再次计算当前 EntitlementDecision。

## 6. 功能需求

- FR-1：V1 Community 索引类型仅含 CreativeWork、World、Character OC 和 Workflow Template listing。
- FR-2：搜索支持关键词、资源类型、标签、作者、语言、更新时间、display/reference/derivative/commercial 和 clone/install/execute/redistribute 当前授权 hint 筛选。
- FR-3：结果必须同时通过 ListingStatus.listed、ModerationStatus.approved 和 display entitlement 过滤，并固定一个不可变 ListingRevision；ListingDraft、review_pending、unlisted、withdrawn、suspended 或 deleted tombstone 不得进入公共结果。
- FR-4：排序至少支持相关性、最新发布和作者名；不得引入付费竞价、黑盒个性化或不可解释热度。
- FR-5：分页使用稳定 cursor；同一快照中不得因并发索引产生重复或跳项。
- FR-6：详情卡固定 listing_revision_id/content hash，显示作者、类型、摘要、target resource revision、offer revision 授权摘要和来源，不下载完整私有内容；canonical 详情必须再次读取权威 listing/moderation/entitlement 状态。
- FR-7：收藏与资源库分开；收藏仅保存 `ResourceLocator(executable=false)`，不创建 LicenseAcceptance/GrantSnapshot；加入可用资源库必须取得 install/reference 等规定 entitlement，保存固定 snapshot-backed ResourceRef、source_listing_revision_id 和可选 LicenseAcceptance。
- FR-8：搜索授权标签是当前 hint；任何 reference、clone、install、execute、redistribute、派生、发布和导出仍走权威 EntitlementDecision，任一 usage scope 不得推导 capability action。
- FR-9：unlisted、withdrawn、suspended、resource deleted 或失去 display 后，公共搜索必须在 60 秒内移除；unlisted canonical URL 仍可访问且新动作走当前 entitlement，withdrawn/deleted 返回 tombstone，suspended 隐藏详情并阻断新行为。
- FR-10：grant revoked 与 listing 展示独立；撤销复用 grant 只影响对应新动作，不能自动 unlist 可合法 display 的 ListingRevision，也不能让已收藏 locator 或历史库快照变成可执行授权。

## 7. 交互与展示

- 社区首页以紧凑类型标签和搜索栏进入真实结果，不先展示营销 hero。
- 桌面支持侧栏筛选，移动端使用筛选抽屉；已选条件始终可见和单项清除。
- 卡片只显示必要摘要，授权按 usage scopes 与 clone/install/execute/redistribute 分组展示当前 hint，不显示误导性的单一“免费”。
- 收藏页标记“仅书签”；个人资源库按 owner、类型、固定 revision、offer/acceptance、当前授权状态和 stale/升级提示筛选。
- unlisted 直链显示“未收录”；withdrawn/deleted 显示最小 tombstone；suspended 只显示通用不可用状态，不泄露内容或审核证据。

## 8. 数据、类型与公共接口

- `SearchDocument` 仅存 listing_id、listing_revision_id/content_hash、resource_type、fixed revision/package ref、public metadata、author、tags、language、usage/action hints、ListingStatus、ModerationStatus 和 index_version。
- `Favorite` 关联 user_id + ResourceLocator，locator 强制 executable=false；`LibraryEntry` 使用对应 COM 需求定义的 snapshot-backed ResourceRef、source_listing_revision_id、GrantSnapshot 与可选 LicenseAcceptance。
- 搜索结果返回 opaque cursor、index_version 和 canonical listing URL。
- 权威资源、listing、审核和授权仍在各聚合中，搜索索引不是事实源。

## 9. 状态机与业务规则

- 索引状态与 ListingRevision/ListingStatus/Moderation/Offer/Grant 状态分离，只作为派生投影；grant 变化只更新 action hints，不隐式改变 listing 状态。
- 收藏/取消收藏以 user + locator 幂等；同一固定资源和授权 revision 加入库不重复创建，条款 revision 变化需要新的 acceptance/snapshot。
- 新 ListingRevision 创建新的索引文档版本；只有审核通过并成为当前 listed revision 后可替换投影，不改用户已固定的库条目。
- 搜索相关性不得使用用户私有项目内容或无授权行为特征。

## 10. 失败、降级与恢复

- 搜索服务不可用时展示可重试状态和最近个人资源库，不返回越权缓存结果。
- 索引落后时 canonical 详情再次校验 ListingRevision、状态、审核和 display entitlement；unlisted 允许直链，withdrawn/deleted tombstone，suspended/越权内容 fail-closed。
- outbox 重放重建索引必须幂等；suspended/deleted/withdrawn/unlisted 移除事件优先于普通更新，并携带 listing_revision_id 防止旧事件复活内容。
- 资源库授权 hint 过期时标记“需验证”，运行前权威检查，禁止假定允许。

## 11. 安全、隐私、内容与授权

- 索引只包含 public metadata，不索引私有正文、prompt、未公开关系、同意凭证或 secret。
- 查询、筛选和高亮防注入；结果 URL 不泄露 Blob 私有地址。
- 用户搜索/收藏日志按隐私策略保留，不用于 V1 个性化推荐。

## 12. 观测与运营

- 在 100,000 个公开 listing 基准下，搜索 API p95 不高于 1.5 秒，索引普通更新 p95 在 60 秒内可见。
- 指标包括零结果率、点击率、筛选使用、索引延迟、陈旧 ListingRevision/越权结果拦截、locator 收藏、entitled 入库和搜索错误。
- 每次结果可记录 query hash、filter、index_version 和权限过滤数量，不记录不必要的原文查询。

## 13. 验收标准

- AC-1：Given 多个 ListingDraft/Revision 和混合状态，When 匿名搜索，Then 仅返回当前 listed + approved + 可匿名 display 的不可变 ListingRevision，旧/未审 revision 不泄露。
- AC-2：Given 用户按 execute=true 筛选，When 查看结果并实际运行，Then 每项只显示当前 action hint，运行仍执行权威 EntitlementDecision，commercial/reference 不能替代 execute。
- AC-3：Given listing 依次 unlisted、withdrawn、suspended、resource deleted，When 访问搜索与 canonical URL，Then 60 秒内均不再发现，直链分别为可读、tombstone、隐藏、tombstone。
- AC-4：Given 用户收藏并取得 entitlement 加入资源库，When 作者发布新 revision 或撤销 grant，Then 收藏仍为非可执行 locator，库条目固定原 ResourceRef/GrantSnapshot，新动作按当前决策且 listing 展示不被撤权隐式改变。
- AC-5：Given 100,000 条基准数据和并发 ListingRevision/状态事件，When 执行标准查询集，Then p95 延迟、稳定 cursor 无重复/漏项且旧事件不能复活隐藏内容。

## 14. 测试场景

- 正常：关键词、组合筛选、三种排序、分页、收藏、入库和详情跳转。
- 边界：空查询、零结果、同名作者/资源、多语言、最后一页和长标签。
- 失败：搜索宕机、索引延迟、重复事件、删除先于更新和详情权限变化。
- 权限：匿名/登录、owner-only 管理项、locator 不可执行、acceptance/entitlement 入库、grant revoked、各 listing 状态和缓存越权。
- 并发/恢复：并发上架/撤回、cursor 快照、索引重建、outbox 重放。

## 15. 交付与回退

- 先启用类型浏览和关键词，再开放组合筛选；复杂推荐始终不在 V1 开关中。
- 可回退到数据库驱动的有限“最新发布”页，但仍执行状态/权限过滤。
- 交付证据包括 100k 性能报告、不可变 ListingRevision 索引一致性、URL/状态矩阵、权限泄漏扫描、ResourceLocator/ResourceRef 隔离和资源库 E2E。

## 16. 已决策事项与开放问题

- 已决策：V1 只做基础检索与筛选，不做交易排名、复杂推荐或独立 Agent/Recipe 发现。
- 已决策：搜索中的授权只为 hint；收藏只存非可执行 ResourceLocator，资源库只存取得 entitlement 后的 snapshot-backed ResourceRef，新动作必须权威决策。
- 开放问题：无阻塞 V1 Community 的开放问题。
