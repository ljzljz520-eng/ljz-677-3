"""FastAPI 入口：体检结果批量上送应用。"""
from __future__ import annotations

import io
from urllib.parse import quote

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook

from . import validators
from .catalog import ITEMS
from .database import get_conn, init_db, row_to_dict, task_to_dict
from .parser import ExcelError, build_template_workbook, parse_excel
from .worker import enqueue_task, start_worker

@asynccontextmanager
async def lifespan(_app):
    init_db()
    start_worker()
    yield


app = FastAPI(title="体检结果批量上送平台", version="1.0.0", lifespan=lifespan)

ROW_STATUSES = {"pending", "success", "failed", "retryable", "invalid", "duplicate"}
TASK_STATUSES = {"uploaded", "validated", "uploading", "completed", "failed", "cancelled"}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------- 任务 ----------------

@app.post("/api/tasks")
async def create_task(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "仅支持 .xlsx 格式的 Excel 文件")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 10MB")

    try:
        records = parse_excel(content)
    except ExcelError as e:
        raise HTTPException(400, str(e))

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (filename, status) VALUES (?, 'uploaded')",
            (file.filename,),
        )
        task_id = cur.lastrowid
        conn.commit()

    stats = validators.validate_records(records, task_id)
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return {"task": task_to_dict(task), "stats": stats}


@app.get("/api/tasks")
def list_tasks(status: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict:
    sql = "SELECT * FROM tasks"
    params: tuple = ()
    if status:
        if status not in TASK_STATUSES:
            raise HTTPException(400, f"未知任务状态：{status}")
        sql += " WHERE status=?"
        params = (status,)
    sql += " ORDER BY id DESC LIMIT ?"
    with get_conn() as conn:
        rows = conn.execute(sql, (*params, limit)).fetchall()
    return {"tasks": [task_to_dict(r) for r in rows]}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int) -> dict:
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        retryable_count = conn.execute(
            "SELECT COUNT(*) c FROM rows WHERE task_id=? AND status='retryable'", (task_id,)
        ).fetchone()["c"]
    d = task_to_dict(task)
    d["retryable_rows"] = retryable_count
    return d


@app.post("/api/tasks/{task_id}/confirm")
def confirm_task(task_id: int) -> dict:
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] != "validated":
            raise HTTPException(409, f"当前状态为 {task['status']}，仅校验完成的任务可以确认上送")
        pending = conn.execute(
            "SELECT COUNT(*) c FROM rows WHERE task_id=? AND status='pending'", (task_id,)
        ).fetchone()["c"]
        if pending == 0:
            raise HTTPException(409, "没有可上送的有效行")
        conn.execute("UPDATE tasks SET status='uploading', error_summary='[]' WHERE id=?", (task_id,))
        conn.commit()

    enqueue_task(task_id)
    return {"ok": True, "message": f"已开始上送 {pending} 行"}


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: int) -> dict:
    """将可重试的行（平台维护/限流/网络异常）重新入队上送。"""
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] not in ("completed", "failed", "uploading"):
            raise HTTPException(409, "仅上送完成或失败的任务可以重试异常行")
        n = conn.execute(
            "UPDATE rows SET status='pending', platform_code=NULL, platform_message=NULL "
            "WHERE task_id=? AND status='retryable'",
            (task_id,),
        ).rowcount
        if n == 0:
            raise HTTPException(409, "当前没有可重试的行")
        conn.execute(
            "UPDATE tasks SET status='uploading', finished_at=NULL WHERE id=?", (task_id,)
        )
        conn.commit()
    enqueue_task(task_id)
    return {"ok": True, "message": f"已重新提交 {n} 行"}


# ---------------- 行明细 ----------------

@app.get("/api/tasks/{task_id}/rows")
def list_rows(
    task_id: int,
    status: str | None = None,
    category: str | None = Query(None, description="exception=所有异常行; duplicate=重复行"),
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> dict:
    where = ["task_id=?"]
    params: list = [task_id]
    if status:
        if status not in ROW_STATUSES:
            raise HTTPException(400, f"未知行状态：{status}")
        where.append("status=?")
        params.append(status)
    if category == "exception":
        where.append("status IN ('invalid','failed','retryable','duplicate')")
    elif category == "duplicate":
        where.append("is_duplicate=1")
    if q:
        where.append("(person_id LIKE ? OR item_name LIKE ? OR item_code LIKE ?)")
        like = f"%{q.strip()}%"
        params += [like, like, like]

    where_sql = " AND ".join(where)
    offset = (page - 1) * page_size
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM rows WHERE {where_sql}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM rows WHERE {where_sql} ORDER BY row_no LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [row_to_dict(r) for r in rows],
    }


# ---------------- 异常导出 ----------------

@app.get("/api/tasks/{task_id}/export")
def export_exceptions(task_id: int) -> StreamingResponse:
    with get_conn() as conn:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        rows = conn.execute(
            """SELECT * FROM rows WHERE task_id=?
               AND status IN ('invalid','failed','retryable','duplicate') ORDER BY row_no""",
            (task_id,),
        ).fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "异常行"
    ws.append(["Excel行号", "人员编号", "项目代码", "项目名称", "结果", "单位", "体检日期",
               "行状态", "问题类型代码", "问题说明", "平台返回码", "平台返回信息"])
    for r in rows:
        ws.append([
            r["row_no"], r["person_id"], r["item_code"], r["item_name"], r["result"],
            r["unit"], r["exam_date"], r["status"], r["error_codes"], r["error_messages"],
            r["platform_code"] or "", r["platform_message"] or "",
        ])
    for col, width in zip("ABCDEFGHIJKL", [9, 12, 10, 18, 10, 10, 12, 10, 16, 40, 12, 40]):
        ws.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"exceptions_task{task_id}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ---------------- 模板 / 项目目录 ----------------

@app.get("/api/template")
def template(sample: bool = False) -> StreamingResponse:
    wb = build_template_workbook(with_sample=sample)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = "template_with_sample.xlsx" if sample else "template.xlsx"
    encoded = quote("体检结果导入模板_含示例.xlsx" if sample else "体检结果导入模板.xlsx")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={name}; filename*=UTF-8''{encoded}"},
    )


@app.get("/api/items")
def items() -> dict:
    return {"items": ITEMS}


# ---------------- 静态前端（放在最后，避免吞掉 /api） ----------------
app.mount("/", StaticFiles(directory="static", html=True), name="static")
