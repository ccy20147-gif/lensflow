# Tool 注册、凭证绑定与 Agent 执行策略

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-AGT-005 |
| 标题 | Tool 注册、凭证绑定与 Agent 执行策略 |
| 状态 | in_delivery |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | Agent Studio/平台内核 |
| 直接依赖 | TF-PLT-001、TF-OPS-001、TF-OPS-005、TF-SEC-001 |
| 责任域 | Agent 安全/平台 |
| 个人 DRI | main-agent |

## 2. 背景与问题

自定义 Agent 需要工具，但任意连接器会形成数据外传、凭证泄漏和越权执行通道。工具能力必须先注册、审批、限定网络与数据范围，并在每次调用时重新鉴权。

## 3. 目标与非目标

- 建立平台托管或审批 ToolDefinition、不可变修订、凭证绑定和最小权限执行策略。
- 对输入披露、网络出口、输出净化、撤权和审计形成闭环。
- 非目标：V1 不允许用户上传代码、Shell、任意 URL 工具，也不允许工具代理调用 Agent/Workflow/Recipe。

## 4. 用户与权限

- 安全/平台管理员注册、审批、暂停或退役工具修订。
- 凭证所有者创建 CredentialBinding，授予特定 owner_scope、tool revision 和 scopes 使用权。
- Agent 作者只能选择已批准工具及请求 scopes，不能读取凭证内容。
- 运行者权限、Agent 请求、凭证 scopes 和组织策略取交集。

## 5. 用户场景与主流程

1. 管理员注册工具 endpoint、I/O schema、网络策略、数据分类和风险等级。
2. 审批后生成不可变 ToolRevision；用户创建加密 CredentialBinding。
3. Agent 作者选择工具与最小 scopes，Studio 展示将披露的数据字段。
4. 调用时 broker 复核当前授权、注入短期凭证、限制 egress、执行并净化输出。
5. 系统记录 ToolInvocation；撤销凭证后新调用立即失败，历史记录保持脱敏可审计。

## 6. 功能需求

- FR-1：ToolRevision 至少定义 typed I/O、操作列表、风险级别、允许域名/IP、超时、大小限制、数据分类和净化策略。
- FR-2：仅 `approved` 且未暂停的 revision 可绑定到 AgentRevision；审批状态不得复用 RevisionStatus。
- FR-3：CredentialBinding 必须加密存储、不可导出，且与主体、owner_scope、tool revision、scopes 和到期时间绑定。
- FR-4：每次调用以当前 EntitlementDecision 计算主体、Agent、工具、凭证和数据资源的权限交集。
- FR-5：执行 broker 必须阻断任意重定向、私网探测、未批准域、超限请求、危险 MIME 和可执行输出。
- FR-6：工具输入按字段最小披露，ToolInvocation 记录披露清单 hash 而非默认保存敏感明文。
- FR-7：输出必须经过 schema 校验、内容安全扫描、prompt-injection 标记和 secret 净化后才能交给 Agent。
- FR-8：撤权阻断新调用；可取消的在途调用应取消，不可取消结果进入隔离且不得自动下游消费。
- FR-9：调用受每步与每运行次数、并发、时长、费用和重试上限约束。

## 7. 交互与展示

- 工具目录展示提供者、操作、风险、数据去向、所需 scopes、状态和版本。
- Agent Studio 在绑定前展示字段级披露与凭证来源，不显示 secret。
- 设置页允许用户测试、轮换、暂停和撤销 CredentialBinding。
- 运行 trace 展示工具、操作、耗时、成本、披露类别、净化/阻断结论和 correlation_id。

## 8. 数据、类型与公共接口

- `ToolDefinition/ToolRevision` 使用 Resource/ResourceRevision 语义；内容含 `operations[]`、schema refs、`egress_policy_ref`、`sanitizer_policy_ref`。
- `CredentialBinding` 为独立安全聚合，仅返回不透明 ID、范围、状态与元数据。
- `ToolInvocationRecord`：tool_revision_id、operation_id、agent/node/attempt、credential_binding_id、input_fingerprint、disclosure_manifest_ref、result_ref、decision_refs、usage。
- Provider 模型密钥仍由 TF-OPS-001 管理，不复制到本工具合同。

