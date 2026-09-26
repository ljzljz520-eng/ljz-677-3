"""SQLite 存储：任务表、明细行表、平台已接收记录表。

- tasks：一次导入 = 一个任务
- rows：Excel 每一行的校验 / 上送状态
- platform_records：健康平台已成功接收的记录（用于跨任务重复识别）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "app.db"

_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'uploaded',
    total_rows      INTEGER NOT NULL DEFAULT 0,
    valid_rows      INTEGER NOT NULL DEFAULT 0,
    invalid_rows    INTEGER NOT NULL DEFAULT 0,
    duplicate_rows  INTEGER NOT NULL DEFAULT 0,
    success_rows    INTEGER NOT NULL DEFAULT 0,
    failed_rows     INTEGER NOT NULL DEFAULT 0,
    processed_rows  INTEGER NOT NULL DEFAULT 0,
    error_summary   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    validated_at    TEXT,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS rows (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    row_no            INTEGER NOT NULL,
    person_id         TEXT,
    item_code         TEXT,
    item_name         TEXT,
    result            TEXT,
    result_num        REAL,
    unit              TEXT,
    exam_date         TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    is_duplicate      INTEGER NOT NULL DEFAULT 0,
    error_codes       TEXT NOT NULL DEFAULT '',
    error_messages    TEXT NOT NULL DEFAULT '',
    platform_code     TEXT,
    platform_message  TEXT,
    reference_no      TEXT,
    UNIQUE(task_id, row_no)
);

CREATE INDEX IF NOT EXISTS idx_rows_task ON rows(task_id, status);

CREATE TABLE IF NOT EXISTS platform_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id   TEXT NOT NULL,
    item_code   TEXT NOT NULL,
    exam_date   TEXT NOT NULL,
    task_id     INTEGER,
    row_id      INTEGER,
    reference_no TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(person_id, item_code, exam_date)
);
"""


def init_db() -> None:
    with _lock, get_conn() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


# ---- 行状态 / 任务状态字典化 ----

def task_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    # 进度仅针对需要上送的有效行（校验失败/重复行不参与上送）
    denominator = d.get("valid_rows") or 0
    if d["status"] == "completed":
        pct = 100.0
    else:
        pct = round(d["processed_rows"] * 100 / denominator, 1) if denominator else 0.0
    d["progress_pct"] = min(pct, 100.0)
    try:
        d["error_summary"] = json.loads(d.get("error_summary") or "[]")
    except json.JSONDecodeError:
        d["error_summary"] = []
    return d


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["is_duplicate"] = bool(d["is_duplicate"])
    d["error_codes"] = [c for c in (d.get("error_codes") or "").split(",") if c]
    return d
