# LensFlow 开发准备门、团队认领规则与 PRD 顺序

> 文档 ID：TF-DEV-READY-001  
> 状态：主代理已裁决，供团队认领  
> 日期：2026-07-12  
> 权威上游：`docs/2026-07-12-toonflow-product-requirements-master.md`  
> 交付跟踪：`docs/2026-07-12-toonflow-prd-delivery-tracker.md`  
> 适用范围：Foundation、V0、V1 Core、V1 Community、V1.5、V2

## 1. 开发就绪结论

当前 62 份 PRD 的产品合同已经足够支持工程拆解：62/62 文件存在，ID 无重复，直接依赖无未知项，依赖图无环；每份文件均有 16 个标准章节、连续 FR/AC、失败恢复、测试、验收、交付回退和开放问题。

当前状态以交付跟踪表为准：23 个 PRD 为 `in_delivery`，32 个为 `defined`，3 个为 `verified`，TF-PLT-003 经多职能评审为 `reviewed`，3 个 V2 PRD 为 `deferred`。`reviewed` 不等于 `approved`，现有 `in_delivery` 也不能在缺少验收证据时提升为 implemented。后续继续采用两级认领：

| 认领级别 | 当前是否允许 | 含义 |
| --- | --- | --- |
| 开发准备任务 | 允许 | 认领 ADR、Schema、spike、许可、测试夹具、clean-room 骨架和实施拆解 |
| 正式功能交付 | 条件允许 | PRD 完成 DRI/评审人分配、`reviewed -> approved`、依赖与专项 Gate 后进入 `in_delivery` |
| deferred 实现 | 不允许 | 需要对应版本重新立项并完成迁移、容量和商业 Gate |

本结论不把“文档完整”误写成“已经批准”，也不要求等待所有未来版本完成后才启动 Foundation。

## 2. 单一真相与项目管理同步

需求范围、公共合同、版本边界和 FR/AC 只在 Master 与对应 PRD 中修改。项目管理工具采用单向引用：

1. PRD 向项目管理工具提供文件路径、需求 ID、版本、FR/AC 和依赖。
2. 项目管理工具只保存人员认领、排期、任务状态、实现链接、测试证据和阻塞项。
3. 项目管理工具不得直接改写范围、公共类型或验收标准。
4. 任何范围变化先走 TF-GOV-001 变更，再同步任务；禁止建立第二需求真相源。

此规则关闭 TF-GOV-001 在进入 `in_delivery` 前的同步方式开放项。

## 3. 团队认领卡

每个 PRD 的每个版本切片必须建立一张认领卡，至少包含：

| 字段 | 要求 |
| --- | --- |
| PRD | 只使用 `TF-*.md` 文件名和正式 ID |
| milestone_slice | Foundation、V0、V1 Core、V1 Community、V1.5 或 V2 |
| work_item_type | ADR、schema、spike、implementation、migration、test、operations |
| personal_dri | 一名可负责最终收口的个人，不得只写团队名 |
| reviewers | 产品验收人、技术评审人、QA；安全/法务按 Gate 增加 |
| scope | 明确认领的 FR/AC；不能只写整个文件 |
| prerequisites | 直接依赖、claim Gate、release Gate 及其证据链接 |
| deliverables | 设计、Schema、API、迁移、实现、自动测试、E2E、监控和回退 |
| evidence | 每项证据反链稳定 FR/AC |
| status | 使用 PRD 生命周期；任务工具状态不能替代 Requirement 状态 |

认领时先把 PRD 的个人 DRI 从“待指派”改为真实人员，完成产品、技术、QA 审查后才能提升为 `approved`。专项 Gate 未关闭时可以认领合同或适配器骨架，但不能把真实功能标为 `verified`。

## 4. Foundation 必须关闭的开发准备门

### 4.1 Gate G0：治理与许可

