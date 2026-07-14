# 授权、署名、使用凭证与派生谱系

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-COM-004 |
| 标题 | 授权、署名、使用凭证与派生谱系 |
| 状态 | defined |
| 版本 | V1 Community |
| 优先级 | P0 |
| 全局位置 | 社区/平台内核 |
| 直接依赖 | TF-PLT-001、TF-WF-005、TF-SEC-001、TF-NFR-002 |
| 责任域 | 法务/社区平台 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

平台必须同时回答“权利人向谁提供了哪个版本的条款”“用户是否接受了该版本”“过去为什么允许这次动作”和“现在能否再次执行”。若把公开、收藏、入库、`GrantSnapshot` 或旧条款当永久权限，撤权会失效；若撤权后删除历史证据，又无法审计合法创作。

## 3. 目标与非目标

- 以不可变 `LicenseOfferRevision` 和 `LicenseAcceptance` 表达面向公众的版本化授权，以 direct Grant 表达面向明确主体的授权。
- 分离 `display/reference/derivative/commercial` usage scopes 与 `clone/install/execute/redistribute` capability actions，生成当前决策和历史快照。
- 让署名、派生 lineage、撤权和法律限制在收藏、入库、克隆、编译、运行、发布与导出时可执行。
- 非目标：不提供法律意见、自动判断全部权属、交易结算或追溯修改历史产物。

## 4. 用户与权限

- 资源权利人创建、修订、停用 `LicenseOfferRevision`，或向明确 grantee 创建、更新、到期或撤销 direct Grant。
- 使用者只能在主体、固定 ResourceRevision、明确 action、期限和条款覆盖范围内操作；需接受的 offer 必须由本人生成 `LicenseAcceptance`。
- 法务/安全角色可依法限制访问，但必须隔离证据和记录依据。

## 5. 用户场景与主流程

1. 作者为固定 ResourceRevision 创建 `LicenseOfferRevision`，分别配置 usage scopes、capability actions、受众、接受要求与署名条款；面向特定主体时也可创建 direct Grant。
2. 用户查看条款；需要接受时，服务端对准确的 offer revision 和 `terms_hash` 创建 `LicenseAcceptance`。
3. 用户发起 reference、clone、install、execute 或 redistribute 等具体动作，授权服务计算当前 `EntitlementDecision`。
4. 允许后为该动作捕获 `GrantSnapshot`，并将 snapshot-backed `ResourceRef` 或证据写入资源库、克隆、运行及作品 lineage。
5. 后续新动作再次计算当前决策，不复用旧快照作为通行证；作者撤权后新动作被阻断，已合法完成运行默认保留证据。

## 6. 功能需求

- FR-1：`LicenseOfferRevision` 必须固定 licensor、resource_revision_id、eligible_principal、usage_scopes、capability_actions、conditions、attribution、territory、effective_period、acceptance_required 和 `terms_hash`；条款或动作变化必须产生新 revision，不能原地修改已发布版本。
- FR-2：面向 public/authenticated 主体的复用必须来自版本化 offer；direct Grant 只面向明确 grantee。匿名主体最多使用无需接受的 display offer。
- FR-3：`display/reference/derivative/commercial` 与 `clone/install/execute/redistribute` 均按 action 独立计算；任一 scope/action 不得推出另一个，commercial 也不得隐含 clone、install、execute 或 redistribute。
- FR-4：当 offer 要求接受时，受控动作前必须创建服务端签发的 `LicenseAcceptance`，固定 accepting_subject、offer_revision_id、accepted_at 和 `terms_hash`；新条款 revision 必须重新接受。
- FR-5：收藏只保存 `executable=false` 的 `ResourceLocator`，不复制内容、不创建 `GrantSnapshot` 且不能用于编译/运行；“加入可用资源库”必须先取得对应 action 的当前 entitlement，并创建带 `grant_snapshot_id` 的固定 `ResourceRef`。
- FR-6：每次 reference、clone、install、execute、redistribute、派生、发布和商业导出必须生成 `EntitlementDecision`，记录 subject、action、resource_revision_id、current_grant_status、decision、reason 和 evaluated_at。
- FR-7：每个获准受控动作必须捕获 `GrantSnapshot`，固定 direct grant 或 accepted offer 的实际 revision、动作、条款和 acceptance；snapshot 只证明该历史动作，不授权新的入库、克隆、运行、派生、发布或导出。
- FR-8：工作流 clone 必须有 clone；包内嵌依赖必须有 redistribute；Agent/Recipe 安装与调用分别必须有 install 与 execute；上述动作不得由 reference、derivative 或 commercial 自动推导。
- FR-9：attribution manifest 和派生资源必须保存直接父 `ResourceRef`、`GrantSnapshot`、变更/贡献摘要与可遍历 lineage，并支持机器可读和用户可见渲染。
- FR-10：撤权/到期阻断新受控动作但不改写历史 Run、ArtifactVersion、Revision、Acceptance 或合法快照；法律/安全处置可隔离历史访问，授权决策服务不可用时新动作 fail-closed。

