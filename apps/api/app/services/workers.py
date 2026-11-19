"""Worker liveness from heartbeats in the database."""
from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import AGENT_QUEUES, get_settings
from app.db import session_scope
from app.models import WorkerHeartbeat, utcnow

log = logging.getLogger("mcr.worker")

BEAT_SECONDS = 20
STALE_AFTER = timedelta(seconds=75)  # three missed beats


def beat(name: str, queues: list[str]) -> None:
    with session_scope() as session:
        row = session.get(WorkerHeartbeat, name)
        if row is None:
            session.add(WorkerHeartbeat(name=name, queues=queues, last_seen=utcnow()))
        else:
            row.queues = queues
            row.last_seen = utcnow()
        # tidy rows of workers gone for a day
        session.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen < utcnow() - timedelta(days=1)))


def start_heartbeat_thread(queues: list[str]) -> threading.Thread:
    name = f"{socket.gethostname()}:{os.getpid()}"
    stop = threading.Event()

    def run() -> None:
        while not stop.is_set():
            try:
                beat(name, queues)
            except Exception as exc:  # noqa: BLE001 - liveness reporting must never kill the worker
                log.warning("worker heartbeat failed: %s", exc)
            stop.wait(BEAT_SECONDS)

    thread = threading.Thread(target=run, name="mcr-heartbeat", daemon=True)
    thread.start()
    return thread


def queue_status(session: Session) -> dict[str, int]:
    """Live workers per agent queue (heartbeat within the last 75 s)."""
    counts = dict.fromkeys(AGENT_QUEUES, 0)
    if get_settings().celery_task_always_eager:  # tests / eager mode: tasks run inline
        return dict.fromkeys(AGENT_QUEUES, 1)
    rows = session.scalars(select(WorkerHeartbeat).where(WorkerHeartbeat.last_seen >= utcnow() - STALE_AFTER))
    for row in rows:
        for q in row.queues or []:
            if q in counts:
                counts[q] += 1
    return counts
