# 审计、可观测性与安全错误

## 1. 元数据

- ID：TF-OPS-005
- 标题：审计、可观测性与安全错误
- 状态：defined
- 目标版本：Foundation
- 优先级：P0
- 全局位置：运营/平台内核
- 直接依赖：TF-ARC-001、TF-GOV-001
- 责任域：SRE/安全工程
- 个人 DRI：待指派

## 2. 背景与问题

开放创作平台跨 API、worker、Provider、Blob、Agent 和社区运行。没有统一 correlation、指标、trace、审计和安全错误，用户只能看到“失败”，运维也无法定位 attempt、Provider request 或权限裁决。

同时，过度记录 prompt、素材、token 和密钥会制造新的安全风险。

## 3. 目标与非目标

目标：

- Foundation 建立结构化日志、指标、trace、审计和安全错误合同。
- 贯通用户请求、run、node、attempt、Provider 和 Blob 操作。
- 对外给出可行动但不泄密的错误，对内保留受控诊断。
- 支持故障演练、告警、发布监控和质量评测。

非目标：

- 不把可观测存储作为业务真相。
- 不记录所有 prompt、响应或媒体正文。
- 不允许管理员无审计浏览私有内容。

## 4. 用户与权限

- 用户可查看与其项目相关的安全错误、correlation ID 和公开运行摘要。
- SRE 可查看基础设施日志、指标和净化 trace。
- 安全人员可按事件授权访问更敏感审计证据。
- 开发者默认只能访问非生产或已脱敏数据。
- 审计写入者不能修改或删除既有记录。

## 5. 用户场景与主流程

1. API 请求建立 correlation ID、actor 和 owner_scope。
2. 调用跨越编译、运行、worker、Provider 和 Blob 时传播 trace context。
3. 各模块记录结构化事件和指标，不写敏感正文。
4. 失败被映射为注册安全错误类别和用户消息。
5. 支持人员通过 correlation ID 定位 run、attempt 和 provider request。
6. 发布后 SLO/错误告警触发故障流程和复盘。

## 6. 功能需求

- FR-1：所有 API、任务和回调必须携带或建立 correlation ID。
- FR-2：日志必须结构化并包含 service、build、environment、actor class、owner scope hash 和关联业务 ID。
- FR-3：错误必须映射为稳定 error_code、retryability、safe_message 和 correlation ID。
- FR-4：公开错误不得包含堆栈、secret、内部 URL、SQL 或原始 Provider 响应。
- FR-5：内部诊断必须关联 run、node、attempt、task binding 和 provider request ID。
- FR-6：审计记录必须覆盖身份、权限、Revision 激活、运行命令、密钥、授权、导出和治理操作。
- FR-7：敏感字段必须在日志、trace 和事件出口统一清理。
- FR-8：指标至少覆盖可用率、延迟、错误、队列、存储、Provider 和成本链完整性。
- FR-9：告警必须有 owner、严重度、runbook 和抑制规则。
- FR-10：审计与诊断保留策略必须版本化并支持法律隔离。
- FR-11：生产调试不得通过临时打印完整请求绕过清理。
- FR-12：发布候选必须通过故障定位和敏感信息清理演练。

## 7. 交互与展示

- 用户错误页展示简洁原因、可重试动作和 correlation ID。
- 运行详情区分业务拒绝、Provider 失败、系统失败和用户取消。
- 运营控制台可按 correlation、run、attempt、provider request 和 error code 查询。
- 审计页面展示 actor、动作、目标、裁决和时间，不默认展示内容正文。
- 告警面板链接到 runbook 与相关发布版本。

## 8. 数据、类型与公共接口

核心合同包括 SafeError、AuditRecord、TraceContext、MetricDefinition 和 AlertDefinition。

SafeError 包含 error_code、category、retryable、safe_message、correlation_id 和 remediation_hint。

AuditRecord 包含 actor、action、target_ref、owner_scope、decision、policy_revision、timestamp 和 evidence_refs。

业务状态仍由各聚合表维护；日志、trace 和指标只引用稳定 ID。

## 9. 状态机与业务规则

AuditRecord 追加不可变，更正以新记录引用原记录。

IncidentStatus 可为 detected、triaged、mitigating、resolved、reviewed；不复用 RunStatus。

同一 error_code 的公开含义保持兼容。需要改变语义时创建新 code 或版本。

## 10. 失败、降级与恢复

- 日志后端不可用时使用有界缓冲，不能阻塞核心安全拒绝。
- 审计写入属于关键操作事务或 outbox；失败时敏感命令不得静默成功。
- trace 丢失时仍保留 correlation ID 和业务审计。
- 指标系统故障不改变业务状态，但 readiness 与告警显示监控降级。
- 缓冲恢复时去重并保持时间与来源。

## 11. 安全、隐私、内容与授权

- 建立字段分类与默认拒绝日志策略。
- 密钥、token、签名 URL、密码、完整 prompt、原始媒体和未授权个人信息不得进入普通日志。
- 诊断访问按角色、工单和时间限制，并完整审计。
- 审计导出按 owner_scope 和法律权限裁剪。
- 日志注入、富文本和 Provider 错误先净化再展示。

## 12. 观测与运营

- 为 API、worker、数据库、队列、Blob、Provider 和事件链建立基础 dashboard。
- 关键告警覆盖高错误率、队列积压、stuck run、outbox 延迟、secret 检测和审计缺口。
- 每个 P0 服务有 runbook、值班 owner 和回退指引。
- 发布前后比较错误、延迟和成本指标。
- 定期执行 trace 抽样、日志清理和审计完整性检查。

## 13. 验收标准

- AC-1：从用户 correlation ID 可在十分钟内定位到 API、run、attempt 和 Provider request。
- AC-2：向请求、Provider 错误和文件名注入测试 secret，日志、trace、事件和 UI 均不出现原值。
- AC-3：审计后端故障时，密钥轮换或高风险授权操作不得无审计成功。
- AC-4：公开 5xx 响应不含堆栈、SQL、内部 URL 或原始 Provider body。
- AC-5：模拟队列积压、Provider 故障和 outbox 延迟分别触发正确告警与 runbook。
- AC-6：任取一个 Revision 激活、运行取消和密钥撤销操作均有不可变 AuditRecord。

## 14. 测试场景

- 正常：跨 API/worker/Provider trace、用户错误和审计查询。
- 边界：长错误、未知 Provider code、高事件量和保留边界。
- 失败：日志、trace、指标、审计和告警通道分别故障。
- 权限：开发者越权生产日志、跨 owner 审计和未授权内容正文访问。
- 并发/恢复：高并发 correlation、缓冲重放、重复 outbox 和服务重启。

## 15. 交付与回退

- Foundation 交付日志/指标/trace SDK、SafeError registry、审计存储和基础 dashboard。
- 各模块接入必须通过敏感信息测试后才能启用生产流量。
- 新观测后端可双写迁移；回退不删除旧审计。
- 发布证据包括三类故障演练、correlation 定位和 secret 清理报告。

## 16. 已决策事项与开放问题

已决策：外部只展示安全错误，内部诊断通过 correlation ID 受控关联；日志不是业务真相。

开放问题：具体观测供应商和保留时长由部署、成本与法律策略决定，但 P0 审计不得因供应商切换丢失。
