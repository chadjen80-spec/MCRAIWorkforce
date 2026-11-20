"""Celery application: one dedicated queue per agent + a maintenance queue for the reaper."""
from __future__ import annotations

import logging
import uuid

from celery import Celery
from celery.signals import setup_logging, worker_ready
from kombu import Queue

from app.config import AGENT_QUEUES, MAINTENANCE_QUEUE, get_settings
from app.logging_config import configure_logging

settings = get_settings()

celery_app = Celery("mcr_ai_workforce", broker=settings.celery_broker_url)

_transport_options: dict = {"visibility_timeout": 3600}
if settings.celery_broker_url.startswith("filesystem"):
    # Single-machine development broker (no Redis needed). Not for production.
    broker_dir = (settings.data_dir / "broker").resolve()
    for sub in ("queue", "control"):
        (broker_dir / sub).mkdir(parents=True, exist_ok=True)
    _transport_options = {
        "data_folder_in": str(broker_dir / "queue"),
        "data_folder_out": str(broker_dir / "queue"),
        "control_folder": str(broker_dir / "control"),
    }

celery_app.conf.update(
    task_queues=[Queue(q) for q in (*AGENT_QUEUES, MAINTENANCE_QUEUE)],
    task_default_queue=MAINTENANCE_QUEUE,
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Reliability: a message is acknowledged only after the task function returns, and is
    # re-queued if the worker process dies. Task state itself lives in PostgreSQL.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options=_transport_options,
    task_soft_time_limit=1500,
    task_time_limit=1800,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=False,
    worker_hijack_root_logger=False,
    beat_schedule={
        "reap-expired-leases": {"task": "mcr.reap", "schedule": 30.0, "options": {"queue": MAINTENANCE_QUEUE}},
        "qtc-deliver-events": {"task": "mcr.qtc_deliver_events", "schedule": 15.0, "options": {"queue": MAINTENANCE_QUEUE}},
        "qtc-scan": {"task": "mcr.qtc_scan", "schedule": 3600.0, "options": {"queue": MAINTENANCE_QUEUE}},
    },
)


@setup_logging.connect
def _setup_logging(**_kwargs) -> None:
    configure_logging()


log = logging.getLogger("mcr.worker")


@worker_ready.connect
def _start_heartbeat(sender=None, **_kwargs) -> None:
    """Report liveness to the database every 20 s (the dashboard warns when no worker is alive)."""
    from app.services.workers import start_heartbeat_thread

    queues: list[str] = []
    try:
        queues = [q.name for q in sender.consumer.task_consumer.queues]  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        queues = [*AGENT_QUEUES, MAINTENANCE_QUEUE]
    start_heartbeat_thread(queues)
    log.info("worker heartbeat started for queues %s", ",".join(queues))


@celery_app.task(name="mcr.run_task", bind=True, max_retries=0)
def run_task(self, task_id: str) -> None:
    from app.worker.runner import execute_task

    execute_task(uuid.UUID(task_id))


@celery_app.task(name="mcr.regenerate_artifact", bind=True, max_retries=0)
def regenerate_artifact_task(self, artifact_id: str, guidance: str | None, actor: str) -> None:
    from app.worker.regenerate import regenerate_artifact

    regenerate_artifact(uuid.UUID(artifact_id), guidance, actor)


@celery_app.task(name="mcr.regenerate_image", bind=True, max_retries=0)
def regenerate_image_task(self, artifact_id: str, concept: str | None, actor: str) -> None:
    from app.worker.regenerate import regenerate_image

    regenerate_image(uuid.UUID(artifact_id), concept, actor)


@celery_app.task(name="mcr.rerun_qa", bind=True, max_retries=0)
def rerun_qa_task(self, job_id: str, actor: str) -> None:
    from app.worker.regenerate import rerun_qa_for_job

    rerun_qa_for_job(uuid.UUID(job_id), actor)


@celery_app.task(name="mcr.reap")
def reap() -> dict:
    from app.db import new_session
    from app.services.orchestrator import reap_expired

    session = new_session()
    try:
        stats = reap_expired(session)
        if any(stats.values()):
            log.info("reaper recovered tasks", extra={"stats": stats})
        return stats
    finally:
        session.close()


@celery_app.task(name="mcr.qtc_deliver_events")
def qtc_deliver_events() -> dict:
    """Send pending Quote-to-Cash events to n8n as signed webhooks (no-op until QTC_EVENTS_URL/SECRET are set)."""
    from app.db import new_session
    from app.qtc.events import deliver_pending

    session = new_session()
    try:
        return deliver_pending(session)
    finally:
        session.close()


@celery_app.task(name="mcr.qtc_scan")
def qtc_scan() -> dict:
    """Emit invoice due-soon / overdue events and the weekly receivables summary (idempotent)."""
    from app.db import new_session
    from app.qtc.events import scan

    session = new_session()
    try:
        return scan(session)
    finally:
        session.close()
