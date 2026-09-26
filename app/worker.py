"""后台上送工作线程：逐行调用健康平台，实时更新任务进度。"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from collections import Counter

from .database import get_conn
from .platform import submit

# 平台返回码 -> 是否可重试
RETRYABLE = {"E5001", "E5002", "E5003"}

_task_queue: "queue.Queue[int]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def enqueue_task(task_id: int) -> None:
    _task_queue.put(task_id)


def _record_platform(conn: sqlite3.Connection, row: sqlite3.Row, reference_no: str) -> bool:
    """落库平台接收记录。返回 False 表示平台已存在（E3001）。"""
    try:
        conn.execute(
            """INSERT INTO platform_records (person_id,item_code,exam_date,task_id,row_id,reference_no)
               VALUES (?,?,?,?,?,?)""",
            (row["person_id"], row["item_code"], row["exam_date"],
             row["task_id"], row["id"], reference_no),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def _process_task(task_id: int) -> None:
    from .platform import reset_rate_limits

    reset_rate_limits()
    # 动态延迟：让大批量也能在约 10 秒内跑完
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM rows WHERE task_id=? AND status='pending'",
                             (task_id,)).fetchone()["c"]
    per_row_delay = 0.0 if total > 300 else (0.05 if total > 60 else 0.12)

    conn = get_conn()
    conn.execute("UPDATE tasks SET status='uploading' WHERE id=?", (task_id,))
    conn.commit()

    processed = success = failed = 0
    summary: Counter[str] = Counter()
    # 重试场景下，以 rows 表当前真实状态为基数，避免把上一轮计数重复累加
    base = conn.execute(
        """SELECT
             SUM(status='success') s,
             SUM(status IN ('failed','retryable')) f,
             SUM(status IN ('success','failed','retryable')) p
           FROM rows WHERE task_id=?""",
        (task_id,),
    ).fetchone()
    success = base["s"] or 0
    failed = base["f"] or 0
    processed = base["p"] or 0
    for code, cnt in conn.execute(
        "SELECT platform_code c, COUNT(*) n FROM rows WHERE task_id=? "
        "AND platform_code IS NOT NULL AND status IN ('failed','retryable') GROUP BY platform_code",
        (task_id,),
    ):
        summary[code] += cnt
    try:
        while True:
            row = conn.execute(
                "SELECT * FROM rows WHERE task_id=? AND status='pending' ORDER BY row_no LIMIT 1",
                (task_id,),
            ).fetchone()
            if row is None:
                break

            resp = submit(row["person_id"], row["item_code"], row["result_num"] or 0.0,
                          row["unit"], row["exam_date"])

            if resp.success:
                reference_no = f"HX{task_id:04d}{row['id']:06d}"
                accepted = _record_platform(conn, row, reference_no)
                if accepted:
                    conn.execute(
                        """UPDATE rows SET status='success', platform_code='S0000',
                           platform_message=?, reference_no=? WHERE id=?""",
                        (resp.message, reference_no, row["id"]),
                    )
                    success += 1
                else:
                    conn.execute(
                        """UPDATE rows SET status='failed', is_duplicate=1, platform_code='E3001',
                           platform_message=? WHERE id=?""",
                        ("平台已存在相同记录（人员+项目+体检日期），重复上送被拒绝", row["id"]),
                    )
                    failed += 1
                    summary["E3001"] += 1
            else:
                summary[resp.code] += 1
                conn.execute(
                    """UPDATE rows SET status=?, platform_code=?, platform_message=? WHERE id=?""",
                    ("retryable" if resp.code in RETRYABLE else "failed",
                     resp.code, resp.message, row["id"]),
                )
                failed += 1

            processed += 1
            conn.execute(
                """UPDATE tasks SET processed_rows=?, success_rows=?, failed_rows=?,
                   error_summary=? WHERE id=?""",
                (processed, success, failed, json.dumps(_summary_list(summary), ensure_ascii=False),
                 task_id),
            )
            conn.commit()
            if per_row_delay:
                time.sleep(per_row_delay)

        conn.execute(
            """UPDATE tasks SET status='completed', finished_at=datetime('now','localtime'),
               error_summary=? WHERE id=?""",
            (json.dumps(_summary_list(summary), ensure_ascii=False), task_id),
        )
        conn.commit()
    except Exception as e:  # 工作线程不能静默死掉
        conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (task_id,))
        conn.commit()
        print(f"[worker] task {task_id} crashed: {e!r}")
    finally:
        conn.close()


def _summary_list(counter: Counter) -> list[dict]:
    from .validators import PLATFORM_LABELS

    return [
        {"code": code, "count": cnt, "message": PLATFORM_LABELS.get(code, code)}
        for code, cnt in counter.most_common()
    ]


def _worker_loop() -> None:
    while True:
        task_id = _task_queue.get()
        try:
            _process_task(task_id)
        finally:
            _task_queue.task_done()


def start_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker_loop, name="upload-worker", daemon=True)
        t.start()
        _worker_started = True
