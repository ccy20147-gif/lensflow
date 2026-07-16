# Artifact、Resource 与 lineage

## 1. 元数据

- ID：TF-WF-005
- 标题：Artifact、Resource 与 lineage
- 状态：verified
- 目标版本：Foundation
- 优先级：P0
- 全局位置：平台内核/资源库
- 直接依赖：TF-ARC-001、TF-WF-002、TF-OPS-003
- 责任域：数据平台/工作流平台
- 个人 DRI：main-agent

## 2. 背景与问题

运行产物、工作台编辑内容和可发布业务资源必须共享一条可解释版本链。若领域表、Blob、Artifact 和 Resource 各自维护 latest，运行、stale、授权与社区引用会出现无法裁决的双真相。

主表已冻结 ArtifactVersion 为不可变内容载体，Resource 为业务身份，ResourceRevision 通过 content_artifact_version_id 固定内容。

## 3. 目标与非目标

目标：

- 建立 ArtifactVersion、ResourceDraft、ResourceRevision 的单一版本链。
- 保存完整来源、生产者、输入 revision、参考素材和衍生关系。
- 支持工作台编辑、运行产物提升和社区发布。
- 允许同一 owner_scope 直接消费固定 ArtifactRef，同时为跨 owner 内容建立强制 ResourceRevision 提升边界。
- 对上游变化提供 stale 传播而不改写历史。

非目标：

- 不允许节点原地修改输入 ArtifactVersion。
- 不为 World、Character、Shot 或 Work 建立平行版本真相。
- 不在运行中动态读取 Resource latest。

## 4. 用户与权限

- 项目 owner 可以查看、编辑 Draft、冻结 Revision 和选择运行输入。
- 节点执行器只能创建新 ArtifactVersion 和声明的 Resource Draft 提案。
- Workbench 可以读取固定 Revision、保存 Draft 和提交 Revision。
- 同一 owner_scope 可直接读取固定 ArtifactRef；跨 owner 内容必须先由来源 owner 提升为 ResourceRevision，再以带 GrantSnapshot 且当前 entitlement 有效的 ResourceRef 使用。
- 不得向裸 ArtifactVersion 发放跨 owner grant，也不得用签名 URL、项目 ID 或 lineage 绕过提升边界。
- 系统维护者可修复索引，不能改写内容或 lineage。

## 5. 用户场景与主流程

1. 节点运行在同一 owner_scope 内消费固定 ArtifactRef，或消费固定 ResourceRef，并产生新 ArtifactVersion。
2. 用户选择“提升为资源”，系统以选定 ArtifactVersion 创建或更新 ResourceDraft；确认后通过 compare-and-swap 冻结 ResourceRevision。
3. 内容需要跨 owner 使用时，来源 owner 必须先完成上述提升，再对该固定 ResourceRevision 建立授权；消费方捕获 GrantSnapshot 并在当前动作重算 entitlement。
4. 工作台编辑时每次保存创建新内容 ArtifactVersion 并推进 draft_version，不能把跨 owner 裸 ArtifactRef 写入草稿后再补授权。
5. 下游运行固定该 revision；上游新 Revision 只使未固定依赖标记 stale。
6. 历史运行、授权和社区引用继续指向原 Revision。

## 6. 功能需求

- FR-1：ArtifactVersion 必须不可变并包含 schema、owner_scope、内容位置和 lineage。
- FR-2：Resource 必须提供稳定 resource_id 与 resource_type。
- FR-3：可变编辑只发生在 ResourceDraft，并记录 base_revision_id 与 draft_version。
- FR-4：ResourceRevision 必须固定 content_artifact_version_id 和 RevisionStatus。
- FR-5：运行、发布和导出只接受固定 Revision 或 ArtifactVersion；同一 owner_scope 可直接使用 ArtifactRef，跨 owner 或跨所有权边界只接受带 GrantSnapshot 的固定 ResourceRef。
- FR-6：节点执行不得覆盖输入，只能创建新 ArtifactVersion。
- FR-7：lineage 必须记录输入 refs、producer run/attempt、模型/Recipe/Agent revision 和参考顺序。
- FR-8：工作台保存使用 compare-and-swap，冲突保留双方内容。
- FR-9：上游变化必须计算 stale 影响，但不修改历史 Run、Revision 或 ArtifactVersion。
- FR-10：World、Character、ShotPlan、ShotSpec、CreativeWork、Agent 和 Recipe 通过专用 schema 扩展 ResourceRevision。
- FR-11：World 内嵌 OC 提升必须保存来源 world revision、局部 ID 和提升事件。
- FR-12：Blob 删除、归档或迁移不能使仍被有效 Revision 引用的内容不可解释。

## 7. 交互与展示

- Artifact Inspector 展示类型、版本、来源、生产节点、模型、成本和引用去向。
- Resource 页面区分 Draft、active Revision、retired Revision 和 stale 建议。
- “使用最新”只存在于编辑意图；运行前明确展示解析后的固定 revision。
- lineage 以可下钻图或列表展示，不把每个关系变成主工作流节点。
- 冲突与 stale 提供比较、固定旧版或生成新版动作。

## 8. 数据、类型与公共接口

严格使用主表第 8.1 节 ArtifactVersion、Resource、ResourceDraft、ResourceRevision、ResourceRef 和 ArtifactRef。

ArtifactVersion 的 content_uri 由 TF-OPS-003 管理；内容 hash 与 Blob hash 可分别记录。ArtifactRef 必须携带 schema_id/schema_version，并且只在其 owner_scope 内直接授权消费。

