# 体检结果批量上传平台

工作人员导入 Excel（人员编号、项目、结果、单位、体检日期），后端逐行本地校验、
自动识别重复数据、按限流上送健康平台；平台返回异常时**按行展示错误码与原因**，
支持任务进度实时跟踪、一键导出异常 Excel、取消与断点续传。

## 功能清单

| 需求 | 实现 |
|---|---|
| 导入 Excel | `.xlsx`，五列必填，前 5 行内自动识别表头，支持常见列名别名（如「工号」「检查日期」） |
| 后端校验 | 必填/编号格式/项目目录匹配/单位匹配/数值格式/日期格式/物理可行区间，错误逐行落库 |
| 上送健康平台 | 模拟客户端：25 条/秒限流、幂等键去重、随机瞬时故障（超时/500/502）、确定性业务异常（查无此人 422）、409 幂等拦截 |
| 异常按行展示 | 明细抽屉分「全部/校验失败/平台异常/重复/待上送/成功」页签，分页查看平台错误码与原始返回 |
| 任务进度 | 状态机：校验中 → 上送中 → 已完成 / 完成（含异常）/ 已取消 / 任务失败；2 秒轮询，进度条+分阶段提示 |
| 重复识别 | **文件内**（同文件同人同项目同天）与**跨文件**（指纹表全局唯一约束）双重识别，提示来源文件/行号/结果差异 |
| 异常导出 | 三页签 Excel：校验失败、平台异常、重复数据，含汇总页与原始行号 |
| 取消/续传 | 上送中可取消；取消时自动与平台对账（在途请求按成功回写），释放未上送指纹；可「继续上送」 |
| 失败重试 | 平台瞬时故障（可重试）可一键重上送；「查无此人」等确定性异常保留结论，提示修改后重新导入 |
| 参考范围提示 | 超医学参考范围只产生 WARNING 仍正常上送；超物理可行区间直接校验失败 |

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 已有 venv 可跳过
.venv/bin/python run.py
# 打开 http://localhost:8000
```

生成演示数据：

```bash
.venv/bin/python samples/make_sample.py          # samples/体检结果_示例.xlsx（35 行，含各类异常）
```

端到端测试（32 项断言）：

```bash
HC_PLATFORM_FAIL_RATE=0 .venv/bin/python tests/smoke_test.py
```

## 导入格式

| 列 | 说明 |
|---|---|
| 人员编号 | 3-30 位字母、数字或连字符；平台建档库预置 P1001~P1050（模拟） |
| 项目 | 项目代码（GLU）或中文名（空腹血糖），模板「项目目录」页含 17 项标准目录 |
| 结果 | 数值 |
| 单位 | 必须与项目目录一致（支持少量等价写法，如 mmoL/L→mmol/L） |
| 体检日期 | `YYYY-MM-DD`、`YYYY/M/D`、`YYYYMMDD`；不晚于今天、不早于 2000 年 |

同一人员 + 同一项目 + 同一天只允许一条有效结果（SHA-256 指纹）。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tasks` | multipart 上传 Excel，创建并启动任务 |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务详情与计数 |
| GET | `/api/tasks/{id}/rows?status=&limit=&offset=` | 逐行记录（可按状态过滤、分页） |
| POST | `/api/tasks/{id}/cancel` | 取消任务 |
| POST | `/api/tasks/{id}/resume` | 继续上送 / 重试平台异常行 |
| GET | `/api/tasks/{id}/errors.xlsx` | 导出异常明细 |
| GET | `/api/template` | 下载导入模板（含填写说明、项目目录） |
| GET | `/api/catalog` | 体检项目目录 |

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `HC_DATA_DIR` | `./data` | SQLite 与上传文件目录 |
| `HC_MAX_UPLOAD_MB` | 20 | 单文件大小上限 |
| `HC_PLATFORM_LATENCY_MS` | 80 | 模拟平台单次调用延迟 |
| `HC_PLATFORM_FAIL_RATE` | 0.05 | 模拟平台瞬时故障概率（0~1） |

## 结构

```
app/
  config.py       配置与目录
  statuses.py     行/任务状态机
  catalog.py      17 个体检项目目录、单位与参考范围
  validator.py    逐行本地校验、日期解析、指纹生成
  excel_io.py     Excel 读取（表头别名识别）、模板、异常导出
  platform.py     模拟健康平台（幂等/限流/瞬时故障/建档校验）
  processor.py    后台处理引擎（线程）、取消、对账、续传
  db.py           SQLite 持久化（tasks / task_rows / fingerprints）
  api.py          HTTP 路由
  static/         前端单页（原生 JS，无需构建）
samples/          演示数据生成器
tests/            端到端冒烟测试
```

## 替换为真实健康平台

实现一个与 `HealthPlatformClient.upload(record) -> {"record_id": ...}` 同构的客户端
（失败时抛带 `code/message/retryable/http_status` 的 `PlatformError`），
替换 `app/processor.py` 顶部的 `client` 即可，其余流程不变。