| 交付物 | 责任 PRD | 解锁条件 |
| --- | --- | --- |
| 需求状态、认领卡与证据链 | `TF-GOV-001.md` | P0 切片有个人 DRI、产品/技术/QA 评审人和项目工具反链 |
| 第三方逐组件台账 | `TF-GOV-002.md` | Toonflow App/Web、SeedV、Vue Flow、WebAV、字体和资产固定 SHA，并裁决 reuse/rewrite/abandon/blocked |
| clean-room 回退 | `TF-GOV-002.md` | 移除所有未批准复用项后 FastAPI/Vue 核心仍可构建 |

任何未裁决第三方文件不得进入生产构建；这不阻止基于独立规格进行 clean-room 实现。

### 4.2 Gate G1：必须冻结的 ADR

以下 ADR 可以并行编写，但相关正式持久 Schema 或业务实现必须等待对应 ADR 批准：

| ADR | 必须冻结的决策 | 责任 PRD |
| --- | --- | --- |
| Backend/Deployment | 关系数据库、事务隔离、迁移/回退、API version、模块依赖、部署拓扑、secret provider | `TF-ARC-001.md` |
| Product Layers | 工作区、业务画布、Workbench、Agent、Media Recipe 和 Runtime 调用边界 | `TF-ARC-002.md` |
| Identity/Tenancy | V0 bootstrap owner、V1 认证、owner_scope、service identity、callback 签名 | `TF-PLT-001.md`、`TF-SEC-001.md` |
| Schema/Compatibility | schema identity/version、canonical hash、代码生成、RegistrySnapshot、旧版本读取 | `TF-WF-002.md`、`TF-WF-004.md` |
| Runtime/Queue | attempt、lease/heartbeat、epoch/fencing、Outbox、取消、unknown 对账 | `TF-WF-006.md`、`TF-OPS-001.md` |
| Blob/Storage | BlobRef、durability barrier、签名 URL、上传、hash、生命周期、CDN 边界 | `TF-OPS-003.md` |
| Observability/Audit | correlation/trace、SafeError、日志净化、审计、保留、告警和 runbook | `TF-OPS-005.md` |
| Security/Policy | 首发地域、同意/权利证据、人工 SLA、fail-closed、合成披露与撤权 | `TF-SEC-001.md` |
| NFR Environment | 参考 CPU/GPU/网络、浏览器、视口、样本窗口和视觉基线更新 | `TF-NFR-001.md` |

具体供应商可以由 ADR/spike 裁决，但不得改变 PRD 已冻结的事务、版本、权限和恢复语义。

### 4.3 Gate G2：公共 Schema 冻结包

正式数据库表、API DTO 和前端类型必须映射到一个版本化合同包，至少覆盖：

- identity：Actor、OwnerScope、ProjectRef、IdempotencyKey、Correlation、SafeError、AuditRecord；
- content：ArtifactVersion/Ref、Resource/Draft/Revision/Ref、lineage、BlobRef、UploadSession；
- workflow：Workflow Draft/Revision、NodeDefinitionRevision、PortTypeRef、ConverterRevision、RegistrySnapshot；
- runtime：CompiledExecutionPlan、WorkflowRun、NodeRun、NodeRunAttempt、AttemptStatus、TaskBinding、OutboxEvent、epoch/fencing；
- provider：Provider/Model/Policy Revision、CapabilitySnapshot、CompilationReport、CredentialBinding、InvocationAttempt/Record/OutputBinding；
- security：PolicyDecision、RightsEvidence、ConsentEvidence、DisclosureManifest、GrantSnapshot、EntitlementDecision。

合同包必须提供 JSON Schema、Pydantic、TypeScript 映射与跨语言 contract tests。各域不得复制公共枚举或自行定义平行状态。

### 4.4 Gate G3：真实 Provider spike

Foundation 至少完成一个真实图片 Provider spike，固定 provider/model/capability revision，并验证：凭证隔离、请求/响应、异步协议、idempotency、unknown 对账、崩溃恢复、多候选、实际成本、模型版本、控制降级和 secret 轮换。

以下能力另有启用门：

