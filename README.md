# 体检结果批量上送平台

工作人员导入 Excel（人员编号、项目、结果、单位、体检日期），后端逐行校验后批量上送健康平台；
平台返回异常时按行展示，并提供任务进度追踪、重复识别与异常导出。

## 功能

- **Excel 导入**：`.xlsx` 上传 / 拖拽；自动校验表头（缺列直接报错），跳过空行，单次最多 5000 行 / 10MB。
- **后端校验（不落平台）**
  - 人员编号非空、统一大写规范化；项目按代码/中文名/别名匹配 8 个体检项目目录；
  - 结果非空且必须为数值；单位必须为该项目标准单位（兼容 `μmol/L`、`kg/m²` 等写法）；
  - 体检日期支持 `YYYY-MM-DD`、`YYYY/MM/DD`、`YYYYMMDD`、`YYYY.MM.DD`，拒绝未来日期；
  - 每行可同时返回多个错误码（如 E1001+E1006），便于一次改全。
- **重复识别**
  - 文件内重复：同一文件内「人员 + 项目 + 体检日期」重复 → `E2001`；
  - 跨任务历史重复：历史任务已成功上送过同样记录 → `E2002`（平台接收记录落库，重启不丢）。
  - 校验失败 / 重复行不会被上送。
- **上送健康平台**：确认后后台线程逐行调用平台接口，实时更新进度、成功/失败计数与异常汇总。
  模拟平台覆盖：成功 `S0000`、人员未建档 `E4001`、结果超医学合理范围 `E4003`、
  平台维护 `E5001`、网络异常 `E5002`、限流 `E5003`、数据冲突 `E3002`、平台重复 `E3001`。
  维护 / 限流 / 网络异常标记为「可重试」，支持一键重试。
- **按行展示异常**：任务明细可按 待上送 / 成功 / 全部异常 / 重复行 / 校验失败 / 平台拒绝 / 可重试 筛选，
  支持人员编号、项目搜索与分页；每行展示 Excel 行号、错误码、平台返回信息，成功行展示平台回执号。
- **异常导出**：一键导出该任务全部异常行为 Excel（含行号、错误代码、平台返回码与信息）。
- **模板与示例**：提供空白模板和内置 20 行示例（覆盖全部异常场景），附项目代码对照 sheet。

## 技术栈

- 后端：Python 3.11 + FastAPI + Uvicorn + openpyxl，SQLite（标准库 `sqlite3`，零额外服务）
- 前端：原生 HTML / CSS / JavaScript，无需构建
- 平台客户端：`app/platform.py` 为**确定性模拟实现**，真实部署时替换该文件内的 `submit()` 为实际 HTTP 调用即可

## 快速开始

```bash
./run.sh
# 或手动：
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000 ，点右上角「下载示例数据」，上传后即可体验完整流程。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/tasks` | 上传 Excel，解析并校验，返回任务与统计 |
| GET  | `/api/tasks?status=` | 任务列表（可按状态过滤） |
| GET  | `/api/tasks/{id}` | 任务详情（进度、异常汇总） |
| POST | `/api/tasks/{id}/confirm` | 确认上送全部有效行 |
| POST | `/api/tasks/{id}/retry` | 重试可重试行（维护/限流/网络） |
| GET  | `/api/tasks/{id}/rows` | 行明细（`status` / `category=exception|duplicate` / `q` / 分页） |
| GET  | `/api/tasks/{id}/export` | 导出异常行 Excel |
| GET  | `/api/template?sample=true` | 下载空白模板 / 示例数据 |
| GET  | `/api/items` | 体检项目目录 |

## 状态模型

- 任务：`uploaded → validated → uploading → completed`（另有 `failed`）
- 行：`pending` 待上送 / `success` 成功 / `invalid` 校验失败 / `duplicate` 重复 /
  `failed` 平台拒绝 / `retryable` 可重试

## 目录结构

```
app/
  main.py        FastAPI 路由（任务、明细、导出、模板）
  parser.py      Excel 解析、日期/数值标准化、模板生成
  validators.py  字段校验 + 文件内/跨任务重复识别
  platform.py    健康平台客户端（模拟）
  worker.py      后台上送线程与任务队列
  catalog.py     体检项目目录（代码、别名、单位、参考区间）
  database.py    SQLite 连接、建表、序列化
static/          前端页面（index.html / styles.css / app.js）
data/app.db      运行后自动生成的 SQLite 数据库
```
