"""Execution backend abstraction.

All backends implement this interface. JobService only ever calls these
methods — it never knows which backend is active.

Adding a new backend:
    1. Create a new file in execution/ (e.g. slurm.py)
    2. Subclass ExecutionBackend
    3. Implement all four abstract methods
    4. Register it in execution/__init__.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from ..domain.job import SimulationJob, JobStatus


class ExecutionBackend(ABC):
    """Abstract base for all execution backends."""

    name: str   # identifier string, e.g. "docker", "local", "slurm"

    @abstractmethod
    def submit(self, job: SimulationJob) -> SimulationJob:
        """Stage input files and start the job.

        Implementations must:
            - Create job.input_dir() and write simulator input files there
            - Create job.output_dir()
            - Start the simulation process/container/cluster job
            - Set job.status = JobStatus.RUNNING
            - Set job.started_at = datetime.now(timezone.utc)
            - Return the updated job

        Args:
            job: Job with working_dir already set by JobService.

        Returns:
            The same job object with updated status and started_at.
        """
        ...

    @abstractmethod
    def status(self, job: SimulationJob) -> JobStatus:
        """Poll the current status of a running job.

        Args:
            job: Previously submitted job.

        Returns:
            Current JobStatus. Backends should update job.finished_at
            and job.error when transitioning to COMPLETED or FAILED.
        """
        ...

    @abstractmethod
    def cancel(self, job: SimulationJob) -> SimulationJob:
        """Cancel a queued or running job.

        Args:
            job: Job to cancel.

        Returns:
            Job with status = CANCELLED.
        """
        ...

    @abstractmethod
    def fetch_results(self, job: SimulationJob) -> list[object]:
        """Retrieve result files from a completed job.

        For local/docker backends, results are already in job.output_dir().
        For remote backends (SLURM, Kubernetes), this transfers files locally.

        Args:
            job: Completed job (status == COMPLETED).

        Returns:
            List of result file paths or parsed result objects.
            Exact type determined per backend.
        """
        ...