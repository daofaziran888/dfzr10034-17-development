# 治数工坊 · 数据治理与生命周期管理平台

面向资产注册、元数据索引与搜索、质量规则引擎、数据画像与分级、脱敏与操作日志、生命周期策略、冷热归档、血缘追踪、影响分析、变更捕获与契约治理的数据治理与生命周期管理平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 质量测量 | `quality_measure` | 质量指标检查记录 | 测量编号、检查编号、所属表、评估窗口、证据状态、复测状态 |
| 反馈表周表 | `survey_week` | 评分对象（质量弱、元数据齐） | 表编号、周次、质量测量条数、元数据完备性 |
| 结算表周表 | `payout_week` | 评分对象（证据分布相反） | 表编号、周次、质量测量条数、元数据完备性 |
| 资产注册 | `asset_registry` | 数据资产 | 资产编号、资产名称、资产类型 |
| 元数据索引 | `metadata_index` | 元数据条目 | 条目编号、所属资产、字段名称 |
| 质量规则 | `quality_rule` | 质量检查 | 规则编号、目标资产、检查类型 |
| 数据画像 | `profile_stats` | 数据画像 | 画像编号、目标资产、行数统计 |
| 数据分级 | `classification` | 分级标签 | 标签编号、目标字段、敏感等级 |
| 生命周期 | `lifecycle_policy` | 生命周期策略 | 策略编号、目标资产、创建阶段 |
| 冷热归档 | `cold_archive` | 归档任务 | 任务编号、源资产、归档条件 |
| 保留清理 | `retention_job` | 清理计划 | 计划编号、目标资产、保留天数 |
| 脱敏引擎 | `data_anonym` | 脱敏规则 | 规则编号、目标字段、脱敏算法 |
| 操作日志 | `operation_log` | 操作记录 | 记录编号、目标资产、操作人 |
| 变更捕获 | `change_capture` | 变更记录 | 记录编号、目标资产、变更类型 |
| 血缘追踪 | `lineage_track` | 血缘关系 | 关系编号、上游资产、下游资产 |
| 影响分析 | `impact_analysis` | 影响报告 | 报告编号、变更资产、影响下游 |
| 标签体系 | `tag_registry` | 业务标签 | 标签编号、标签名称、标签层级 |
| 治理评分 | `govern_score` | 评分任务 | 任务编号、评估对象、评分维度 |
| 数据契约 | `data_contract` | 契约定义 | 契约编号、数据资产、Schema约束 |
| 合规扫描 | `compliance_scan` | 扫描任务 | 任务编号、目标资产、合规条款 |
| 问题追踪 | `issue_track` | 治理问题 | 问题编号、关联资产、问题类型 |
| 版本履历 | `version_history` | 版本快照 | 快照编号、目标资产、版本标签 |
| 调度治理 | `schedule_job` | 治理调度 | 作业编号、治理类型、目标资产 |
| 差异报告 | `diff_report` | 差异记录 | 记录编号、比较对象、比较维度 |
| 负责人管理 | `owner_mgr` | 责任人 | 指派编号、目标资产、负责角色 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 治理评分贡献视图

评审入口：前端导航「评分贡献视图」（路由 `/score-contribution`），后端固定路由前缀
`/api/governance-scoring`（在通用 `/api/{module}` 之前注册）。后端启动时由
`app/demo.py::build_demo_scenario` 幂等构建一套完整演示数据，`POST /api/demo/reset`
可重建。

口径要点：

- 一次评分按评估窗口内的质量检查记录（`quality_measure`）和权重计算每项贡献，
  贡献视图列出影响总分的原始检查编号、检查类型、权重、检查分与证据状态。
- **无证据**（未采集实测值，`check_score=null`，不贡献权重、按剩余权重重归一化）
  与**实际零分**（检查执行但实测越界，检查得 0 分并真实拉低维度分）严格区分。
- 同一检查编号只能归属一个评估窗口，跨窗口登记会被窗口守卫拒绝；同窗口质量权重
  合计不得超过 1。
- 评分签发时固化证据快照 `score_breakdown`；签发后补登记/补录的检查不改写原始
  分项依据，只进入重算漂移报告和下一轮复测清单。
- `POST /scores/{id}/recompute` 按原口径重算：旧评分走旧规则、证据化评分按快照
  重算，返回 `compatible` 与补登记证据漂移，兼容已有评分。
- 复测 `POST /measures/{id}/retest` 把结论回写到巡检待办 `schedule_job`
  （治理类型「质量巡检待办」），最初评分的分项依据保持不变。
- 贡献视图同时给出历史排名（按总分）与本周排查次序（按待复测/零分/无证据量），
  两者不一致时标记 `order_conflict`。

演示场景：`2026-W45` 的 `retention_week`（含实际零分 + 签发后补登记）与
`weekly_active_week` 构成排名/排查次序冲突；`2026-W46` 的 `survey_week`
（质量检查无证据、元数据齐）与 `payout_week`（检查得零分、元数据稀疏）总分接近
（70.6 / 72.7）且证据分布相反，并各补录一次质量检查进入复测清单。

领域：数据工程
子方向：数仓与数据湖