跨 owner 内容必须先建立 Resource/ResourceDraft，并冻结一个通过 content_artifact_version_id 指向该 ArtifactVersion 的 ResourceRevision；外部消费只传 ResourceRef。该 ResourceRef 的授权覆盖修订固定内容，但不把底层 ArtifactVersion 变成可单独授权对象。

LineageEdge 至少包含 source_ref、role、order、producer_ref、transformation 和 captured_policy_refs。

## 9. 状态机与业务规则

ResourceRevision 使用 RevisionStatus：draft、active、retired。实际可变内容仍只在 ResourceDraft。

ArtifactVersion 创建后没有可变内容状态；可增加可用性或隔离标记，但不能覆盖其内容。

同一 Resource 的 revision_number 单调递增。冻结请求基于 draft_version compare-and-swap。

## 10. 失败、降级与恢复

- Blob 上传未完成或未通过 TF-NFR-002 定义的 durability barrier 时不得提交/发布 ArtifactVersion。
- lineage 写入失败时整个产物提交回滚或通过同事务 outbox 恢复，不能留下无来源产物。
- Draft 冲突时返回 base、current 和 proposed refs。
- projection 或搜索索引失败不改变 ArtifactVersion 真相，可异步重建。
- Blob 暂不可用时保留元数据和安全错误，不删除 Revision。

## 11. 安全、隐私、内容与授权

- owner_scope 是 Artifact、Resource 和 Blob 访问的强制边界。
- 同 owner ArtifactRef 可直接消费；跨 owner ArtifactRef 一律拒绝，即使调用方持有 Blob URL、历史 GrantSnapshot 或能推测 artifact ID。
- 跨 owner ResourceRef 的 grant_snapshot_id 强制存在，并在每次新编译、运行、发布和商业导出重算当前 entitlement。
- EntitlementDecision 在编译、运行、发布和商业导出重新计算。
- lineage 展示按权限裁剪，不因引用泄露上游私有内容。
- 敏感内容隔离保留审计 hash 与引用关系。

## 12. 观测与运营

- 记录 ArtifactVersion 创建、Resource 提升、Revision 冻结、stale 传播和冲突。
- 监控无 lineage 产物、无内容 Blob、孤儿 Blob 和 hash 不一致。
- 统计每类 Resource 的 revision 数、引用数和 stale 修复时长。
- 提供按 run、revision、artifact 和 source 反向追踪能力。

## 13. 验收标准

- AC-1：节点重跑产生新的 ArtifactVersion，旧版本内容、hash 和引用保持不变。
- AC-2：工作台编辑并冻结后，ResourceRevision 的 content_artifact_version_id 可读且不可替换。
- AC-3：修改上游 Revision 后，相关未固定 Draft 标记 stale，历史 Run 与固定 Revision 不变化。
- AC-4：跨 owner 裸 ArtifactRef 或缺 GrantSnapshot 的 ResourceRef 在编译/读取时被拒绝；同内容提升为 ResourceRevision、建立授权并携带有效 ResourceRef 后可按 scope 使用。
- AC-5：从 World 提升 OC 后可追溯来源 world revision 和局部角色 ID，后续分叉不回写来源。
- AC-6：索引全部删除后可从 canonical 记录重建，不改变资源版本。

## 14. 测试场景

- 正常：运行产物、提升资源、工作台编辑、冻结和下游引用。
- 边界：大 Blob、JSON 内容、深 lineage、同资源多 Revision 和固定旧版。
- 失败：Blob、lineage、projection 和索引分别失败。
- 权限：同 owner ArtifactRef、跨 owner 裸 ArtifactRef、提升后 ResourceRef、撤权、敏感 lineage 和签名 URL 越权。
- 并发/恢复：两个 Draft 保存竞争、重复产物回调和索引重建。

## 15. 交付与回退

- Foundation 交付 canonical schema、事务写入、lineage、stale 和 contract tests。
- 领域模块只能通过本合同创建投影或专用 schema。
- 新索引或投影可独立回退，不删除 canonical 数据。
- 发布证据包括提升、编辑、stale、跨 owner 和重建演练。

## 16. 已决策事项与开放问题

已决策：ArtifactVersion 是内容与运行产物真相；ResourceRevision 固定其内容，不另建平行版本。同 owner 可直接使用 ArtifactRef，跨 owner 必须先提升为 ResourceRevision 并通过 ResourceRef 授权。

已决策：通用节点产物保存到个人创作资产的产品闭环、ResourceTypeDefinitionRevision eligibility、幂等和 Project link 由 TF-PLT-003 收紧；新建 promotion 必须从准确非 superseded OutputBinding/SelectionRecord 事务性生成可用 ResourceRevision，不默认批量提升。

开放问题：大规模 lineage 图的归档与查询优化可后续调整，但不能牺牲逐版本可追溯性。

### 实施与独立验收证据

- 2026-07-16：ArtifactVersion/ResourceDraft/ResourceRevision 的不可变版本链、Draft CAS、lineage、stale、跨 owner ResourceRef、World 内嵌 OC 提升、显式产物提升及 projection rebuild 已实现。
- Promotion 仅接受 owner 确认的、未 superseded OutputBinding/SelectionRecord；三种受支持的 SelectionRecord Artifact ref 键名都不能绕过 bootstrap 阻断。
- 独立验收：PostgreSQL 专项及 artifact/resource/runtime/authorization 回归 91 passed；`alembic upgrade head`、`ruff check src tests` 与 `mypy src` 通过。
