# 用户账户与项目所有权

## 1. 元数据

- ID：TF-PLT-001
- 标题：用户账户与项目所有权
- 状态：in_delivery
- 目标版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：产品外壳/平台内核
- 直接依赖：TF-ARC-001
- 责任域：平台产品/身份后端
- 个人 DRI：main-agent

## 2. 背景与问题

V0 需要尽快验证创作价值，但项目、Blob、运行和密钥从第一天就必须有 owner/tenant 边界，否则 V1 开放注册时会发生破坏性迁移和数据越权。

V1 Core 只提供个人账户与私有项目，不提前实现团队空间或实时协作。

## 3. 目标与非目标

目标：

- V0 提供安全的单部署 bootstrap owner。
- V1 Core 提供注册、登录、会话、恢复和个人项目隔离。
- 所有业务聚合使用稳定 owner_scope。
- 支持账户停用、删除请求和会话撤销。

非目标：

- V1 不提供组织、团队角色或多人实时协作。
- 不把 API key 当作最终用户账户。
- 不允许匿名访问私有项目和媒体。

## 4. 用户与权限

- bootstrap owner 是 V0 唯一交互用户，但仍通过正式身份与 owner_scope 访问。
- V1 注册用户拥有个人 owner_scope 和其创建的项目。
- 平台管理员只能执行受审计的支持和安全操作，默认不能浏览内容。
- Worker、Provider callback 和系统任务使用服务身份，不继承管理员权限。
- 删除、导出和安全处置需要重新认证或等价高强度验证。

## 5. 用户场景与主流程

1. V0 部署通过一次性流程创建 bootstrap owner 并强制设置凭证。
2. 用户登录后创建项目，所有子资源继承 owner_scope。
3. V1 用户完成注册、验证、登录和会话建立。
4. 用户在多个项目间切换，只看到自己可访问的数据。
5. 用户撤销会话、修改凭证或提交账户删除请求。
6. 安全处置可以暂停账户而不破坏历史运行证据。

## 6. 功能需求

- FR-1：V0 必须通过幂等 bootstrap 流程创建唯一 owner，禁止默认共享密码。
- FR-2：所有 Project、Resource、Artifact、Run、Blob 和 CredentialBinding 必须关联 owner_scope。
- FR-3：V1 Core 必须支持注册、登录、登出、凭证恢复和会话撤销。
- FR-4：认证结果必须包含 actor_id、owner_scope、会话强度和审计上下文。
- FR-5：项目查询与写入默认按 owner_scope 过滤，并在后端再次校验。
- FR-6：账户暂停后阻止新运行、发布和密钥使用，但保留受限历史证据。
- FR-7：删除请求必须进入受审计流程，遵守引用保护和保留策略。
- FR-8：服务身份必须使用单独 credential 和 scope，不能伪装用户。
- FR-9：认证失败、会话过期和权限不足使用不同安全错误码。
- FR-10：V0 数据升级到 V1 后归属 bootstrap owner 对应账户，不重写业务 Revision。

### 逐版本切片矩阵

| 能力 | V0 | V1 Core |
| --- | --- | --- |
| 身份入口 | 单一 bootstrap owner | 公共注册、登录、恢复 |
| owner_scope | 全量业务对象强制 | 保持兼容并支持多用户 |
| 项目权限 | owner-only | 个人 owner-only |
| 会话 | 单用户安全会话与撤销 | 多设备会话列表与撤销 |
| 数据升级 | 预留稳定用户身份 | 无破坏映射 V0 owner |

## 7. 交互与展示

- V0 首次启动显示 bootstrap 设置页，完成后不可再次创建第二 owner。
- V1 提供注册、登录、找回凭证、账户设置和会话管理。
- 项目列表不展示无权项目的存在性。
- 被暂停账户显示安全说明和支持 correlation ID，不泄露内部策略。
- 删除流程明确展示可立即删除、需保留和被引用保护的数据类别。

## 8. 数据、类型与公共接口

核心实体包括 UserAccount、OwnerScope、Session、ServiceIdentity 和 AccountDeletionRequest。

业务对象使用 owner_scope，不以邮箱或显示名作为外键。ResourceRef 与 ArtifactRef 的访问通过其所属 owner_scope 和授权决策完成。

凭证 hash、验证 token 和会话 secret 与业务数据分离存储，日志只记录不可逆标识。

## 9. 状态机与业务规则

AccountStatus 至少包含 pending_verification、active、suspended、deletion_pending 和 deleted_tombstone。

SessionStatus 包含 active、expired 和 revoked。暂停账户会使现有会话失效。

bootstrap 操作使用数据库唯一约束和幂等键；并发请求最多成功一个。

## 10. 失败、降级与恢复

- 邮件或外部验证服务不可用时，不创建已验证账户。
- 会话存储短暂不可用时默认拒绝敏感写操作。
- bootstrap 中断后可用同一恢复 token 继续，不产生第二 owner。
- 恢复凭证后撤销旧会话和恢复 token。
- V1 升级失败时保留 V0 owner 登录与数据只读能力。

## 11. 安全、隐私、内容与授权

- 密码使用当前安全散列参数；登录、恢复和验证接口限速。
- Session cookie 使用 Secure、HttpOnly 和合适 SameSite 策略。
- 高风险操作要求近期认证。
- 审计日志不记录密码、token、完整邮箱或私有内容。
- 账户删除遵守 TF-NFR-002，不能伪造删除被授权引用的历史证据。

## 12. 观测与运营

- 记录注册成功率、登录失败率、恢复请求、会话撤销和权限拒绝。
- 对暴力登录、异常 owner_scope 探测和重复 bootstrap 告警。
- 审计管理员支持操作和服务身份调用。
- V0 到 V1 升级生成 owner 映射报告。

## 13. 验收标准

- AC-1：并发执行十次 bootstrap，数据库中只存在一个 owner 且无默认凭证。
- AC-2：V0 创建的项目、Blob、Run 和密钥均可追溯到同一稳定 owner_scope。
- AC-3：V1 两个账户互相无法查询、修改或推断对方私有项目。
- AC-4：撤销会话后，原 token 在下一次受保护请求时被拒绝。
- AC-5：V0 升级 V1 后所有 Revision ID 与 content hash 不变，归属映射完整。

## 14. 测试场景

- 正常：注册、验证、登录、创建项目、登出和恢复凭证。
- 边界：重复邮箱、过期恢复 token、多设备会话和空项目账户。
- 失败：验证服务、会话存储和数据库短暂故障。
- 权限：跨 owner ID 枚举、伪造服务身份和被暂停账户写入均被拒绝。
- 并发/恢复：bootstrap 竞争、重复注册和删除流程重试保持幂等。

## 15. 交付与回退

- V0 先交付 bootstrap owner、owner_scope 中间件和私有访问。
- V1 通过功能开关开放注册、恢复和多用户查询。
- 数据迁移先生成审计报告，再切换公共入口。
- 回退 V1 入口时保留 bootstrap owner 的安全访问，不回滚业务 Revision。

## 16. 已决策事项与开放问题

已决策：V0 不是无身份模式；V1 Core 只做个人账户和私有项目。

开放问题：具体登录方式可选择邮箱密码或受支持身份提供方，但必须满足本需求的恢复、会话和 owner_scope 合同。
