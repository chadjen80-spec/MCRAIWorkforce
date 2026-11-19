"""Atomic budget reservations for paid AI calls.

Before a paid call, its estimate is *reserved* against the job budget and the organization's monthly
budget in one short transaction that first locks the organization row (a row lock on PostgreSQL, the
database write lock on SQLite). Concurrent tasks therefore cannot both pass the same remaining budget.
When the call is billed (`settle`), the amount moves from reserved to spent; when the task ends, any
reservation left over is released (`release_task`), including by the reaper for tasks of dead workers.

Invariant per organization and month: spent + reserved <= monthly budget (and per job: actual +
reserved <= job budget) at the moment each reservation is granted.
"""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from decimal import Decimal

from sqlalchemy import case, select, update
from sqlalchemy.orm import Session

from app.errors import AgentError
from app.models import Job, Organization, Task

# The task the current worker thread is running (set by AgentContext), so routing code can attach a
# reservation to it without changing every call signature.
CURRENT_TASK: ContextVar[uuid.UUID | None] = ContextVar("mcr_current_task", default=None)

ZERO = Decimal("0")


def _lock_org(session: Session, org_id: uuid.UUID) -> None:
    # A no-op write: takes the organization's row lock (PostgreSQL) / the write lock (SQLite) first, so
    # every read below sees the latest committed spend and reservations, and others wait for us.
    session.execute(update(Organization).where(Organization.id == org_id)
                    .values(reserved_usd=Organization.reserved_usd))


def committed_this_month(session: Session, org_id: uuid.UUID) -> Decimal:
    """Spent this month plus money currently reserved by running work."""
    from app.services.router import month_spend

    reserved = session.scalar(select(Organization.reserved_usd).where(Organization.id == org_id)) or ZERO
    return month_spend(session, org_id) + Decimal(str(reserved))


def reserve(session: Session, job: Job, org: Organization, amount: Decimal,
            task_id: uuid.UUID | None = None) -> None:
    """Reserve `amount` for an upcoming paid call or raise BUDGET_EXCEEDED / MONTHLY_BUDGET_EXCEEDED."""
    if amount <= 0:
        return
    task_id = task_id or CURRENT_TASK.get()
    session.commit()  # never mix the caller's pending work into the reservation transaction
    try:
        _lock_org(session, org.id)
        committed = committed_this_month(session, org.id)
        monthly = session.scalar(select(Organization.monthly_budget_usd).where(Organization.id == org.id))
        if committed + amount > Decimal(str(monthly)):
            raise AgentError(
                "MONTHLY_BUDGET_EXCEEDED",
                f"Organization monthly budget ${monthly:.2f} would be exceeded "
                f"(spent or reserved ${committed:.4f}, next call ~${amount:.4f})",
            )
        granted = session.execute(
            update(Job).where(Job.id == job.id,
                              Job.actual_cost_usd + Job.reserved_cost_usd + amount <= Job.budget_limit_usd)
            .values(reserved_cost_usd=Job.reserved_cost_usd + amount)
        ).rowcount  # type: ignore[attr-defined]
        if not granted:
            row = session.execute(select(Job.budget_limit_usd, Job.actual_cost_usd, Job.reserved_cost_usd)
                                  .where(Job.id == job.id)).one()
            raise AgentError(
                "BUDGET_EXCEEDED",
                f"Job budget ${row[0]:.2f} would be exceeded "
                f"(spent ${row[1]:.4f}, reserved ${row[2]:.4f}, next call ~${amount:.4f})",
            )
        session.execute(update(Organization).where(Organization.id == org.id)
                        .values(reserved_usd=Organization.reserved_usd + amount))
        if task_id is not None:
            session.execute(update(Task).where(Task.id == task_id)
                            .values(reserved_cost_usd=Task.reserved_cost_usd + amount))
        session.commit()
    except BaseException:
        session.rollback()
        raise


def settle(session: Session, *, org_id: uuid.UUID, job_id: uuid.UUID, task_id: uuid.UUID, cost: Decimal) -> None:
    """Book `cost` as spent and release the matching part of the task's reservation (same transaction
    as the caller's UsageEvent; the caller commits)."""
    held = session.scalar(select(Task.reserved_cost_usd).where(Task.id == task_id)) or ZERO
    release = min(Decimal(str(held)), cost) if cost > 0 else ZERO
    _lock_org(session, org_id)
    session.execute(update(Task).where(Task.id == task_id).values(
        actual_cost_usd=Task.actual_cost_usd + cost, reserved_cost_usd=Task.reserved_cost_usd - release))
    session.execute(update(Job).where(Job.id == job_id).values(
        actual_cost_usd=Job.actual_cost_usd + cost,
        reserved_cost_usd=case((Job.reserved_cost_usd < release, ZERO), else_=Job.reserved_cost_usd - release)))
    if release > 0:
        session.execute(update(Organization).where(Organization.id == org_id).values(
            reserved_usd=case((Organization.reserved_usd < release, ZERO), else_=Organization.reserved_usd - release)))


def release_task(session: Session, task_id: uuid.UUID) -> Decimal:
    """Give back whatever the task still has reserved (it finished, failed, was cancelled or its worker
    died). Returns the amount released. Commits."""
    row = session.execute(select(Task.reserved_cost_usd, Task.job_id, Task.organization_id)
                          .where(Task.id == task_id)).one_or_none()
    if row is None or not row[0] or Decimal(str(row[0])) <= 0:
        return ZERO
    amount = Decimal(str(row[0]))
    _lock_org(session, row[2])
    session.execute(update(Task).where(Task.id == task_id).values(reserved_cost_usd=ZERO))
    session.execute(update(Job).where(Job.id == row[1]).values(
        reserved_cost_usd=case((Job.reserved_cost_usd < amount, ZERO), else_=Job.reserved_cost_usd - amount)))
    session.execute(update(Organization).where(Organization.id == row[2]).values(
        reserved_usd=case((Organization.reserved_usd < amount, ZERO), else_=Organization.reserved_usd - amount)))
    session.commit()
    return amount
