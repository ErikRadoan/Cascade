"""JobStep — one execution unit within a job's pipeline.

Why this exists (see PLAN_depletion_r2s_execution.md):
    The pipeline previously assumed 1 job = 1 OpenMC invocation. That's
    still true for eigenvalue/fixed_source, but not for depletion (1 job =
    1 Python-driver-script invocation — no step list needed, the
    openmc.deplete integrator owns its own timestep loop) or r2s (1 job =
    3 sequential, heterogeneous steps: neutron transport -> activation
    (not transport) -> photon transport, each consuming the previous
    step's output).

    JobStep is the unit DockerBackend actually launches/polls/advances.
    A single-leg job (eigenvalue/fixed_source/depletion) still gets
    exactly one JobStep — this isn't a special case, it's a pipeline of
    length 1. Only r2s has more than one.

Ownership boundary:
    JobStep is a pure data structure, like the rest of domain/. It knows
    nothing about subprocess handles, container commands, or file
    staging — that's execution/docker_backend.py's job. JobStep only
    tracks *that* a step exists, what kind it is, and its lifecycle state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from .job_status import JobStatus

# StepStatus is JobStatus — a step's lifecycle and a job's derived lifecycle
# are the same five states. Aliased for readability at call sites
# (`StepStatus.RUNNING` reads more clearly in step-handling code than
# `JobStatus.RUNNING` would), and to keep this module's public API stable
# if the two ever need to diverge later.
StepStatus = JobStatus


class StepKind(StrEnum):
    """What kind of work a step does — determines how DockerBackend builds
    its run command and how it stages the *next* step's inputs.

    TRANSPORT:       A single OpenMC transport leg (XML + CLI `openmc`
                     binary). Used by eigenvalue, fixed_source, and both
                     of r2s's legs.
    DEPLETION_DRIVER: A Python driver script invoking openmc.deplete's
                     coupled operator + integrator. Used by depletion.
                     Always exactly one step — see module docstring.
    ACTIVATION:      A non-transport decay/activation calculation (r2s's
                     middle step). Consumes the neutron leg's reaction-rate
                     mesh + irradiation schedule, produces a photon source
                     file. Tooling TBD — see PLAN doc Task 5.
    """
    TRANSPORT        = "transport"
    DEPLETION_DRIVER = "depletion_driver"
    ACTIVATION       = "activation"


@dataclass(slots=True)
class JobStep:
    """One execution unit within a job.

    Attributes:
        id:          Unique step identifier (UUID string).
        job_id:      Parent SimulationJob.id.
        sequence:    0-indexed position in the pipeline. DockerBackend
                     only starts step N+1 after step N reaches COMPLETED.
        kind:        What kind of work this step does — see StepKind.
        label:       Human-readable name for UI display, e.g. "neutron leg",
                     "activation", "photon leg". Single-step jobs can use
                     a generic label like "transport".
        status:      Current lifecycle state.
        working_dir: This step's own input/output staging directory —
                     NOT shared with other steps in the same job, since
                     r2s's steps need independent input/ (settings.xml
                     differs per leg) and output/ (the next step reads
                     the previous step's output/ to build its own input/).
        error:       Error message if status is FAILED, else None.
        started_at / finished_at: UTC timestamps.
    """
    id:          str
    job_id:      str
    sequence:    int
    kind:        StepKind
    label:       str
    status:      StepStatus       = StepStatus.QUEUED
    working_dir: Path | None      = None
    error:       str | None       = None
    started_at:  datetime | None  = None
    finished_at: datetime | None  = None

    def input_dir(self) -> Path:
        if self.working_dir is None:
            raise RuntimeError(f"Step '{self.id}' has no working_dir set.")
        return self.working_dir / "input"

    def output_dir(self) -> Path:
        if self.working_dir is None:
            raise RuntimeError(f"Step '{self.id}' has no working_dir set.")
        return self.working_dir / "output"

    def to_dict(self) -> dict[str, object]:
        return {
            "id":          self.id,
            "job_id":      self.job_id,
            "sequence":    self.sequence,
            "kind":        self.kind.value,
            "label":       self.label,
            "status":      self.status.value,
            "working_dir": str(self.working_dir) if self.working_dir else None,
            "error":       self.error,
            "started_at":  self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> JobStep:
        return cls(
            id=d["id"],
            job_id=d["job_id"],
            sequence=d["sequence"],
            kind=StepKind(d["kind"]),
            label=d["label"],
            status=StepStatus(d.get("status", "queued")),
            working_dir=Path(d["working_dir"]) if d.get("working_dir") else None,
            error=d.get("error"),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            finished_at=datetime.fromisoformat(d["finished_at"]) if d.get("finished_at") else None,
        )


def derive_job_status(steps: list[JobStep]) -> StepStatus:
    """Roll up a list of JobSteps into one overall status.

    Mirrors the pattern already used for sweeps (SweepRow's status is
    derived at read time from child job statuses, never stored) — applied
    one level down, from steps to their parent job.

    Rules (checked in order):
        - Any step FAILED     -> job FAILED (fail fast; don't advance further)
        - Any step CANCELLED  -> job CANCELLED
        - Any step RUNNING    -> job RUNNING
        - All steps COMPLETED -> job COMPLETED
        - Otherwise (all QUEUED, or a queued gap before the next step
          starts) -> job QUEUED
    """
    if not steps:
        raise ValueError("Cannot derive status from an empty step list.")
    statuses = {s.status for s in steps}
    if StepStatus.FAILED in statuses:
        return StepStatus.FAILED
    if StepStatus.CANCELLED in statuses:
        return StepStatus.CANCELLED
    if StepStatus.RUNNING in statuses:
        return StepStatus.RUNNING
    if all(s == StepStatus.COMPLETED for s in statuses):
        return StepStatus.COMPLETED
    return StepStatus.QUEUED


def next_runnable_step(steps: list[JobStep]) -> JobStep | None:
    """Return the earliest-sequence step that should be started/resumed next.

    A step is runnable if it's QUEUED and every step before it (by
    `sequence`) is COMPLETED. Returns None if nothing is runnable right
    now (either everything is done, something failed, or the current step
    is still RUNNING).
    """
    ordered = sorted(steps, key=lambda s: s.sequence)
    for step in ordered:
        if step.status == StepStatus.RUNNING:
            return None  # still waiting on this one
        if step.status in (StepStatus.FAILED, StepStatus.CANCELLED):
            return None  # pipeline is stopped
        if step.status == StepStatus.QUEUED:
            return step  # all prior steps must be COMPLETED to reach here
    return None  # all COMPLETED