| 能力 | 启用证据 |
| --- | --- |
| 图片生成 | `TF-MED-009.md` 真实 Provider E2E |
| 视频生成 | `TF-MED-010.md` 真实视频 Provider、恢复、质量与安全 E2E |
| source-video modify | 至少一个 Provider 完成分片控制 E2E；compile-only 不算交付 |
| 音频/配音 | `TF-MED-011.md` Provider、同步、授权和撤回 E2E |
| Grid composite/custom | `TF-MED-004.md` capability snapshot 与真实生成证据 |

### 4.5 Gate G4：质量、安全和发布基线

`TF-QLT-001.md` 必须建立文本、身份、镜头控制、51 镜头、广告图、核心交互六类固定数据集和 rubric revision。真实 Provider spike 后形成首个签名基线和阈值；阈值冻结前可以开发合同、UI 和 adapter，不能发布相关功能。

安全基线必须覆盖跨 owner、真人肖像/声音、未成年人、冒充、版权/商标、提示注入、多语言规避、撤权与 dispatch 竞态。社区、声音克隆和跨 owner 引用不能在相应法律与安全 Gate 前启用。

## 5. 认领状态裁决

| 分类 | 文件 | 裁决 |
| --- | --- | --- |
| in_delivery | 23 个 PRD | 继续按认领范围交付；只有建立 FR/AC 证据后才能提升 implemented/verified |
| reviewed | `TF-PLT-003.md` | 用户已冻结产品方向；同步公共合同、认领卡和 Gate 后可进入 approved/in_delivery |
| ready_after_gate | 34 个 `defined` PRD | 可按第 7 节顺序认领准备任务；正式实现需 DRI、approved、依赖和专项 Gate |
| deferred | `TF-LNG-001.md`、`TF-TEAM-001.md`、`TF-MKT-001.md` | V2 重新立项前不得进入 delivery |

当前不存在可以跳过治理门直接标为 `in_delivery` 的 PRD。阻断来自责任和证据未落位，不是 PRD 正文缺失。

## 6. 自动校验

运行：

```bash
node scripts/validate-requirements.mjs
```

检查内容包括文件名/ID、一致状态、元数据、1..16 章节、FR/AC 连续性、未知依赖、依赖环、Master 覆盖和本文件是否覆盖全部 PRD 文件名。该检查应进入 CI；它不能替代产品、技术、安全或 QA 审查。

## 7. PRD 开发顺序

规则：`串行` 表示下一组必须等待当前组的合同或 Gate；`并行` 表示该组满足各自直接依赖后可由不同团队同时认领。多版本 PRD 会在后续里程碑再次出现，只认领对应版本切片。

### 7.1 Foundation

1. 串行 F0：`TF-GOV-001.md`
2. 串行 F1：`TF-GOV-002.md`
3. 串行 F2：`TF-ARC-001.md`
4. 并行 F3：`TF-ARC-002.md`、`TF-OPS-001.md`（Provider 合同、凭证和基础 spike）、`TF-OPS-003.md`、`TF-OPS-005.md`、`TF-WF-002.md`
5. 并行 F4：`TF-QLT-001.md`、`TF-SEC-001.md`、`TF-WF-004.md`
6. 串行 F5：`TF-WF-005.md`
7. 串行 F6：`TF-WF-003.md`
8. 串行 F7：`TF-WF-006.md`

F7 关闭后，公共版本、编译、持久运行、存储、安全、Provider 和质量合同才形成平台实现基线。`TF-WF-004.md` 必须先于直接依赖它的 `TF-WF-003.md`。F3 的 `TF-OPS-001.md` 不能在该阶段宣称完成完整可靠调用；Outbox、发送前后崩溃恢复和 unknown 对账必须等待 F7 的 `TF-WF-006.md` 后关闭 Provider Gate。

2026-07-16：F3 的 `TF-OPS-003` Foundation slice 与 F5 的 `TF-WF-005` 以“批次 A：内容与存储基座”联合验收。`TF-WF-005` 已 verified；`TF-OPS-003` 的 V0 对象存储、签名读取和可恢复上传仍在交付中。

2026-07-16：F6 的 `TF-WF-003` 与 `TF-SEC-001` 最小编译 entitlement gate 以“批次 B：图编译与最小准入门”联合验收。`TF-WF-003` 已 verified；`TF-SEC-001` 的同意、审核、撤回、披露与导出范围仍在交付中。

