"""Concurrency tests: many threads, each with its own database session, racing on the same rows.

They run on SQLite by default and on PostgreSQL in CI (TEST_DATABASE_URL), where the row locks used by
the claim and reservation code are real.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import new_session, session_scope
from app.errors import AgentError
from app.models import AuditLog, Job, Organization, Task, TaskStatus, utcnow
from app.services import budget, orchestrator
from tests.conftest import make_org

THREADS = 12


def _race(fn, n: int = THREADS) -> list:
    """Run fn(i) on n threads released at the same moment; returns results (exceptions included)."""
    gate = threading.Barrier(n)

    def run(i):
        gate.wait()
        try:
            return fn(i)
        except Exception as exc:  # noqa: BLE001 - the test inspects what each thread got
            return exc

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(run, range(n)))


def _job_with_task(org_id: uuid.UUID, *, status: str = TaskStatus.QUEUED, budget_usd: str = "5",
                   max_attempts: int = 3) -> tuple[uuid.UUID, uuid.UUID]:
    with session_scope() as s:
        org = s.get(Organization, org_id)
        job, _ = orchestrator.create_job(
            s, org, command="concurrency test job", agents=["content"], budget_limit_usd=Decimal(budget_usd),
            language="en", aspect_ratio="16:9", require_approval=False, data_classification="internal",
            project=orchestrator.default_project(s, org), auto_start=False, idempotency_key=None, user_id=None,
            actor="test")
        task = s.scalar(select(Task).where(Task.job_id == job.id))
        task.agent, task.key = "content", "content_copy"  # an ordinary agent task (not the planner)
        task.status = status
        task.max_attempts = max_attempts
        return job.id, task.id


@pytest.fixture
def org_id():
    return make_org("Race Org", "race@example.com")["org_id"]


@pytest.fixture
def no_dispatch(monkeypatch):
    """Record re-queued messages instead of running them inline (eager mode would)."""
    sent: list[tuple[uuid.UUID, str, float]] = []
    monkeypatch.setattr(orchestrator, "send_task", lambda tid, q, countdown=0: sent.append((tid, q, countdown)))
    return sent


# --- task claims -----------------------------------------------------------------------------
def test_exactly_one_worker_claims_a_task(org_id):
    _, task_id = _job_with_task(org_id)

    def claim(_):
        session = new_session()
        try:
            task = orchestrator.claim_task(session, task_id)
            return task.attempt if task else None
        finally:
            session.close()

    results = _race(claim)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert sum(r is not None for r in results) == 1  # duplicate deliveries all lose
    with session_scope() as s:
        task = s.get(Task, task_id)
        assert task.status == TaskStatus.RUNNING and task.attempt == 1


# --- retries ---------------------------------------------------------------------------------------
def test_retry_backoff_then_dead_letter(org_id, no_dispatch, fresh_state):
    fresh_state.retry_base_seconds = 5
    _, task_id = _job_with_task(org_id, max_attempts=2)
    for attempt in (1, 2):
        session = new_session()
        try:
            task = orchestrator.claim_task(session, task_id)
            assert task is not None and task.attempt == attempt
            orchestrator.fail_task(session, task, "PROVIDER_TIMEOUT", "upstream timed out", retryable=True)
        finally:
            session.close()
        if attempt == 1:  # the retry message would arrive after the backoff; make it claimable now
            with session_scope() as s:
                assert s.get(Task, task_id).status == TaskStatus.QUEUED
    with session_scope() as s:
        task = s.get(Task, task_id)
        assert task.status == TaskStatus.FAILED and task.error_code == "PROVIDER_TIMEOUT"
        dead = s.scalars(select(AuditLog).where(AuditLog.action == "task.failed")).all()
        assert any((a.detail or {}).get("dead_letter") for a in dead)
    # one re-queue after attempt 1, with exponential backoff; none after the last attempt
    assert [(tid, round(cd)) for tid, _, cd in no_dispatch] == [(task_id, 5)]


def test_a_stale_worker_cannot_overwrite_the_retry(org_id, no_dispatch):
    """Worker A's lease expired and the task was retried by worker B: A's late result is ignored."""
    _, task_id = _job_with_task(org_id)
    s_a, s_b = new_session(), new_session()
    try:
        a = orchestrator.claim_task(s_a, task_id)
        with session_scope() as s:  # A dies; the reaper re-queues the task
            s.get(Task, task_id).status = TaskStatus.QUEUED
        b = orchestrator.claim_task(s_b, task_id)
        assert a is not None and b is not None and b.attempt == 2
        orchestrator.complete_task(s_a, a, {"from": "stale worker A"}, False)  # too late
        orchestrator.complete_task(s_b, b, {"from": "worker B"}, False)
    finally:
        s_a.close()
        s_b.close()
    with session_scope() as s:
        task = s.get(Task, task_id)
        assert task.status == TaskStatus.SUCCEEDED and task.result == {"from": "worker B"}


def test_concurrent_failures_of_one_attempt_requeue_once(org_id, no_dispatch):
    _, task_id = _job_with_task(org_id)
    session = new_session()
    assert orchestrator.claim_task(session, task_id) is not None
    session.close()

    def fail(_):
        s = new_session()
        try:
            t = s.get(Task, task_id)
            orchestrator.fail_task(s, t, "PROVIDER_TIMEOUT", "timeout", retryable=True)
        finally:
            s.close()

    results = _race(fail, 6)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert len(no_dispatch) == 1  # the same failure reported six times is re-queued once
    with session_scope() as s:
        assert s.get(Task, task_id).status == TaskStatus.QUEUED


# --- budget reservations ------------------------------------------------------------------------
def _reserve_in_thread(org_id: uuid.UUID, job_id: uuid.UUID, task_id: uuid.UUID | None, amount: str):
    session = new_session()
    try:
        job = session.get(Job, job_id)
        org = session.get(Organization, org_id)
        budget.reserve(session, job, org, Decimal(amount), task_id=task_id)
        return True
    except AgentError as exc:
        return exc.code
    finally:
        session.close()


def test_parallel_reservations_never_exceed_the_job_budget(org_id):
    job_id, task_id = _job_with_task(org_id, budget_usd="0.50")
    results = _race(lambda _: _reserve_in_thread(org_id, job_id, task_id, "0.10"))
    assert results.count(True) == 5, results
    assert set(results) <= {True, "BUDGET_EXCEEDED"}, results
    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job.reserved_cost_usd == Decimal("0.50") and job.reserved_cost_usd <= job.budget_limit_usd
        assert s.get(Organization, org_id).reserved_usd == Decimal("0.50")
        assert s.get(Task, task_id).reserved_cost_usd == Decimal("0.50")


def test_parallel_reservations_never_exceed_the_monthly_budget(org_id):
    with session_scope() as s:
        s.get(Organization, org_id).monthly_budget_usd = Decimal("0.30")
    jobs = [_job_with_task(org_id, budget_usd="5") for _ in range(THREADS)]
    results = _race(lambda i: _reserve_in_thread(org_id, jobs[i][0], jobs[i][1], "0.10"))
    assert results.count(True) == 3, results
    assert set(results) <= {True, "MONTHLY_BUDGET_EXCEEDED"}, results
    with session_scope() as s:
        assert s.get(Organization, org_id).reserved_usd == Decimal("0.30")


def test_settling_and_releasing_return_unused_money(org_id):
    job_id, task_id = _job_with_task(org_id, budget_usd="1")
    assert _reserve_in_thread(org_id, job_id, task_id, "0.20") is True
    with session_scope() as s:
        budget.settle(s, org_id=org_id, job_id=job_id, task_id=task_id, cost=Decimal("0.07"))
    with session_scope() as s:
        job, org, task = s.get(Job, job_id), s.get(Organization, org_id), s.get(Task, task_id)
        assert (job.actual_cost_usd, job.reserved_cost_usd) == (Decimal("0.07"), Decimal("0.13"))
        assert org.reserved_usd == Decimal("0.13") and task.reserved_cost_usd == Decimal("0.13")
    with session_scope() as s:
        assert budget.release_task(s, task_id) == Decimal("0.13")
    with session_scope() as s:
        assert s.get(Job, job_id).reserved_cost_usd == 0 and s.get(Organization, org_id).reserved_usd == 0
        assert s.get(Job, job_id).actual_cost_usd == Decimal("0.07")


def test_the_reaper_releases_a_dead_workers_reservation(org_id, no_dispatch):
    job_id, task_id = _job_with_task(org_id, budget_usd="1")
    session = new_session()
    assert orchestrator.claim_task(session, task_id) is not None
    session.close()
    assert _reserve_in_thread(org_id, job_id, task_id, "0.25") is True
    with session_scope() as s:  # the worker died: its lease runs out
        s.get(Task, task_id).lease_expires_at = utcnow() - timedelta(seconds=1)
    with session_scope() as s:
        stats = orchestrator.reap_expired(s)
    assert stats["requeued"] == 1
    with session_scope() as s:
        assert s.get(Task, task_id).reserved_cost_usd == 0
        assert s.get(Organization, org_id).reserved_usd == 0 and s.get(Job, job_id).reserved_cost_usd == 0
