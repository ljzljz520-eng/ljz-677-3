"""SQLite 持久化层：任务、逐行记录、去重指纹。"""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config
from . import statuses as st

_local = threading.local()
_init_lock = threading.Lock()

_TASK_FIELDS = {
    "status", "stage", "total_count", "success_count", "platform_error_count",
    "invalid_count", "duplicate_count", "pending_count", "error_message",
    "updated_at",
}


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                   TEXT PRIMARY KEY,
    filename             TEXT NOT NULL,
    stored_path          TEXT NOT NULL,
    status               TEXT NOT NULL,
    stage                TEXT DEFAULT '',
    total_count          INTEGER DEFAULT 0,
    success_count        INTEGER DEFAULT 0,
    platform_error_count INTEGER DEFAULT 0,
    invalid_count        INTEGER DEFAULT 0,
    duplicate_count      INTEGER DEFAULT 0,
    pending_count        INTEGER DEFAULT 0,
    error_message        TEXT DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_rows (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    row_no              INTEGER NOT NULL,
    person_id           TEXT DEFAULT '',
    item_code           TEXT DEFAULT '',
    item_name           TEXT DEFAULT '',
    result              TEXT DEFAULT '',
    unit                TEXT DEFAULT '',
    exam_date           TEXT DEFAULT '',
    status              TEXT NOT NULL,
    error_code          TEXT DEFAULT '',
    error_message       TEXT DEFAULT '',
    warning             TEXT DEFAULT '',
    fingerprint         TEXT DEFAULT '',
    platform_record_id  TEXT DEFAULT '',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE(task_id, row_no)
);
CREATE INDEX IF NOT EXISTS idx_rows_task ON task_rows(task_id);
CREATE INDEX IF NOT EXISTS idx_rows_status ON task_rows(task_id, status);

