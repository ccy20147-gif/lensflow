# 文件、Blob 与媒体资产存储

## 1. 元数据

- ID：TF-OPS-003
- 标题：文件、Blob 与媒体资产存储
- 状态：in_delivery
- 目标版本：Foundation -> V0
- 优先级：P0
- 全局位置：资源库/平台内核
- 直接依赖：Foundation：TF-ARC-001；V0 增加 TF-PLT-001
- 责任域：存储平台
- 个人 DRI：main-agent

## 2. 背景与问题

图片、视频、音频、文档和控制稿体积大，不能直接放入业务表或长期公开 URL。ArtifactVersion 需要稳定内容引用、hash、owner 边界和生命周期，而上传失败、重复文件和 Blob 删除不能破坏 Revision。

Foundation 先冻结元数据/Blob 合同，V0 完成私有访问与可恢复上传。

## 3. 目标与非目标

目标：

- 分离业务元数据与 Blob 内容。
- 提供私有上传、断点恢复、内容 hash、去重和签名读取。
- 保证 ArtifactVersion 引用的 Blob 可验证、可迁移和受生命周期保护。
- 支持安全删除、隔离和存储运营。

非目标：

- 不提供匿名永久公开 URL。
- 不让对象存储元数据成为 Resource/Artifact 真相。
- 不在 V0 承诺全球 CDN 或长视频转码平台。

## 4. 用户与权限

- 项目 owner 可上传和读取本项目 Blob。
- Worker 可在节点 scope 内读取输入并写输出。
- Provider 临时访问使用最小范围、短期限 URL。
- 存储运维只能管理物理对象和索引，不能改变业务授权。
- 安全处置可以隔离内容并保留审计 hash。

## 5. 用户场景与主流程

1. 客户端请求 UploadSession，声明项目、类型、大小和 hash。
2. 后端校验 owner、配额、类型与安全策略。
3. 客户端分片直传或经服务上传，并可断点继续。
4. 完成后系统验证大小、hash 和对象存在性。
5. ArtifactVersion 事务只引用已完成 BlobRef。
6. 读取时后端签发短期 URL 或受控流。

## 6. 功能需求

- FR-1：Blob 元数据必须与物理对象分离，并使用稳定 blob_id。
- FR-2：BlobRef 必须记录 owner_scope、storage key、size、media type、content hash 和状态。
- FR-3：Foundation 必须冻结上传、完成、读取、删除和迁移合同。
- FR-4：V0 必须支持分片或可恢复上传以及幂等完成。
- FR-5：完成前必须校验声明大小、hash 和实际对象。
- FR-6：去重不得让一个 owner 推断另一个 owner 的文件存在。
- FR-7：私有读取使用短期限、限用途签名 URL 或后端流。
- FR-8：仍被 active/retired Revision、ArtifactVersion、Run 或审计引用的 Blob 不得物理删除。
- FR-9：生命周期任务必须区分临时上传、孤儿 Blob、有效引用和法律保留。
- FR-10：存储迁移必须保持 blob_id、content hash 和业务引用不变。
- FR-11：上传类型、大小和文件名不能作为可信内容判定。
- FR-12：任何失败都不得创建指向不完整对象的 ArtifactVersion。

### 逐版本切片矩阵

| 能力 | Foundation | V0 |
| --- | --- | --- |
| 合同 | BlobRef、UploadSession、生命周期 ADR | 生产级元数据与对象存储 |
| 上传 | fake/本地 adapter contract | 私有直传、分片或断点恢复 |
| 完成 | hash 与幂等规则 | 大小/hash/对象验证 |
| 访问 | owner_scope 与签名策略 | 项目私有读取和 Provider 临时访问 |
| 运维 | 删除/迁移规则 | 去重、孤儿清理和恢复演练 |

## 7. 交互与展示

- 上传界面显示进度、暂停/继续、失败原因和取消。
- 资源库显示安全缩略图、大小、类型和处理状态。
- 签名 URL 对用户透明，不显示长期 storage key。
- 删除受引用保护时展示引用类型和可执行动作。
- 隔离内容显示安全状态，不直接暴露内部策略。

