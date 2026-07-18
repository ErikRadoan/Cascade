"""Slurm execution backend scaffold.

Submits jobs via SSH to a SLURM login node (sbatch) — see
SlurmBackendConfig in backend_config.py for the connection/queue
parameters this will need to use.

CHANGE (interface audit): this class name already matched what
backend_config.py imports (`SlurmBackend`), but like local.py and
kubernetes.py it only overrode `submit()`. ExecutionBackend is an ABC
with four @abstractmethods, so it couldn't be instantiated at all —
`TypeError: Can't instantiate abstract class ... with abstract methods
cancel, fetch_results, status`. Added stubs for all four, plus the
from_config() classmethod create_backend() calls on every backend
(this class was also missing that).

TODO:
    - submit(): SSH to config.host, use self._adapter.write_input_files()
      to build the input set locally, SCP it to config.remote_work_dir,
      then sbatch a generated batch script that loads config.openmc_module
      (or calls config.openmc_bin directly) and runs `openmc`.
    - status(): SSH + `squeue`/`sacct` to poll the SLURM job state.
    - cancel(): SSH + `scancel`.
    - fetch_results(): SCP results back from remote_work_dir to
      job.output_dir() once the SLURM job completes.
"""
from __future__ import annotations

from pathlib import Path

from ..domain.job import JobStatus, SimulationJob
from .backend_config import SlurmBackendConfig
from .base import ExecutionBackend


class SlurmBackend(ExecutionBackend):
    name = "slurm"

    def __init__(self, config: SlurmBackendConfig):
        self._config = config

    @classmethod
    def from_config(cls, config: SlurmBackendConfig) -> "SlurmBackend":
        return cls(config)

    def submit(self, job: SimulationJob) -> SimulationJob:
        raise NotImplementedError("Slurm execution is not wired up in the scaffold yet.")

    def status(self, job: SimulationJob) -> JobStatus:
        raise NotImplementedError("Slurm execution is not wired up in the scaffold yet.")

    def cancel(self, job: SimulationJob) -> SimulationJob:
        raise NotImplementedError("Slurm execution is not wired up in the scaffold yet.")

    def fetch_results(self, job: SimulationJob) -> list[Path]:
        raise NotImplementedError("Slurm execution is not wired up in the scaffold yet.")