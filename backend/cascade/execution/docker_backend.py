"""Docker/Podman execution backend."""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.openmc_adapter import OpenMCAdapter, OpenMCRunSettings
from ..domain.job import JobStatus, SimulationJob
from .backend_config import DockerBackendConfig
from .base import ExecutionBackend

_running_processes: dict[str, subprocess.Popen] = {}
_process_lock = threading.Lock()


class DockerBackend(ExecutionBackend):
    """Runs OpenMC in a Podman/Docker container via subprocess CLI calls."""

    name = "docker"

    def __init__(self, config: DockerBackendConfig):
        self._config  = config
        self._adapter = OpenMCAdapter()

    @classmethod
    def from_config(cls, config: DockerBackendConfig) -> DockerBackend:
        return cls(config)

    def submit(self, job: SimulationJob) -> SimulationJob:
        cfg = self._config
        jobs_base = Path(cfg.jobs_base_dir)

        if job.working_dir is None:
            job.working_dir = jobs_base / job.id

        job.input_dir().mkdir(parents=True, exist_ok=True)
        job.output_dir().mkdir(parents=True, exist_ok=True)

        # Reconstruct OpenMCRunSettings from the plain dict stored on the job.
        # The domain model stores settings as a dict to avoid an import cycle
        # (domain → adapter). The backend is the right place to reconstitute it.
        rs = job.run_settings
        try:
            run_settings = OpenMCRunSettings(
                particles = int(rs.get("particles", 1000)),
                inactive  = int(rs.get("inactive",  20)),
                batches   = int(rs.get("batches",   100)),
                seed      = int(rs.get("seed",      1)),
                run_mode  = str(rs.get("run_mode",  "eigenvalue")),
            )
        except (ValueError, TypeError) as exc:
            job.status = JobStatus.FAILED
            job.error  = f"Invalid run settings: {exc}"
            job.finished_at = datetime.now(timezone.utc)
            return job

        self._adapter.write_input_files(
            geometry=job.geometry,
            materials=job.materials,
            output_dir=job.input_dir(),
            settings=run_settings,
            results_config=job.results_config,
        )

        cmd = self._build_run_command(job)

        log_path = job.working_dir / "run.log"
        log_file = open(log_path, "w")
        log_file.write(f"Command: {' '.join(cmd)}\n")
        log_file.write(f"Started: {datetime.now(timezone.utc).isoformat()}\n")
        log_file.write("=" * 60 + "\n")
        log_file.flush()

        try:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            job.status      = JobStatus.FAILED
            job.error       = f"'{cfg.cli}' not found in PATH."
            job.finished_at = datetime.now(timezone.utc)
            log_file.close()
            return job

        with _process_lock:
            _running_processes[job.id] = process

        job.status     = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        return job

    def status(self, job: SimulationJob) -> JobStatus:
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return job.status

        with _process_lock:
            process = _running_processes.get(job.id)

        if process is None:
            if self._output_exists(job):
                job.status      = JobStatus.COMPLETED
                job.finished_at = datetime.now(timezone.utc)
            else:
                job.status = JobStatus.FAILED
                job.error  = "Process not found and no output files detected."
            return job.status

        return_code = process.poll()
        if return_code is None:
            return JobStatus.RUNNING

        job.finished_at = datetime.now(timezone.utc)
        with _process_lock:
            _running_processes.pop(job.id, None)

        if return_code == 0:
            job.status = JobStatus.COMPLETED
        else:
            job.status = JobStatus.FAILED
            job.error  = self._read_last_error(job)

        return job.status

    def cancel(self, job: SimulationJob) -> SimulationJob:
        with _process_lock:
            process = _running_processes.pop(job.id, None)

        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

        job.status      = JobStatus.CANCELLED
        job.finished_at = datetime.now(timezone.utc)
        return job

    def fetch_results(self, job: SimulationJob) -> list[Path]:
        if job.status != JobStatus.COMPLETED:
            raise RuntimeError(f"Job '{job.id}' is not completed.")

        result_files = []
        for pattern in ("statepoint.*.h5", "tallies.out", "summary.h5"):
            result_files.extend(job.input_dir().glob(pattern))

        if not result_files:
            raise RuntimeError(f"No result files found in {job.input_dir()}.")

        return sorted(result_files)

    def _build_run_command(self, job: SimulationJob) -> list[str]:
        cfg = self._config
        nuclear_host = Path(cfg.nuclear_data_path)

        cmd = [
            cfg.cli, "run", "--rm",
            "--name", f"cascade-job-{job.id[:8]}",
            "--volume", f"{job.input_dir()}:/work:z",
            "--volume", f"{nuclear_host}:{cfg.nuclear_data_container_path}:ro,z",
            "--workdir", "/work",
            "--env", f"OPENMC_CROSS_SECTIONS={cfg.cross_sections_container_path}",
        ]

        if cfg.memory_limit:
            cmd += ["--memory", cfg.memory_limit]

        if cfg.cpu_limit and cfg.cpu_limit != "0":
            cmd += ["--cpus", cfg.cpu_limit]

        cmd += [
            cfg.image,
            "bash", "-lc",
            (
                f"source /opt/miniconda/etc/profile.d/conda.sh && "
                f"conda activate openmc && "
                f"{cfg.openmc_bin}"
            ),
        ]

        return cmd

    def _output_exists(self, job: SimulationJob) -> bool:
        if job.working_dir is None:
            return False
        return any(job.input_dir().glob("statepoint.*.h5"))

    def _read_last_error(self, job: SimulationJob) -> str:
        log_path = job.working_dir / "run.log"
        if not log_path.exists():
            return "No log file found."
        lines = log_path.read_text().splitlines()
        return "\n".join(lines[-20:] if len(lines) > 20 else lines)