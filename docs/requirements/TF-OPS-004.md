# RunEvent、通知与任务状态

## 1. 元数据

- ID：TF-OPS-004
- 标题：RunEvent、通知与任务状态
- 状态：defined
- 目标版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：产品外壳/平台内核
- 直接依赖：TF-PLT-001、TF-WF-006
- 责任域：事件平台/核心产品
- 个人 DRI：待指派

## 2. 背景与问题

用户需要实时看到运行、节点、成本、失败和人工等待，但 SSE、WebSocket、Redis Pub/Sub 或浏览器内存都可能断线和丢消息。若推送通道成为事实源，刷新后状态会与后端不一致。

RunEvent 必须持久、按序且可回放；实时通道只负责投递。

## 3. 目标与非目标

目标：

- V0 提供运行快照、持久 RunEvent、SSE 重连和任务通知。
- V1 Core 扩展多事件类型、通知偏好和规模化回放。
- 使用 after_seq 保证断线恢复和去重。
- 让前端始终以数据库快照和事件收敛。

非目标：

- 不把事件流当作业务数据库。
- 不保证每个通知通道恰好一次送达。
- 不在本需求实现外部营销消息系统。

## 4. 用户与权限

- 项目 owner 可订阅自己项目的运行和任务事件。
- Worker 与运行时通过事务 outbox 产生事件，不直接向用户连接写状态。
- 运营人员可查看净化事件诊断和积压。
- 通知发送者不能扩大事件中 Resource/Artifact 的访问权限。

## 5. 用户场景与主流程

1. 客户端先请求 WorkflowRun snapshot，获得 current_seq。
2. 客户端建立 SSE 或等价连接并传 after_seq。
3. 事件服务按 run_id 和 seq 回放缺失事件。
4. 新事件由 outbox 投递并持久记录，再推送给在线客户端。
5. 断线后客户端重复 snapshot + after_seq 流程。
6. waiting_user、失败或完成事件生成去重的站内任务通知。

## 6. 功能需求

- FR-1：每个 RunEvent 必须有 run_id、单调 seq、event_type、payload、created_at 和 owner_scope。
- FR-2：事件必须来自已提交业务状态或同事务 outbox。
- FR-3：客户端连接前必须能获取权威 run snapshot 与 current_seq。
- FR-4：after_seq 回放必须按 seq 有序且可重复请求。
- FR-5：客户端可以按 event_id/seq 去重，不因重放重复 UI 动作。
- FR-6：SSE/WebSocket/PubSub 只作投递，丢失后可从持久事件恢复。
- FR-7：事件 payload 必须版本化并提供安全摘要。
- FR-8：通知必须引用 run/node/task，不复制敏感内容。
- FR-9：waiting_user、failed、completed 和 cancelling 等关键状态必须产生事件。
- FR-10：事件保留与压缩不能破坏当前快照和审计链。
- FR-11：跨 owner 订阅和猜测 run_id 必须被拒绝。
- FR-12：事件消费失败不能回滚已完成业务状态。

### 逐版本切片矩阵

| 能力 | V0 | V1 Core |
| --- | --- | --- |
| 快照 | Run/Node 基础状态 | attempt、成本、人工任务和子运行摘要 |
| 事件 | 状态、错误、成本基础事件 | 完整版本化事件目录 |
| 投递 | SSE + after_seq | SSE/WebSocket 可选、水平扩展 |
| 通知 | 站内 waiting/failed/completed | 偏好、聚合和去重策略 |
| 保留 | 单运行完整回放 | 压缩、归档和审计查询 |

## 7. 交互与展示

- 运行页先显示快照，再平滑应用事件，不出现空白等待。
- 断线显示连接状态但保留最后权威数据。
- 任务中心展示等待用户、失败和完成通知，可跳到具体节点。
- 已读状态只影响通知展示，不改变 Run/NodeRun。
- 同一事件重放不重复弹窗、声音或计数。

## 8. 数据、类型与公共接口

RunEvent 包含 event_id、run_id、seq、event_type、payload_schema_version、payload、owner_scope 和 created_at。

RunSnapshot 包含 RunStatus、NodeRunStatus 集合、current_seq、成本摘要和待处理 HumanTask refs。

NotificationRecord 引用 event_id、recipient、channel、dedupe_key、delivery status 和 read_at。

## 9. 状态机与业务规则

RunEvent 本身追加不可变。seq 在每个 run 内单调且唯一。

NotificationDeliveryStatus 可为 pending、sent、failed、suppressed；重复投递不产生第二业务事件。

客户端若发现 seq 间隙必须停止增量应用并重新获取 snapshot。

## 10. 失败、降级与恢复

- SSE 断线后使用指数退避并携带 after_seq。
- Pub/Sub 丢消息时持久回放补齐。
- 事件投递失败由 outbox 重试，不修改业务状态。
- 通知通道不可用时站内任务仍可查询。
- 事件 payload 版本未知时客户端重新拉取快照并显示升级提示。
- 归档事件不可在线回放时提供受控历史查询。

## 11. 安全、隐私、内容与授权

- 订阅、快照和回放均验证 owner_scope。
- payload 不包含 secret、签名 URL、完整 prompt 或未授权内容。
- 通知预览按隐私设置隐藏敏感标题和缩略图。
- 事件存储访问、导出和运营检索审计。
- 连接 token 短期有效且不能跨项目重用。

## 12. 观测与运营

- 指标包括连接数、断线率、回放延迟、seq 间隙、outbox 积压和通知失败。
- 监控事件生成后长时间未投递和消费者落后。
- 按 event_type、schema_version 和客户端版本诊断兼容问题。
- 事件与 API trace 通过 correlation ID 关联。

## 13. 验收标准

- AC-1：客户端断线期间产生 20 个事件，使用 after_seq 重连后按序完整收到且不重复 UI 动作。
- AC-2：清空 Pub/Sub 后仍可从持久 RunEvent 恢复状态。
- AC-3：业务状态提交后事件投递失败，恢复后最终发送且业务记录不重复。
- AC-4：跨 owner 订阅、回放和通知查询全部被拒绝。
- AC-5：客户端检测到 seq 间隙时重新拉取 snapshot 并收敛到数据库状态。
- AC-6：waiting_user 通知重复投递只生成一个任务中心条目。

## 14. 测试场景

- 正常：快照、在线事件、任务通知、已读和完成。
- 边界：零事件、长运行、大量节点、事件压缩和旧客户端。
- 失败：SSE、Pub/Sub、outbox、通知发送和 payload 解析失败。
- 权限：跨 owner run_id、过期连接 token 和敏感通知预览。
- 并发/恢复：多事件生产者、重复投递、seq 间隙和服务重启。

## 15. 交付与回退

- V0 交付持久事件、snapshot、SSE after_seq 和站内关键通知。
- V1 Core 扩展事件目录、水平扩展与保留策略。
- 新事件 schema 先兼容旧客户端，必要时只依赖 snapshot。
- 回退实时通道时轮询 snapshot 仍能提供正确状态。

## 16. 已决策事项与开放问题

已决策：数据库事件和运行快照是真相；SSE/WebSocket 只是投递。

开放问题：是否在 V1 Core 同时启用 WebSocket 由容量测试决定，SSE after_seq 合同必须始终可用。