2026-07-16：F7 的 `TF-WF-006` 与 `TF-OPS-001` 以“批次 C：持久运行与 Provider 调用事实链”完成 Foundation slice 独立验收。固定持久 plan、epoch fencing、输出绑定 owner、dispatch/result outbox dedupe、unknown 对账和取消隔离已验证；两项仍为 `in_delivery`，真实 Provider spike、V0 最小持久运行与 V1 的 lease/复杂恢复、fallback 和健康矩阵不得据此关闭。

2026-07-16：批次 C V0 hardening 对 `TF-WF-006` / `TF-OPS-001` 的 phase-2 fencing、lease heartbeat/recovery、partial-run 闭包和 rejected callback 单次审计完成独立验收。该切片不改变两项 `in_delivery` 状态，也不关闭真实 Provider gate、完整 V0 或 V1 范围。

### 7.2 V0

1. 并行 V0-A：`TF-PLT-001.md`、`TF-WF-009.md`、`TF-WF-010.md`
2. 并行 V0-B：`TF-OPS-002.md`、`TF-OPS-003.md`、`TF-OPS-004.md`、`TF-WF-004.md`、`TF-WF-006.md`、`TF-STY-001.md`
3. 并行 V0-C：`TF-SEC-001.md`、`TF-PLT-002.md`、`TF-MED-002.md`
4. 并行 V0-D：`TF-NFR-001.md`、`TF-MED-009.md`
5. 并行 V0-E：`TF-PLT-003.md`（V0 创作壳/项目关联切片）、`TF-MED-001.md`、`TF-IMG-001.md`、`TF-MED-012.md`

V0 的 `TF-MED-002.md` 文本 ShotPlan 可以与图片 Provider 开发并行，但缩略图和完整基准链验收必须等待 `TF-MED-009.md`。`TF-MED-001.md` 直接依赖 `TF-MED-009.md`，不得反向排期。

### 7.3 V1 Core 平台扩展

1. 并行 C0：`TF-PLT-001.md`、`TF-OPS-001.md`、`TF-OPS-002.md`、`TF-OPS-004.md`、`TF-QLT-001.md`、`TF-SEC-001.md`、`TF-WF-004.md`、`TF-WF-006.md`、`TF-WF-009.md`
2. 并行 C1：`TF-WF-001.md`、`TF-WF-007.md`、`TF-WF-008.md`、`TF-AGT-005.md`、`TF-AGT-006.md`、`TF-MR-001.md`、`TF-NFR-002.md`、`TF-MED-009.md`
3. 并行 C2：`TF-PLT-003.md`（V1 Core 资产化创作体验切片）、`TF-AGT-001.md`、`TF-WF-010.md`、`TF-NFR-001.md`、`TF-MED-001.md`、`TF-IMG-001.md`
4. 并行 C3：`TF-AGT-002.md`、`TF-AGT-003.md`、`TF-AGT-004.md`、`TF-STY-002.md`

同组内部仍遵守直接依赖：例如 Agent Studio 只有在 AgentDefinition、Tool/Skill 与 Human Gate 合同可用后才能完成集成验收。

### 7.4 V1 Core 小说与影视主链

平台 C3 与以下领域链可以在接口冻结后分团队推进，但每条链内部保持顺序：

1. 串行 C4：`TF-STY-003.md`
2. 并行 C5：`TF-STY-004.md`、`TF-MED-011.md`
3. 串行 C6：`TF-STY-005.md`
4. 串行 C7：`TF-STY-006.md`
5. 串行 C8：`TF-MED-002.md`
6. 串行 C9：`TF-MED-003.md`
7. 并行 C10：`TF-MED-005.md`、`TF-MED-006.md`
8. 并行 C11：`TF-MED-004.md`、`TF-MED-007.md`、`TF-MED-008.md`
9. 串行 C12：`TF-MED-010.md`
10. 串行 C13：`TF-MED-012.md`

