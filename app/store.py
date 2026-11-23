"""Verdicts + feedback. Redacted evidence only, 90-day TTL."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .config import DB_PATH, EVIDENCE_TTL_DAYS


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS verdicts ("
        "id TEXT PRIMARY KEY, band TEXT, signals TEXT, quote TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS feedback ("
        "id INTEGER PRIMARY KEY, verdict_id TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    return conn


@contextmanager
def _db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EVIDENCE_TTL_DAYS)).isoformat()
    with _db() as conn:
        conn.execute("DELETE FROM verdicts WHERE created_at < ?", (cutoff,))


def save(band: str, signals: list[dict], quote: str) -> str:
    vid = uuid4().hex[:12]
    with _db() as conn:
        conn.execute(
            "INSERT INTO verdicts (id, band, signals, quote, created_at) VALUES (?,?,?,?,?)",
            (vid, band, json.dumps(signals), quote, datetime.now(timezone.utc).isoformat()),
        )
    return vid


def get(vid: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, band, signals, quote, created_at FROM verdicts WHERE id = ?", (vid,)
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "band": row[1], "signals": json.loads(row[2]),
        "quote": row[3], "created_at": row[4],
    }


def add_feedback(vid: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO feedback (verdict_id, created_at) VALUES (?,?)",
            (vid, datetime.now(timezone.utc).isoformat()),
        )


def get_offset() -> int | None:
    """Telegram update offset. Persisted so a restart does not replay old messages.

    None means "no offset stored yet" (fresh or wiped database). Callers should
    then start from the newest pending update instead of oldest, otherwise a
    redeploy replays a whole day of already-answered forwards.
    """
    with _db() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'offset'").fetchone()
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def set_offset(offset: int) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('offset', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(offset),),
        )