## 8. 数据、类型与公共接口

核心对象包括 BlobRef、UploadSession、UploadPart、BlobReferenceIndex 和 LifecycleAction。

ArtifactVersion 通过 content_uri 或 blob_refs 引用 blob_id；对象存储 key 不是公共业务 ID。

UploadSession 包含 expected_size、expected_hash、expires_at、part state 和 idempotency key。

## 9. 状态机与业务规则

BlobStatus 至少为 uploading、available、quarantined、deletion_pending、deleted。

UploadSession 可为 initiated、uploading、verifying、completed、aborted、expired。

completed 后内容不可原地替换；不同内容必须创建新 blob_id。相同 owner 内可安全去重引用。

## 10. 失败、降级与恢复

- 分片失败可从已确认 part 继续。
- 完成验证失败时对象隔离或删除，不能进入 available。
- 对象存在但元数据写入失败时由 orphan scanner 处理。
- 元数据存在但对象丢失时立即告警并尝试副本恢复。
- 签名服务不可用时不回退公开 URL。
- 存储迁移中读取按映射回退旧位置，直到校验完成。

## 11. 安全、隐私、内容与授权

- storage key 不包含用户文件名、邮箱或可猜测项目 ID。
- 上传和读取均验证 owner_scope、用途、大小与速率限制。
- Provider URL 限定单对象、短期限和必要方法。
- 日志净化签名参数与原始文件内容。
- 恶意文件扫描或内容 Gate 结果可触发 quarantine。

## 12. 观测与运营

- 指标包括上传成功率、吞吐、断点恢复、签名失败、存储容量和孤儿数量。
- 监控 hash 不一致、对象丢失、删除受阻和生命周期积压。
- 对每次签名、删除、隔离和迁移保留审计。
- 定期抽样验证 content hash 和备份可恢复性。

## 13. 验收标准

- AC-1：V0 上传中断后可从已完成分片继续，最终 hash 与原文件一致。
- AC-2：跨 owner 使用相同 hash 上传时，响应不泄露另一 owner 对象存在性。
- AC-3：过期签名 URL 和跨用途重放均被拒绝。
- AC-4：仍被 ResourceRevision 引用的 Blob 删除请求被阻断并列出引用。
- AC-5：模拟对象丢失时系统告警，ArtifactVersion 不被静默改指其他内容。
- AC-6：迁移后 blob_id、hash 和所有业务引用保持不变。

## 14. 测试场景

- 正常：小文件、大文件、分片上传、读取、缩略图和生命周期。
- 边界：零字节拒绝、最大大小、同 hash、特殊文件名和过期 session。
- 失败：网络中断、hash 错误、对象/元数据单边失败和签名服务故障。
- 权限：跨 owner、伪造 storage key、过期 URL 和 Provider URL 越权。
- 并发/恢复：重复完成、同一分片竞争、孤儿扫描和迁移恢复。

## 15. 交付与回退

- Foundation 交付存储 adapter、BlobRef schema、签名和生命周期 ADR。
- V0 接入目标对象存储并完成真实上传、私有读取和删除保护。
- 存储后端可通过 adapter 切换，迁移使用双读校验期。
- 回退时保持 blob_id 与元数据，不修改 ArtifactVersion。

## 16. 已决策事项与开放问题

### Foundation 实施与独立验收证据

- 2026-07-16：Blob/UploadSession 元数据、完整性校验、available gate、私有读取、引用保护、生命周期与可重建引用索引的 Foundation 合同已实现并独立验收。
- PostgreSQL 专项及 artifact/resource/runtime/authorization 回归 91 passed；`alembic upgrade head`、`ruff check src tests` 与 `mypy src` 通过。
- V0 的真实对象存储、私有签名读取和分片/断点续传体验仍未交付，本 PRD 保持 `in_delivery`。

已决策：元数据与 Blob 分离；私有访问和引用保护从 V0 起强制。

开放问题：具体对象存储供应商与 CDN 方案由部署 ADR 决定，不得引入永久公开 URL。
