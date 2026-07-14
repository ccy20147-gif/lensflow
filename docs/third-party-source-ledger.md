# 第三方组件来源与许可台账

> 本台账记录 ToonFlow Foundation 层所有候选第三方组件的来源、许可、裁决和持续义务。
>
> Governed by: TF-GOV-002 | Last updated: 2026-07-12

---

## 组件清单

### 1. Toonflow-app

| 字段 | 值 |
|---|---|
| 组件名称 | Toonflow-app |
| 仓库 URL | `https://github.com/toonflow/toonflow-app` |
| 锁定 Commit SHA | `bc61ec7` |
| 获取日期 | 2026-07-10 |
| 许可证 | Apache-2.0 |
| 许可证 hash (sha256) | `af52dd7d237609f10282280add79d9d87e8cf0523846124f4a27efd22841632f` |
| **裁决** | `clean_room_rewrite` |
| 决策依据 | Toonflow-app 前身为开源参考项目，Apache-2.0 许可允许参考但要求 NOTICE 保留。ToonFlow 产品使用全新代码库，不直接复制源文件，仅通过公开 API 接口观察与功能等价实现。 |
| 裁决日期 | 2026-07-12 |
| 裁决人 | 工程治理负责组 |
| 持续义务 | 无直接复制，无需 NOTICE 义务；保留参考设计文档中的接口定义证据。 |
| 传递依赖 | 无（闭源构建，不重新分发） |

#### 文件级 Provenance

| 源文件 | 目标文件 | 关系 |
|---|---|---|
| 无 | — | clean-room rewrite，无可复制源文件 |

---

### 2. Toonflow-web

| 字段 | 值 |
|---|---|
| 组件名称 | Toonflow-web |
| 仓库 URL | `https://github.com/toonflow/toonflow-web` |
| 锁定 Commit SHA | `9c4cb0e` |
| 获取日期 | 2026-07-10 |
| 许可证 | Apache-2.0 |
| 许可证 hash (sha256) | `677dc241c3e38e72a835699dc4bb749d817e4cf2f65f4b5ce086cc9ed611694d` |
| **裁决** | `clean_room_rewrite` |
| 决策依据 | 同 Toonflow-app，Apache-2.0 开源参考。ToonFlow 前端 UI、工作流画布和组件库独立实现，仅参考公开交互模型与功能规格。 |
| 裁决日期 | 2026-07-12 |
| 裁决人 | 工程治理负责组 |
| 持续义务 | 无直接复制，无需 NOTICE 义务；保留参考设计文档中的接口定义证据。 |
| 传递依赖 | 无（闭源构建，不重新分发） |

---

### 3. SeedV

| 字段 | 值 |
|---|---|
| 组件名称 | SeedV |
| 仓库 URL | `https://github.com/nousresearch/seedv` |
| 锁定 Commit SHA | `54a6d4f` |
| 获取日期 | 2026-07-10 |
| 许可证 | 私有 — 所有者授权引用 |
| 许可证 hash | N/A（私有项目） |
| **裁决** | `private_authorized_reference` |
| 决策依据 | SeedV 为 Nous Research 内部项目，所有者已授权 ToonFlow 作为设计参考。仅限于架构模式、领域流程和执行逻辑的非复制性参考，不用于再分发或衍生发布。 |
| 授权人 | 项目所有者 |
| 裁决日期 | 2026-07-12 |
| 裁决人 | 工程治理负责组 |
| 持续义务 | 1. 不复制 SeedV 源代码。2. 不引用 SeedV 的非公开配置或数据。3. 不在 NOTICE 或 LICENSE 中列出 SeedV。4. 仅使用的领域概念和架构模式需在 ADR 中标注“启发来源”。 |
| 使用边界 | 领域流程、Schema、可靠执行和生产经验参考；不用于代码复制、数据迁移或运行时依赖。 |

---

### 4. Vue Flow

| 字段 | 值 |
|---|---|
| 组件名称 | Vue Flow |
| 仓库 URL | `https://github.com/bcakmakoglu/vue-flow` |
| 锁定 Commit SHA | v1.48.2 (npm release) |
| 获取日期 | 2026-07-12 |
| 许可证 | MIT |
| 许可证 hash (sha256) | `102b29fe1db781aaad7c28e4ac4f20de288781ac5cd06936099be15a473c1794` |
| **裁决** | `approved_reuse` |
| 决策依据 | MIT 许可证允许复制、修改、闭源使用和再分发，仅要求保留版权声明和许可声明。Vue Flow 为前端工作流画布核心依赖，拟通过 npm 包管理器引用，不修改源码。 |
| 裁决日期 | 2026-07-12 |
| 裁决人 | 法务/工程治理 |
| 持续义务 | 1. 在 LICENSE 文件中保留 MIT 版权声明。2. 在 NOTICE 文件中列出 Vue Flow 及其 MIT 许可。3. 不移除 Vue Flow 源码中的版权注释。4. 分发时附带 MIT 许可副本。 |
| 传递依赖 | 详见 npm package |

---

### 5. WebAV

| 字段 | 值 |
|---|---|
| 组件名称 | WebAV |
| 仓库 URL | `https://www.npmjs.com/package/webav` |
| 锁定 Commit SHA | latest (npm) |
| 获取日期 | 2026-07-10 |
| 许可证 | MIT |
| 许可证 hash (sha256) | `待 V1.5 评估时补全` |
| **裁决** | `blocked` (待 V1.5) |
| 决策依据 | WebAV 提供浏览器端视频编码与时间线能力，但 V1 Core 交付前无需浏览器最终编码；V1.5 启用前需从 npm 获取最新版本并重新评估。 |
| 裁决日期 | 2026-07-12 |
| 裁决人 | 工程治理 |
| 持续义务 | V1.5 立项时重新评估并补全许可证 hash。当前保持 blocked，不进入生产依赖。 |
| 传递依赖 | 待 V1.5 评估 |

---

## 汇总

| 组件 | 许可证 | 裁决 | 持续义务 | NOTICE 要求 |
|---|---|---|---|---|
| Toonflow-app | Apache-2.0 | clean_room_rewrite | 无 | 无 |
| Toonflow-web | Apache-2.0 | clean_room_rewrite | 无 | 无 |
| SeedV | 私有 — 所有者授权 | private_authorized_reference | 不复制源码、不列出、ADR 标注来源 | 无 |
| Vue Flow | MIT | approved_reuse | 保留版权+许可副本 | 必须列出 |
| WebAV | MIT | blocked | V1.5 重新评估 | 待定 |

## 变更记录

| 日期 | 操作 | 组件 | 原因 | 操作人 |
|---|---|---|---|---|
| 2026-07-12 | clean_room_rewrite | Toonflow-app | 参考设计，独立实现 | 工程治理 |
| 2026-07-12 | clean_room_rewrite | Toonflow-web | 参考设计，独立实现 | 工程治理 |
| 2026-07-12 | private_authorized_reference | SeedV | 私有项目所有者授权参考 | 工程治理 |
| 2026-07-12 | approved_reuse | Vue Flow | MIT 许可，npm 引用 | 法务/工程治理 |
| 2026-07-12 | blocked | WebAV | V1.5 启用 | 工程治理 |

## 未裁决组件（blocked by default）

当前无额外未裁决组件。所有候选组件均有明确裁决。

## 参考

- [Vue Flow License](https://github.com/bcakmakoglu/vue-flow/blob/main/LICENSE)
- [Apache 2.0 License](https://www.apache.org/licenses/LICENSE-2.0)
