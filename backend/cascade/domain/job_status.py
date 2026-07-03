"""JobStatus — shared lifecycle enum for SimulationJob and JobStep.

Split out from domain/job.py so domain/job_step.py can use the same enum
(a step's status and a job's derived status are the same five states)
without job.py <-> job_step.py forming a circular import: job.py needs
JobStep, job_step.py needs JobStatus, so the shared enum has to live
somewhere both can import from independently.

domain/job.py re-exports this as `JobStatus` for backwards compatibility —
existing `from ..domain.job import JobStatus` imports elsewhere in the
codebase (api/jobs.py, execution/docker_backend.py, etc.) are unaffected.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED    = "queued"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"