`TF-STY-002.md` 已在 C3 开始，`TF-STY-003.md` 等待其世界观/OC 合同。镜头工作台先有 `TF-MED-003.md`，3D/Provider 编译可并行，宫格、连续性和身份审查再并行，最后接真实视频和时间线。

### 7.5 V1 Community

1. 并行 G0：`TF-COM-004.md`、`TF-COM-006.md`
2. 并行 G1：`TF-COM-001.md`、`TF-COM-002.md`、`TF-COM-003.md`
3. 串行 G2：`TF-COM-005.md`

授权与治理合同必须先于公开发布。作品、世界/OC 和工作流模板可以并行建设；发现/搜索等待可公开内容类型稳定。

### 7.6 V1.5

当前已有完整 PRD 切片、在完成第 3/5 节通用认领门后可正式认领的 V1.5 工作可并行：`TF-MED-011.md`、`TF-MED-012.md`、`TF-COM-007.md`。

Master 中提到的精确 Animatic、IK/动捕、3DGS、空间重建、高级灯光/色彩和视频 extend/reframe 已明确后置，但 `TF-MED-003.md`、`TF-MED-005.md`、`TF-MED-007.md`、`TF-MED-010.md` 目前没有独立 V1.5 FR/AC 和交付门。它们不属于当前可认领切片；V1.5 立项前必须先走 TF-GOV-001 变更，补版本矩阵与可独立验收合同，不能从非目标段落推导实现任务。

### 7.7 V2

1. 串行 V2-Gate：重新立项、团队权限迁移 ADR、长内容容量测试和市场合规/结算 Gate。
2. 并行 V2-A：`TF-TEAM-001.md`、`TF-LNG-001.md`
3. 串行 V2-B：`TF-MKT-001.md`

三个文件当前均保持 `deferred`。`TF-MKT-001.md` 依赖团队、社区、结算与 V1.5 生态能力，不能提前实现。

## 8. 版本验收主链

开发顺序不是发布证明。每个版本只有在以下主链完成时才能通过：

| 版本 | 最小验收主链 |
| --- | --- |
| Foundation | 治理/许可 -> FastAPI/Vue 骨架 -> Schema -> Artifact/Revision -> 编译 -> 持久运行 -> Provider spike -> 质量基线 |
| V0 | Idea/剧本 -> ShotPlan + 产品 Brief -> 三张真实广告候选 -> 人工选择 -> Timeline JSON/交付包 |
| V1 Core | 动态画布/Agent/Recipe -> 小说与镜头工作台 -> 真实图片/视频 -> 单镜替换 -> 视频代理和基础音轨时间线 |
| V1 Community | 授权/审核 -> 作品、世界/OC、模板发布 -> 引用/克隆 -> 发现检索 |
| V1.5 | 专业媒体切片通过各自 Provider/设备/质量 Gate；Agent/Recipe 可独立发布安装 |
| V2 | 团队与长内容先通过迁移/容量 Gate，再启用市场交易与生态治理 |

## 9. Terra 审查与主代理裁决

Terra 子代理完成了原 61 份 PRD 的完整性、依赖拓扑、版本切片与 Foundation Gate 审查；TF-PLT-003 另由 UI/UX、前端、后端/领域架构和总架构完成独立评审。主代理采纳以下发现：

- 62/62 PRD 结构完整，依赖无环且无未知 ID；
- 23 个 PRD in_delivery、32 个 defined、1 个 reviewed、3 个 verified，3 个 V2 PRD deferred；
- 正式认领前必须补个人 DRI、reviewed/approved 和证据反链；
- 修正 Master 中 WF-003/WF-004、MED-001/MED-009 和 WF-009 的人类可读顺序；
- 把 ADR、Schema 包、许可台账、真实 Provider spike、质量和安全基线列为显式 Gate；
- V1.5 未形成独立 FR/AC 的高级能力保持不可认领，不以非目标文字生成隐式范围。

未采纳“因为全部 DRI 待指派而完全停止工程”的严格解释。治理、ADR、spike、clean-room 骨架、Schema harness 和评测框架可立即认领；只有正式功能交付需要完整批准门。