CREATE TABLE IF NOT EXISTS fingerprints (
    fingerprint TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    row_pk      INTEGER NOT NULL,
    person_id   TEXT DEFAULT '',
    item_code   TEXT DEFAULT '',
    exam_date   TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
"""


def init_db() -> None:
    with _init_lock:
        conn = get_db()
        conn.executescript(SCHEMA)
        conn.commit()


# ---------------- 任务 ----------------

def create_task(task_id: str, filename: str, stored_path: str) -> dict:
    ts = now_ts()
    conn = get_db()
    conn.execute(
        "INSERT INTO tasks (id, filename, stored_path, status, stage, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, filename, stored_path, st.TASK_PARSING, "解析校验", ts, ts),
    )
    conn.commit()
    return get_task(task_id)


def get_task(task_id: str):
    cur = get_db().execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_tasks(limit: int = 100):
    cur = get_db().execute(
        "SELECT * FROM tasks ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in cur.fetchall()]


def update_task(task_id: str, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _TASK_FIELDS}
    if not fields:
        return
    fields["updated_at"] = now_ts()
    sets = ", ".join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [task_id]
    conn = get_db()
    conn.execute(f"UPDATE tasks SET {sets} WHERE id=?", params)
    conn.commit()


def refresh_task_counts(task_id: str) -> None:
    """以逐行记录为准重算任务计数。"""
    conn = get_db()
    cur = conn.execute(
        "SELECT status, COUNT(*) c FROM task_rows WHERE task_id=? GROUP BY status",
        (task_id,),
    )
    counts = {r["status"]: r["c"] for r in cur.fetchall()}
    total = int(sum(counts.values()))
    conn.execute(
        "UPDATE tasks SET total_count=?, success_count=?, platform_error_count=?,"
        " invalid_count=?, duplicate_count=?, pending_count=?, updated_at=? WHERE id=?",
        (
            total,
            counts.get(st.SUCCESS, 0),
            counts.get(st.PLATFORM_ERROR, 0),
            counts.get(st.INVALID, 0),
            counts.get(st.DUPLICATE, 0),
            counts.get(st.PENDING, 0),
            now_ts(), task_id,
        ),
    )
    conn.commit()


# ---------------- 逐行记录 ----------------

_ROW_COLS = (
    "task_id", "row_no", "person_id", "item_code", "item_name", "result",
    "unit", "exam_date", "status", "error_code", "error_message",
    "warning", "fingerprint",
)


def insert_rows(task_id: str, rows: list[dict]) -> None:
    ts = now_ts()
    conn = get_db()
    placeholders = ", ".join(["?"] * (len(_ROW_COLS) + 2))
    sql = (
        f"INSERT INTO task_rows ({', '.join(_ROW_COLS)}, created_at, updated_at)"
        f" VALUES ({placeholders})"
    )
    params = [
        tuple([r.get(c, "") for c in _ROW_COLS] + [ts, ts]) for r in rows
    ]
    conn.executemany(sql, params)
    conn.commit()


def list_rows(task_id: str, status: str | None = None, limit: int = 500, offset: int = 0):
    conn = get_db()
    if status:
        sql = ("SELECT * FROM task_rows WHERE task_id=? AND status=?"
               " ORDER BY row_no LIMIT ? OFFSET ?")
        params = (task_id, status, limit, offset)
    else:
        sql = "SELECT * FROM task_rows WHERE task_id=? ORDER BY row_no LIMIT ? OFFSET ?"
        params = (task_id, limit, offset)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def count_rows(task_id: str, status: str | None = None) -> int:
    conn = get_db()
    if status:
        cur = conn.execute(
            "SELECT COUNT(*) c FROM task_rows WHERE task_id=? AND status=?",
            (task_id, status),
        )
    else:
        cur = conn.execute("SELECT COUNT(*) c FROM task_rows WHERE task_id=?", (task_id,))
    return cur.fetchone()["c"]


def get_pending_rows(task_id: str, limit: int = 100):
    cur = get_db().execute(
        "SELECT * FROM task_rows WHERE task_id=? AND status=? ORDER BY row_no LIMIT ?",
        (task_id, st.PENDING, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def get_row_by_pk(row_pk: int):
    cur = get_db().execute("SELECT * FROM task_rows WHERE id=?", (row_pk,))
    r = cur.fetchone()
    return dict(r) if r else None


def update_row(row_pk: int, **fields) -> None:
    allowed = {
        "status", "error_code", "error_message", "warning",
        "fingerprint", "platform_record_id",
    }
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    fields["updated_at"] = now_ts()
    sets = ", ".join(f"{k}=?" for k in fields)
    params = list(fields.values()) + [row_pk]
    conn = get_db()
    conn.execute(f"UPDATE task_rows SET {sets} WHERE id=?", params)
    conn.commit()


def reset_rows_for_resume(task_id: str) -> int:
    """平台异常行重新置为待上送，返回重置行数。"""
    conn = get_db()
    cur = conn.execute(
        "UPDATE task_rows SET status=?, error_code='', error_message='',"
        " platform_record_id='', updated_at=? WHERE task_id=? AND status=?",
        (st.PENDING, now_ts(), task_id, st.PLATFORM_ERROR),
    )
    conn.commit()
    return cur.rowcount


# ---------------- 去重指纹 ----------------

def claim_fingerprint(fp: str, task_id: str, row_pk: int,
                      person_id: str, item_code: str, exam_date: str) -> bool:
    """原子占用指纹；首次占用返回 True，已存在返回 False。"""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO fingerprints (fingerprint, task_id, row_pk, person_id,"
            " item_code, exam_date, created_at) VALUES (?,?,?,?,?,?,?)",
            (fp, task_id, row_pk, person_id, item_code, exam_date, now_ts()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        conn.rollback()
        return False


def get_fingerprint_owner(fp: str):
    cur = get_db().execute(
        "SELECT * FROM fingerprints WHERE fingerprint=?", (fp,)
    )
    r = cur.fetchone()
    return dict(r) if r else None


def release_pending_fingerprints(task_id: str) -> int:
    """任务取消时，释放本任务中尚未上送行占用的指纹。"""
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM fingerprints WHERE task_id=? AND row_pk IN"
        " (SELECT id FROM task_rows WHERE task_id=? AND status=?)",
        (task_id, task_id, st.PENDING),
    )
    conn.commit()
    return cur.rowcount
