"""SQLite 数据访问层。

- 每线程一个连接(threading.local), WAL 模式, 支持多线程读写
- 时间统一存本地时间字符串 "%Y-%m-%d %H:%M:%S"
"""
import os
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "checkin.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    name          TEXT NOT NULL,
    config        TEXT NOT NULL DEFAULT '{}',
    cron          TEXT NOT NULL,
    timeout       INTEGER NOT NULL DEFAULT 300,
    enabled       INTEGER NOT NULL DEFAULT 1,
    expires_info  TEXT,
    balance_info  TEXT,
    last_status   TEXT,
    last_message  TEXT,
    last_run_at   TEXT,
    next_run_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL,
    status       TEXT NOT NULL,
    trigger      TEXT NOT NULL DEFAULT 'auto',
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration     REAL,
    message      TEXT,
    output       TEXT
);

CREATE INDEX IF NOT EXISTS idx_logs_task ON logs(task_id);
CREATE INDEX IF NOT EXISTS idx_logs_started ON logs(started_at);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def get_db():
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def init_db():
    get_db().executescript(SCHEMA)
    _migrate()


def _migrate():
    """轻量迁移: 补齐后续版本新增的列(已存在则跳过)。"""
    cols = {r["name"] for r in query("PRAGMA table_info(tasks)")}
    if "expires_info" not in cols:
        execute("ALTER TABLE tasks ADD COLUMN expires_info TEXT")
    if "balance_info" not in cols:
        execute("ALTER TABLE tasks ADD COLUMN balance_info TEXT")


def query(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def query_one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def execute(sql, args=()):
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid
