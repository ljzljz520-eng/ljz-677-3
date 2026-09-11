"""任务处理引擎：解析 Excel -> 逐行校验 -> 指纹去重 -> 限流上送平台。"""
import sqlite3
import threading
import time
import traceback
import uuid

from . import db
from . import excel_io
from . import platform as platform_mod
from . import statuses as st
from . import validator

client = platform_mod.client
_progress_lock = threading.Lock()  # 任务计数刷新串行化，避免 SQLite 写竞争


class TaskManager:
    def __init__(self):
        # 每个任务独立的取消信号，避免并发任务互相干扰
        self._cancel_events: dict[str, threading.Event] = {}
        self._events_lock = threading.Lock()

    def _event(self, task_id: str) -> threading.Event:
        with self._events_lock:
            ev = self._cancel_events.get(task_id)
            if ev is None:
                ev = threading.Event()
                self._cancel_events[task_id] = ev
            return ev

    # ---------------- 生命周期 ----------------

    def new_task_id(self) -> str:
        return "T" + time.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:8]

    def create_and_start(self, filename: str, stored_path: str) -> str:
        task_id = self.new_task_id()
        db.create_task(task_id, filename, stored_path)
        t = threading.Thread(
            target=self._safe_run, args=(task_id, False), name=f"task-{task_id}", daemon=True
        )
        t.start()
        return task_id

    def cancel(self, task_id: str) -> bool:
        task = db.get_task(task_id)
        if not task:
            return False
        if task["status"] in st.ACTIVE_TASK_STATUS:
            db.update_task(task_id, status=st.TASK_CANCELLED)
            self._event(task_id).set()
            return True
        return False

    def resume(self, task_id: str) -> bool:
        task = db.get_task(task_id)
        if not task:
            return False
        if task["status"] not in (st.TASK_COMPLETED_WITH_ERRORS, st.TASK_CANCELLED, st.TASK_FAILED):
            return False
        # 仅平台异常行参与重上送；校验失败 / 重复行保持原结论
        reset = db.reset_rows_for_resume(task_id)
        db.refresh_task_counts(task_id)
        if reset == 0 and task["status"] != st.TASK_CANCELLED:
            # 没有可重上送的平台异常行
            return False
        self._event(task_id).clear()
        db.update_task(task_id, status=st.TASK_UPLOADING, stage="平台上送", error_message="")
        t = threading.Thread(
            target=self._safe_run, args=(task_id, True), name=f"resume-{task_id}", daemon=True
        )
        t.start()
        return True

    # ---------------- 主流程 ----------------

    def _safe_run(self, task_id: str, resumed: bool) -> None:
        try:
            self._run(task_id, resumed)
        except Exception as exc:
            traceback.print_exc()
            try:
                db.update_task(
                    task_id, status=st.TASK_FAILED, stage="处理失败",
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass

    def _run(self, task_id: str, resumed: bool) -> None:
        if not resumed:
            self._parse_and_validate(task_id)
        if self._is_cancelled(task_id):
            self._finalize_cancelled(task_id)
            return
        self._upload_pending(task_id)
        if self._is_cancelled(task_id):
            self._finalize_cancelled(task_id)
            return
        self._finalize_done(task_id)

    def _is_cancelled(self, task_id: str) -> bool:
        return self._event(task_id).is_set() or db.get_task(task_id)["status"] == st.TASK_CANCELLED

    # ---------------- 阶段一：解析 + 校验 + 去重 ----------------

    def _parse_and_validate(self, task_id: str) -> None:
        db.update_task(task_id, status=st.TASK_PARSING, stage="解析校验")
        task = db.get_task(task_id)
        _, raw_rows = excel_io.read_excel_rows(task["stored_path"])

        prepared: list[dict] = []
        for raw in raw_rows:
            if self._is_cancelled(task_id):
                break
            v = validator.validate_row(raw)
            base = {
                "task_id": task_id,
                "row_no": raw["row_no"],
                "person_id": v["person_id"],
                "item_code": v["item_code"],
                "item_name": v["item_name"],
                "result": v["result"],
                "unit": v["unit"],
                "exam_date": v["exam_date"],
                "fingerprint": v["fingerprint"],
            }
            if v["errors"]:
                base.update({
                    "status": st.INVALID,
                    "error_code": "VALIDATION_ERROR",
                    "error_message": "；".join(v["errors"]),
                    "warning": "；".join(v["warnings"]),
                })
            else:
                base.update({
                    "status": st.PENDING,
                    "warning": "；".join(v["warnings"]),
                })
            prepared.append(base)

        # 先落库无效行，有效行落库后再占指纹（需要 row_pk）
        db.insert_rows(task_id, [r for r in prepared if r["status"] == st.INVALID])
        valid = [r for r in prepared if r["status"] == st.PENDING]
        db.insert_rows(task_id, valid)

        # 定位有效行的自增主键
        saved = {r["row_no"]: r for r in db.list_rows(task_id, st.PENDING, limit=10_000_000)}
        in_file = {}  # fingerprint -> 首次出现的行号
        for r in valid:
            if self._is_cancelled(task_id):
                break
            with _progress_lock:
                row_pk = saved[r["row_no"]]["id"]
                fp = r["fingerprint"]
                if fp in in_file:
                    db.update_row(
                        row_pk, status=st.DUPLICATE, error_code="DUPLICATE_IN_FILE",
                        error_message=f"与本文件第 {in_file[fp]} 行重复（同一人员、项目、体检日期）",
                    )
                    continue
                claimed = self._claim_with_retry(fp, task_id, row_pk, r)
                if not claimed:
                    continue  # 行状态已由 _claim_with_retry 置为 DUPLICATE
                in_file[fp] = r["row_no"]

        with _progress_lock:
            db.refresh_task_counts(task_id)

    def _claim_with_retry(self, fp: str, task_id: str, row_pk: int, row: dict) -> bool:
        """占用指纹；与其他任务竞争时重试一次后标记重复。"""
        try:
            ok = db.claim_fingerprint(
                fp, task_id, row_pk, row["person_id"], row["item_code"], row["exam_date"]
            )
        except sqlite3.OperationalError:
            time.sleep(0.2)
            ok = db.claim_fingerprint(
                fp, task_id, row_pk, row["person_id"], row["item_code"], row["exam_date"]
            )
        if ok:
            return True
        owner = db.get_fingerprint_owner(fp)
        reason = self._dup_reason(owner, task_id, row)
        db.update_row(
            row_pk, status=st.DUPLICATE, error_code="DUPLICATE_CROSS_FILE",
            error_message=reason,
        )
        return False

    def _dup_reason(self, owner, task_id: str, row: dict) -> str:
        if not owner:
            return "重复数据（指纹已被占用）"
        owner_task = db.get_task(owner["task_id"])
        owner_row = db.get_row_by_pk(owner["row_pk"])
        same_task = owner["task_id"] == task_id
        prefix = "与本任务" if same_task else "与历史导入"
        filename = owner_task["filename"] if owner_task else owner["task_id"]
        detail = f"{prefix}第 {owner_row['row_no']} 行重复（源文件：{filename}）"
        # 若指纹相同但结果值不同，说明同人同项目同天录了不同结果，值得提示
        if owner_row and owner_row["result"] not in ("", row["result"]):
            detail += f"；注意：已存在结果为 {owner_row['result']} {owner_row['unit']}"
        return detail

    # ---------------- 阶段二：限流上送 ----------------

    def _upload_pending(self, task_id: str) -> None:
        db.update_task(task_id, status=st.TASK_UPLOADING, stage="平台上送")
        while True:
            if self._is_cancelled(task_id):
                return
            batch = db.get_pending_rows(task_id, limit=20)
            if not batch:
                return
            for row in batch:
                # 发送前最后一道取消检查；一旦发出，无论是否收到取消信号都等响应并回写，
                # 保证平台已受理的数据不会在本地“悬空”为待上送。
                if self._is_cancelled(task_id):
                    return
                try:
                    resp = client.upload(
                        {
                            "fingerprint": row["fingerprint"],
                            "person_id": row["person_id"],
                            "item_code": row["item_code"],
                            "exam_date": row["exam_date"],
                            "result": row["result"],
                        }
                    )
                    with _progress_lock:
                        db.update_row(
                            row["id"], status=st.SUCCESS,
                            platform_record_id=resp["record_id"],
                            error_code="", error_message="",
                        )
                        db.refresh_task_counts(task_id)
                except platform_mod.PlatformError as exc:
                    self._handle_platform_error(task_id, row, exc)

    def _handle_platform_error(self, task_id: str, row: dict,
                               exc: platform_mod.PlatformError) -> None:
        """平台报错分情况处理：幂等冲突需对账（可能是取消前已受理）。"""
        with _progress_lock:
            if exc.code == "DUPLICATE_SUBMISSION":
                record_id = client.lookup(row["fingerprint"])
                if record_id:
                    # 平台侧确有受理记录：按成功对账，并保留平台记录号
                    db.update_row(
                        row["id"], status=st.SUCCESS,
                        platform_record_id=record_id,
                        error_code="", error_message="",
                    )
                    db.refresh_task_counts(task_id)
                    return
            db.update_row(
                row["id"], status=st.PLATFORM_ERROR,
                error_code=exc.code, error_message=exc.message,
            )
            db.refresh_task_counts(task_id)

    # ---------------- 收尾 ----------------

    def _finalize_done(self, task_id: str) -> None:
        with _progress_lock:
            db.refresh_task_counts(task_id)
        task = db.get_task(task_id)
        if task["platform_error_count"] or task["invalid_count"] or task["duplicate_count"]:
            status = st.TASK_COMPLETED_WITH_ERRORS
        else:
            status = st.TASK_COMPLETED
        db.update_task(task_id, status=status, stage="已完成")

    def _finalize_cancelled(self, task_id: str) -> None:
        with _progress_lock:
            # 与平台对账：取消前可能已有在途请求被平台受理
            reconciled = self._reconcile_inflight(task_id)
            # 对账后仍是待上送的行，才释放其指纹占用
            released = db.release_pending_fingerprints(task_id)
            db.refresh_task_counts(task_id)
        task = db.get_task(task_id)
        note = []
        if reconciled:
            note.append(f"对账确认 {reconciled} 条平台已受理")
        if released:
            note.append(f"已释放 {released} 条待上送占用")
        extra = ("；" + "，".join(note)) if note else ""
        db.update_task(
            task_id, status=st.TASK_CANCELLED, stage="已取消",
            error_message=(task["error_message"] or "") + extra,
        )

    def _reconcile_inflight(self, task_id: str) -> int:
        """取消收尾时，将平台已受理但本地仍为待上送的行回写为成功。"""
        count = 0
        for row in db.get_pending_rows(task_id, limit=100_000):
            record_id = client.lookup(row["fingerprint"])
            if record_id:
                db.update_row(
                    row["id"], status=st.SUCCESS,
                    platform_record_id=record_id,
                    error_code="", error_message="",
                )
                count += 1
        if count:
            db.refresh_task_counts(task_id)
        return count


manager = TaskManager()
