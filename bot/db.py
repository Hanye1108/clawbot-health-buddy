"""SQLite 数据库：一个文件存全部数据，便于备份。"""
import datetime
import os
import sqlite3
from contextlib import closing

from . import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS profiles (
        user_id TEXT PRIMARY KEY, nickname TEXT, sex TEXT, age INTEGER,
        height_cm REAL, weight_kg REAL, goal_weight_kg REAL,
        activity TEXT, city TEXT, province TEXT, updated_at TEXT,
        reminders INTEGER DEFAULT 1, weigh_reminder INTEGER DEFAULT 1)""",
    """CREATE TABLE IF NOT EXISTS chat (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
        role TEXT, content TEXT, ts TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_chat ON chat(user_id, id)",
    """CREATE TABLE IF NOT EXISTS meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, day TEXT,
        meal TEXT, description TEXT, kcal INTEGER, ts TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_meals ON meals(user_id, day)",
    """CREATE TABLE IF NOT EXISTS exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, day TEXT,
        kind TEXT, description TEXT, minutes INTEGER, kcal INTEGER,
        source TEXT, ts TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_ex ON exercises(user_id, day)",
    """CREATE TABLE IF NOT EXISTS weights (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, day TEXT,
        kg REAL, ts TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_w ON weights(user_id, day)",
    """CREATE TABLE IF NOT EXISTS pending (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, text TEXT,
        ts TEXT, expires_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS ilink_sessions (
        account_id TEXT, user_id TEXT, context_token TEXT, updated_at TEXT,
        PRIMARY KEY (account_id, user_id))""",
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)",
]

USER_TABLES = ("profiles", "chat", "meals", "exercises", "weights", "pending", "ilink_sessions")


def run(sql, args=(), fetch=False):
    with closing(sqlite3.connect(str(config.DB_PATH), timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, args)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows


def init_db():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    for sql in SCHEMA:
        run(sql)
    try:
        os.chmod(config.DB_PATH, 0o600)
    except OSError:
        pass


def kv_get(key, default=None):
    rows = run("SELECT value FROM kv WHERE key=?", (key,), fetch=True)
    return rows[0]["value"] if rows else default


def kv_set(key, value):
    run("INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def now():
    return datetime.datetime.now(config.TZ)


def now_iso():
    return now().isoformat()


def today_str():
    return now().strftime("%Y-%m-%d")
