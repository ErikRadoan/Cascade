"""Local execution backend scaffold.

Runs OpenMC as a direct subprocess (no container) — the same shape as
DockerBackend in docker_backend.py, minus the podman/docker command
wrapping. That file is the reference implementation to mirror once this
is built out.

CHANGE (interface audit): this class was named `LocalExecutionBackend`,
but backend_config.py's create_backend() does
`from .local import LocalBackend` — an ImportError the moment the local
backend was ever selected. Renamed to `LocalBackend` to match (and to be
consistent with DockerBackend/SlurmBackend's naming). It also only
overrode `submit()`; ExecutionBackend is an ABC with four
@abstractmethods (submit/status/cancel/fetch_results), so the old class
couldn't even be instantiated — Python would raise
`TypeError: Can't instantiate abstract class ... with abstract methods
cancel, fetch_results, status` before ever reaching the NotImplementedError
inside submit(). Added stubs for all four, plus the from_config()
classmethod create_backend() calls on every backend.

TODO:
    - submit(): call self._adapter.write_input_files(...) into
      job.input_dir(), then subprocess.Popen the config's `openmc_bin`
      directly (no container wrapper) — mirror
      DockerBackend._submit_single_leg() minus the docker/podman command
      building. Depletion/r2s dispatch can likely reuse the same shape
      DockerBackend.submit() uses to pick _submit_single_leg /
      _submit_depletion / _submit_r2s.
    - status(): poll the subprocess the same way DockerBackend._status_single_leg
      does.
    - cancel(): terminate the subprocess (see DockerBackend._terminate).
    - fetch_results(): results are already local to job.output_dir(), same
      as DockerBackend.fetch_results() — no transfer step needed.
"""
from __future__ import annotations

from pathlib import Path

from ..domain.job import JobStatus, SimulationJob
from .backend_config import LocalBackendConfig
from .base import ExecutionBackend


class LocalBackend(ExecutionBackend):
    name = "local"

    def __init__(self, config: LocalBackendConfig):
        self._config = config

    @classmethod
    def from_config(cls, config: LocalBackendConfig) -> "LocalBackend":
        return cls(config)

    def submit(self, job: SimulationJob) -> SimulationJob:
        raise NotImplementedError("Local execution is not wired up in the scaffold yet.")

    def status(self, job: SimulationJob) -> JobStatus:
        raise NotImplementedError("Local execution is not wired up in the scaffold yet.")

    def cancel(self, job: SimulationJob) -> SimulationJob:
        raise NotImplementedError("Local execution is not wired up in the scaffold yet.")

    def fetch_results(self, job: SimulationJob) -> list[Path]:
        raise NotImplementedError("Local execution is not wired up in the scaffold yet.")