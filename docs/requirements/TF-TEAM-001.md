# 团队空间、角色权限与多人协作

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-TEAM-001 |
| 标题 | 团队空间、角色权限与多人协作 |
| 状态 | deferred |
| 版本 | V2 |
| 优先级 | P1 |
| 全局位置 | 产品外壳/全工作区 |
| 直接依赖 | TF-PLT-001、TF-PLT-002、TF-WF-004、TF-WF-005、TF-COM-006、TF-OPS-005 |
| 责任域 | 协作产品/平台 |
| 个人 DRI | 待指派 |

## 2. 背景与问题

专业项目由编剧、导演、分镜、媒体、审阅与运营共同完成。V1 的个人 owner/项目边界不足以表达团队成员、职责、交接、评论和并发编辑，但协作功能不能改变不可变 revision、Run 或授权真相。

## 3. 目标与非目标

- V2 提供组织/团队空间、项目角色、邀请、评论审阅、任务交接、在线状态和对象级冲突管理。
- 权限贯穿画布、工作台、资源、运行、社区和审计，保持 owner_scope 可迁移。
- 非目标：本项不进入 V1；V2 首版不承诺所有富文本的字符级 CRDT、外部企业 IAM 全套或制片排班/薪酬。

## 4. 用户与权限

- 基础团队角色：Owner、Admin、Editor、Reviewer、Operator、Viewer；项目可覆盖为更窄权限。
- Owner 管理组织与删除；Admin 管成员/项目；Editor 改草稿；Reviewer 评论/审批；Operator 运行；Viewer 只读。
- 高风险动作如发布、商业导出、成员移除、法律处置和密钥管理需独立 capability，不从角色名默认推导。

## 5. 用户场景与主流程

1. Owner 创建团队空间、邀请编剧与审阅者，并把现有项目转入团队 owner_scope。
2. Editor 修改 WorkflowDraft，Reviewer 在固定草稿/修订位置评论并请求更改。
3. 另一 Editor 打开同一工作台时看到 presence 与对象软锁；冲突时系统使用 CAS/三方 diff。
4. Reviewer 批准固定 revision，Operator 以该 revision 启动运行。
5. 成员被移除后新访问立即失效，历史编辑、审批与运行审计保留。

## 6. 功能需求

- FR-1：组织、团队、成员、邀请、项目归属和角色绑定必须是一等、可审计聚合。
- FR-2：授权模型支持团队默认角色 + 项目覆盖 + 资源/动作 capability，最终权限取最小允许交集。
- FR-3：现有个人项目转团队必须迁移 owner_scope、Blob、资源、workflow、运行和密钥引用，不改变 revision/Artifact ID。
- FR-4：评论必须锚定 resource/draft/revision、字段/画布元素或时间点，支持 thread、mention、resolve 和历史读取。
- FR-5：审阅请求固定目标 hash/revision、required reviewers、decision 和过期条件；后续内容变化使旧批准失效。
- FR-6：协作 presence 仅是临时信号；编辑事实由 ResourceDraft/WorkflowDraft 和 CAS 保存。
- FR-7：画布节点、工作台对象使用可续租软锁与三方 diff；锁过期不允许最后写覆盖。
- FR-8：运行、NodeRun、ArtifactVersion 与 ResourceRevision 不可由协作编辑原地修改；更改必须走新草稿/修订/运行。
- FR-9：成员撤权、角色变化和项目转移在 60 秒内使新 API/SSE/Blob 访问失效，并终止可终止会话。

## 7. 交互与展示

- 团队切换器、成员页和项目成员面板展示角色与具体 capability，不用模糊“协作者”。
- 画布/工作台显示 presence、正在编辑对象、评论和审阅状态；状态不遮挡核心创作内容。
- 冲突界面提供 base/current/local 三方 diff、保留/合并操作和责任人。
- 移动端支持查看、评论和审批；完整多人编辑以桌面为主。

## 8. 数据、类型与公共接口