## 7. 交互与展示

- 授权编辑器将四类 usage scope 与四类 capability action 分组展示，并显示受众、是否需要接受、期限、地域/用途限制和署名预览。
- 资源详情明确显示 offer revision、条款 hash、“现在可做什么”以及“接受并加入资源库/克隆/安装”等准确动作；历史运行详情显示“当时依据什么”，两者文案区分。
- 撤权前展示对新动作、既有草稿、已完成运行和 listing 的不同影响。
- 派生作品展示可展开来源树，默认提供简洁署名并可查看条款快照。

## 8. 数据、类型与公共接口

- 严格使用主表 `LicenseOfferRevision`、`LicenseAcceptance`、`GrantSnapshot`、`EntitlementDecision`、`ResourceLocator` 与 `ResourceRef`；不得建立兼容性不足的平行授权类型。
- `LicenseOfferRevision` 是不可变条款版本；`LicenseAcceptance` 固定主体和准确条款版本；direct `Grant` 聚合固定 revision、明确主体、usage scopes、capability actions、terms_hash 与 GrantStatus。
- `GrantSnapshot.source_kind` 只能为 `direct_grant` 或 `accepted_offer`，并固定 `grant_or_offer_revision_id`、resource revision、实际 scopes/actions、terms_hash、captured_at 与可选 acceptance_id。
- `ResourceLocator` 只含定位信息并强制 `executable=false`；可执行或可引用的跨 owner 库条目必须保存 snapshot-backed `ResourceRef`。
- `AttributionManifestEntry` 含 source ResourceRef、creator、required_text、terms_hash、grant_snapshot_id 和 lineage role。
- 当前授权 hint 可供 UI 展示，但接受、入库、克隆、编译、运行、发布和导出必须调用权威决策。

## 9. 状态机与业务规则

- Offer revision 发布后不可变；条款更新创建新 revision，旧 `LicenseAcceptance` 只对应旧 revision。GrantStatus = draft -> active -> expired/revoked/legally_restricted。
- 撤权与到期以权威时间和事件顺序生效；重复接受/决策/撤权命令以 subject + action + revision + idempotency key 幂等。
- 权限取主体、资源所有权、accepted offer/direct grant、准确 action、用途、内容安全与法律限制的交集。
- GrantStatus 与 ListingStatus、ModerationStatus、RevisionStatus 独立；grant revoked 与 listing 展示独立，不自动改变展示状态，listing 展示也不产生复用权。
- 授权决策必须读取权威 listing/resource 状态：unlisted 只退出发现且 canonical URL 按 display entitlement 可访问；withdrawn 返回 tombstone 并拒绝新 acceptance/install/clone；suspended 隐藏内容并拒绝所有新行为；resource deleted 返回 tombstone。上述状态不删除历史 Acceptance/Snapshot。
- 派生链追加不可变边，不删除父边；纠错产生新证据记录和审计说明。

## 10. 失败、降级与恢复

