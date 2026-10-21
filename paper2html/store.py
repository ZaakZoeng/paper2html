"""Local job index and atomic duplicate claims; credentials never enter the index."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root, reports_dir=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.reports = Path(reports_dir).resolve() if reports_dir is not None else self.root / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS claims (key TEXT PRIMARY KEY, job_id TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        with self.lock, sqlite3.connect(self.root / "jobs.sqlite3", timeout=30) as db:
            yield db

    def create(self, job_id, filename, model):
        payload = {"id": job_id, "filename": filename, "model": model,
                   "status": "queued", "stage": "等待处理", "created_at": now(), "updated_at": now()}
        with self.db() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?)", (job_id, json.dumps(payload, ensure_ascii=False)))
        return payload

    def get(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update(self, job_id, **changes):
        with self.db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
            payload = json.loads(row[0])
            payload.update(changes, updated_at=now())
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), job_id))
        return payload

    def claim(self, key, job_id):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO claims VALUES (?, ?)", (key, job_id))
            owner = db.execute("SELECT job_id FROM claims WHERE key=?", (key,)).fetchone()[0]
        return None if owner == job_id else owner

    def release(self, job_id):
        with self.db() as db:
            db.execute("DELETE FROM claims WHERE job_id=?", (job_id,))

    def recover(self):
        # API keys are intentionally ephemeral. An interrupted job needs resubmission.
        with self.db() as db:
            rows = db.execute("SELECT id, payload FROM jobs").fetchall()
        for job_id, raw in rows:
            if json.loads(raw)["status"] in ("queued", "running"):
                self.update(job_id, status="failed", stage="服务重启，任务中断", error="请重新提交 PDF 和 API 配置")
                self.release(job_id)