- `Organization`、`TeamMembership`、`ProjectRoleBinding`、`CapabilityGrant` 与 `Invitation` 为独立聚合。
- `ReviewRequest` 引用 immutable target ref/hash、reviewers[]、HumanTaskStatus/decision refs，不改变 Resource RevisionStatus。
- `CommentThread` 保存 anchor type/ref、base content hash、messages[] 和 resolved metadata。
- presence/lock 使用短期 lease；持久编辑继续采用主表 ResourceDraft/WorkflowDraft contracts。

## 9. 状态机与业务规则

- 成员、邀请、评论、审阅、锁、Revision、Run 状态族分离。
- 角色变更以服务端版本 CAS 和审计为准；客户端缓存不得延长已撤权限。
- 审批只对固定 hash 有效；草稿改变自动标审批 stale，不修改原决定。
- 转移 owner_scope 必须原子或可恢复；禁止出现资源/Blob/密钥跨 owner 孤儿。

## 10. 失败、降级与恢复

- presence/实时通道不可用时退化为刷新与 CAS 编辑，不能放宽权限或覆盖冲突。
- 成员服务不可用时高风险写操作 fail-closed；已打开只读页面按短 TTL 策略降级。
- 项目转移失败从迁移 checkpoint 恢复，完成前冻结高风险写入。
- 锁持有者断线后 lease 到期释放；未保存本地内容通过三方 diff 恢复。

## 11. 安全、隐私、内容与授权

- 邀请 token 单次、短期、绑定目标身份；防域名混淆、转发和权限提升。
- 团队管理员不能自动获得成员个人空间、外部资源 grant 或 CredentialBinding 明文。
- 评论/mention/通知遵循项目可见性；移除成员后 Blob 签名 URL 与实时订阅失效。
- 角色、审阅、导出、发布、密钥和内容处置全部审计。

## 12. 观测与运营

- V2 基准支持同一项目 25 个并发在线成员、10 个并发编辑者；保存确认 p95 不高于 1 秒（不含冲突人工处理）。
- 指标包括邀请转化、权限拒绝、CAS 冲突、锁等待、评论响应、审阅周期、撤权传播和迁移失败。
- 审计可按团队/项目/资源/actor 重建成员、编辑、批准与运行时间线。

## 13. 验收标准

- AC-1：Given Editor/Reviewer/Operator，When 分别编辑、审批、运行，Then 仅允许对应 capability，越权 API 均拒绝。
- AC-2：Given 两位 Editor 同改对象，When 后提交基于旧 draft_version，Then 返回三方冲突且不覆盖首个提交。
- AC-3：Given Reviewer 批准 hash A，When 草稿变为 B，Then 批准标 stale，B 不能借 A 的决定发布/运行。
- AC-4：Given 成员被移除，When 60 秒后访问 API/SSE/Blob，Then 全部拒绝且历史审计仍归因于该成员。
- AC-5：Given 25 人在线且实时通道中断，When 继续操作，Then 系统退化为 CAS/刷新且 revision/Run 真相不变。

## 14. 测试场景

- 正常：建团队、邀请、角色、项目转移、评论、审阅、运行和成员移除。
- 边界：最后一个 Owner、邀请过期、25 人在线、10 人编辑、深评论 thread。
- 失败：实时通道中断、成员服务故障、迁移中断、锁过期和通知失败。
- 权限：角色矩阵、项目覆盖、个人空间、凭证、发布/导出和管理员边界。
- 并发/恢复：双编辑 CAS、双审批、角色变更竞态、迁移 checkpoint 和事件重放。

## 15. 交付与回退

- 本项保持 deferred；V2 开始前需完成权限 ADR、owner_scope 迁移演练和全产品权限清单。
- 团队能力按新团队空间启用，关闭后不把团队资源错误转回个人 owner。
- 交付证据包括角色矩阵、25/10 并发、项目迁移、撤权传播、冲突和审计 E2E。

## 16. 已决策事项与开放问题

- 已决策：V1 不含团队实时协作；V2 协作不得原地修改不可变 revision、Artifact 或 Run。
- 已决策：V2 首版以 presence、评论审阅、对象软锁、CAS/三方 diff 为核心，不承诺全域字符级 CRDT。
- 开放问题：企业 SSO/SCIM 与更细组织层级需 V2 立项另行裁决，不影响本范围封套。
