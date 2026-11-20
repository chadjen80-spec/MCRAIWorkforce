"""Agent registry: one place that lists every agent, what it is for, and how it runs.

- "creative" agents run inside jobs on their Celery queue with an `AgentContext` (job + task). These are the
  original six; their runners and queues are unchanged, and `app.worker.runner.AGENTS` is built from here.
- "qtc" agents (Quote-to-Cash) run with a `QtcAgentContext` (organization + actor) when Mira or the API asks,
  and return a validated, structured result. They read real records only and never write commercial data on
  their own: anything that creates or changes a record is returned as a proposal for a person to confirm.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.agents import content, document, image, planner, qa, video


@dataclass(frozen=True)
class AgentSpec:
    name: str
    family: Literal["creative", "qtc"]
    description: str
    runner: Callable[..., Any] | None  # creative: run(ctx) -> dict; qtc agents are classes, see app.qtc.agents
    queue: str | None = None  # Celery queue for creative agents


_REGISTRY: dict[str, AgentSpec] = {}


def register(spec: AgentSpec) -> AgentSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"agent {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> AgentSpec:
    return _REGISTRY[name]


def all_agents(family: str | None = None) -> list[AgentSpec]:
    return [s for s in _REGISTRY.values() if family is None or s.family == family]


for _name, _module, _desc in (
    ("planner", planner, "Turns a command into a validated plan and task graph"),
    ("content", content, "Captions, copy and video scripts"),
    ("image", image, "Images and posters"),
    ("document", document, "Business documents as PDF and DOCX"),
    ("video", video, "Short videos with narration and subtitles"),
    ("qa", qa, "Checks every deliverable before release"),
):
    register(AgentSpec(name=_name, family="creative", description=_desc, runner=_module.run, queue=_name))


def creative_runners() -> dict[str, Callable[..., dict[str, Any]]]:
    return {s.name: s.runner for s in all_agents("creative") if s.runner is not None}