- 授权或 acceptance 服务不可用时新受控动作 fail-closed；收藏仍可仅保存可见 listing 的非可执行 locator，历史只读按缓存策略和法律状态安全降级。
- 失效事件投递失败由 outbox 重放；关键服务在执行前仍做权威查询。
- 署名渲染失败阻断发布/商业导出，不允许省略后继续。
- 并发 offer/grant 更新以版本 CAS 裁决，决策记录固定实际读取的 offer/grant revision 和 acceptance。

## 11. 安全、隐私、内容与授权

- offer/grant 管理、条款接受、决策、快照、撤权、法律限制和隔离访问全部审计。
- 不向社区暴露身份证明、同意凭证正文或法律文件，只显示必要授权摘要。
- 防止客户端伪造 LicenseAcceptance、GrantSnapshot、terms hash、subject 或 owner；所有证据由服务端签发并校验。

## 12. 观测与运营

- 指标包括各 usage/action allow/deny、offer 接受转化、原因分布、撤权传播延迟、过期缓存命中、署名阻断和 lineage 断链。
- 审计记录 subject、action、resource revision、offer/grant revision、acceptance、decision、snapshot、运行/作品和 correlation_id。
- 定期扫描已 listed/可运行对象的当前授权漂移，结果仅触发处置流程，不原地改历史。

## 13. 验收标准

- AC-1：Given 公开 offer 只提供 display，When 用户查看、收藏、加入可用资源库和引用，Then 查看/收藏允许且收藏只保存 ResourceLocator，入库/引用因无对应 entitlement 被拒绝。
- AC-2：Given install offer 要求接受，When 用户接受准确 revision 并加入资源库，Then 产生 LicenseAcceptance、EntitlementDecision、GrantSnapshot 和固定 snapshot-backed ResourceRef；条款升级后再次安装必须重新接受。
- AC-3：Given 工作流有 clone 但依赖无 redistribute，When 用户克隆，Then 包内嵌在发布/克隆解析阶段被阻断；reference/derivative/commercial 均不能补足缺失动作。
- AC-4：Given 历史 execute 已捕获 GrantSnapshot，When offer/grant 后续撤销且用户重跑，Then 新 execute 被拒绝，旧合法运行、acceptance 与 snapshot 仍可审计。
- AC-5：Given 署名缺失或法律限制生效，When 发布/导出或访问历史内容，Then 前者 fail-closed，后者按政策隔离且授权审计角色仍可读取不可变证据。

## 14. 测试场景

- 正常：offer 发布/接受、direct Grant、八类 scope/action、收藏、入库、引用、克隆、安装、执行、再分发、署名和撤权。
- 边界：无需接受的匿名 display、到期临界、多个 offer/grant、owner 自用、条款版本更新、unlisted/withdrawn/suspended/deleted、深派生链。
- 失败：acceptance/决策服务不可用、缓存过期、署名渲染失败、事件延迟和非法 acceptance/snapshot。
- 权限：跨 owner、伪造主体、动作 scope 混淆、管理员撤权、法律隔离和审计访问。
- 并发/恢复：offer/grant CAS、重复接受、撤权与运行竞态、outbox 重放和重复决策请求。

## 15. 交付与回退

- 先在社区对象上强制版本化 offer/acceptance 和八类 scope/action，再开放复用；不得以旧单一 public flag 或模糊 commercial flag 作为回退路径。
- 决策服务可进入全新动作 fail-closed 模式；历史证据库保持只读可用。
- 交付证据包括 offer revision/acceptance contract tests、完整权限矩阵、ResourceLocator 与 snapshot-backed ResourceRef 隔离、撤权竞态、署名、lineage、法律隔离和审计演练。

## 16. 已决策事项与开放问题

- 已决策：公众授权使用不可变 LicenseOfferRevision，需接受条款时生成 LicenseAcceptance；direct Grant 只面向明确 grantee。
- 已决策：usage scopes 与 clone/install/execute/redistribute 完全分离；GrantSnapshot 与当前 EntitlementDecision 完全分离。
- 已决策：撤权不追溯破坏合法历史，但法律/安全处置可以隔离访问并保留证据。
- 开放问题：无阻塞 V1 Community 的开放问题。
