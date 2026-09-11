"""HTTP 接口：上传、任务查询、逐行异常、导出、取消/重试。"""
import os
import uuid
from urllib.parse import quote

from flask import Blueprint, jsonify, render_template, request, send_file

from . import config, db, excel_io
from . import statuses as st
from .processor import manager

bp = Blueprint("api", __name__)


# ---------------- 页面 ----------------

@bp.get("/")
def index():
    return render_template("index.html")


# ---------------- 任务 ----------------

@bp.post("/api/tasks")
def create_task():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "请选择要导入的 Excel 文件"}), 400
    ext = os.path.splitext(upload.filename)[1].lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        return jsonify({"error": "仅支持 .xlsx 格式，请另存后重新上传"}), 400

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{ext}"
    stored_path = str(config.UPLOAD_DIR / stored)
    upload.save(stored_path)

    # 提前做一次可读性校验，给上传请求同步反馈
    try:
        excel_io.read_excel_rows(stored_path)
    except excel_io.ExcelFormatError as exc:
        try:
            os.remove(stored_path)
        except OSError:
            pass
        return jsonify({"error": str(exc)}), 400

    task_id = manager.create_and_start(upload.filename, stored_path)
    return jsonify({"task_id": task_id}), 201


@bp.get("/api/tasks")
def get_tasks():
    return jsonify({"tasks": manager_serialize_tasks(db.list_tasks())})


@bp.get("/api/tasks/<task_id>")
def get_task(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify({"task": serialize_task(task)})


@bp.get("/api/tasks/<task_id>/rows")
def get_task_rows(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    status = request.args.get("status") or None
    if status and status not in st.ROW_STATUS:
        return jsonify({"error": f"不支持的行状态：{status}"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 500)), 5000))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return jsonify({"error": "分页参数必须为整数"}), 400

    total = db.count_rows(task_id, status)
    rows = db.list_rows(task_id, status, limit=limit, offset=offset)
    return jsonify({
        "total": total, "limit": limit, "offset": offset,
        "rows": [serialize_row(r) for r in rows],
    })


@bp.post("/api/tasks/<task_id>/cancel")
def cancel_task(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    if not manager.cancel(task_id):
        return jsonify({"error": "当前任务状态不允许取消（仅校验中/上送中可取消）"}), 409
    return jsonify({"ok": True})


@bp.post("/api/tasks/<task_id>/resume")
def resume_task(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    if not manager.resume(task_id):
        return jsonify({
            "error": "没有可重上送的平台异常行（仅平台异常可重试；校验失败请修改后重新导入）"
        }), 409
    return jsonify({"ok": True})


@bp.get("/api/tasks/<task_id>/errors.xlsx")
def export_errors(task_id):
    task = db.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    rows = db.list_rows(
        task_id, None,
        limit=1_000_000,
    )
    problem = [
        r for r in rows
        if r["status"] in (st.INVALID, st.PLATFORM_ERROR, st.DUPLICATE)
    ]
    buf = excel_io.build_error_workbook(task, problem)
    filename = f"异常明细_{task_id}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.get("/api/template")
def download_template():
    buf = excel_io.build_template_workbook()
    filename = "体检结果导入模板.xlsx"
    rv = send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    # 显式按 RFC 5987 编码中文文件名，保证各浏览器一致
    rv.headers["Content-Disposition"] = (
        "attachment; filename*=UTF-8''" + quote(filename)
    )
    return rv


@bp.get("/api/catalog")
def get_catalog():
    from .catalog import catalog_rows
    return jsonify({"items": catalog_rows()})


# ---------------- 序列化 ----------------

def serialize_task(task: dict) -> dict:
    t = dict(task)
    t["status_text"] = st.TASK_STATUS.get(task["status"], task["status"])
    t["row_status_text"] = st.ROW_STATUS
    t["active"] = task["status"] in st.ACTIVE_TASK_STATUS
    total = task["total_count"] or 0
    t["progress_pct"] = round(
        (task["success_count"] + task["platform_error_count"]
         + task["invalid_count"] + task["duplicate_count"]) * 100 / total, 1
    ) if total else 0.0
    return t


def manager_serialize_tasks(tasks):
    return [serialize_task(t) for t in tasks]


def serialize_row(row: dict) -> dict:
    r = dict(row)
    r["status_text"] = st.ROW_STATUS.get(row["status"], row["status"])
    return r


# ---------------- 错误处理 ----------------

@bp.app_errorhandler(413)
def too_large(_exc):
    return jsonify({"error": f"文件过大，单文件上限 {config.MAX_CONTENT_LENGTH // (1024 * 1024)}MB"}), 413


@bp.app_errorhandler(500)
def internal_error(exc):
    return jsonify({"error": f"服务器内部错误：{exc}"}), 500