## 9. 状态机与业务规则

- 工具审批状态与 RevisionStatus 分离；只有 active revision + approved approval 才可新绑定。
- 凭证状态支持 active/expired/revoked/disabled；状态变化以数据库为真相。
- `invocation_id + attempt` 保证幂等；非幂等外部操作必须使用 provider 幂等键或禁止自动重试。
- 网络策略、权限或凭证变化不修改历史 ToolInvocation，只影响后续决策。

## 10. 失败、降级与恢复

- 凭证失效、scope 不足和策略阻断返回安全错误码，不暴露 endpoint 内部或 secret。
- 超时/限流仅按工具声明有限重试；非幂等操作默认不自动重试。
- broker 重启后依据 invocation 状态和外部幂等键恢复，未知结果进入人工核对而非重复执行。
- 净化失败或疑似数据外传时隔离输出、停止相关步骤并产生安全告警。

## 11. 安全、隐私、内容与授权

- secret 使用专用密钥系统加密，传输时短期解封，禁止进入 Agent prompt、图、包、日志或导出。
- 防 SSRF、DNS rebinding、重定向逃逸和私网访问；出口默认拒绝。
- 高敏字段默认不披露，必须由工具声明、Agent 请求、用户授权和策略共同允许。
- 安全管理员可紧急暂停工具并保留审计，法律/安全处置优先于历史可用性。

## 12. 观测与运营

- 指标包括调用量、成功率、延迟、重试、scope 拒绝、egress 阻断、净化命中、撤权后调用和费用。
- 审计记录审批者、策略版本、凭证元数据、权限决策、披露 manifest、响应 hash 和告警。
- 支持人员只能查看脱敏诊断；访问隔离内容需审批并被二次审计。

## 13. 验收标准

- AC-1：Given 已批准工具与有效绑定，When Agent 调用允许操作，Then 返回 schema 合法输出且 trace 含最小披露与策略版本。
- AC-2：Given Agent 请求超出凭证 scope，When 调用，Then 在外部请求前阻断且不泄露凭证。
- AC-3：Given 工具尝试重定向到私网或未批准域，When broker 执行，Then 阻断、告警并隔离任何响应。
- AC-4：Given 凭证在运行中撤销，When 新调用或晚到结果到达，Then 新调用失败，晚到结果不被下游消费。
- AC-5：Given 非幂等调用后 broker 重启，When 状态不明，Then 不自动重复外部副作用并进入可审计核对态。

## 14. 测试场景

- 正常：注册审批、凭证绑定、scope 调用、轮换、撤销和脱敏 trace。
- 边界：最大 payload、最短超时、到期临界点、多个凭证选择、无敏感输入。
- 失败：限流、超时、坏 schema、恶意 MIME、prompt injection、净化服务不可用。
- 权限：跨租户绑定、作者读取 secret、超 scope、退役工具、撤权后重放。
- 并发/恢复：并发额度、重复回调、broker 重启、非幂等未知结果、撤权竞态。

## 15. 交付与回退

- 初始 allowlist 只开放平台托管低风险工具；高风险工具按审批与功能开关逐个启用。
- 紧急回退可全局暂停 tool invocation，但保留 Agent 编辑、历史 trace 和凭证管理。
- 发布证据包括 SSRF/外传红队、凭证泄漏扫描、撤权竞态、幂等恢复和审计抽查。

## 16. 已决策事项与开放问题

- 已决策：V1 仅允许平台托管或审批工具，最小权限且出口默认拒绝。
- 已决策：工具不可成为嵌套 Agent、Workflow、Subworkflow 或 Recipe 的代理通道。
- 开放问题：无阻塞 V1 Core 的开放问题。
