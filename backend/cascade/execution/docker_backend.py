"""Docker/Podman execution backend.

Runs OpenMC simulations inside a container using subprocess calls to
the podman (or docker) CLI. No Python SDK dependency — just the CLI tool.

Job lifecycle:
    1. JobService calls submit(job)
    2. Backend writes input files to job.input_dir()
    3. Backend starts container with input_dir mounted as /work
    4. Container runs OpenMC and exits
    5. Output files land in job.output_dir() (same mount)
    6. JobService polls status(job) until COMPLETED or FAILED
    7. JobService calls fetch_results(job) to get result paths

Container requirements:
    - OpenMC binary at OPENMC_BIN path
    - OPENMC_CROSS_SECTIONS env var pointing to cross_sections.xml
      OR cross_sections.xml accessible at CROSS_SECTIONS_PATH on the host
        (mounted read-only into the container)
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.openmc_adapter import OpenMCAdapter, OpenMCRunSettings
from ..domain.job import JobStatus, SimulationJob
from .base import ExecutionBackend


# ---------------------------------------------------------------------------
# Configuration constants — adjust to match your container setup
# ---------------------------------------------------------------------------

# CLI tool to use — "podman" or "docker"
CONTAINER_CLI = "podman"

# Image that has OpenMC installed
OPENMC_IMAGE = "cascade-openmc:latest"

# Full path to OpenMC binary inside the container
OPENMC_BIN = "/opt/miniconda/envs/openmc/bin/openmc"

# Path to cross_sections.xml INSIDE the container.
# Set to None if it's already in the image and on OPENMC_CROSS_SECTIONS.
# If set, this path is passed as the OPENMC_CROSS_SECTIONS env var.
#
# Common locations after conda install:
#   /opt/miniconda/envs/openmc/share/endf/cross_sections.xml
#   /opt/miniconda/envs/openmc/lib/python3.12/site-packages/openmc/data/...
#
# Find yours with:
#   podman run --rm cascade-openmc:latest find / -name cross_sections.xml
CROSS_SECTIONS_IN_CONTAINER: str | None = None   # set after you find the path

# Base directory for job working directories on the HOST machine
JOBS_BASE_DIR = Path("/tmp/cascade/jobs")

# Container run options
CONTAINER_MEMORY_LIMIT = "4g"       # memory cap per job container
CONTAINER_CPU_LIMIT    = "0"        # "0" = no limit; "2.0" = 2 cores max


# ---------------------------------------------------------------------------
# Internal job tracking
# ---------------------------------------------------------------------------

# Maps job.id -> subprocess.Popen for running containers
# Only used within this process — not persistent across restarts
_running_processes: dict[str, subprocess.Popen] = {}
_process_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------

class DockerBackend(ExecutionBackend):
    """Runs OpenMC in a Podman/Docker container via subprocess CLI calls.

    Args:
        image:               Container image to use.
        openmc_bin:          Path to OpenMC binary inside the container.
        cross_sections_path: Path to cross_sections.xml inside the container.
                             If None, assumes OPENMC_CROSS_SECTIONS is already
                             set in the image environment.
        cli:                 "podman" or "docker".
        jobs_base_dir:       Host directory where job working dirs are created.
        run_settings:        Default OpenMC run parameters.
                             Passed to every job unless overridden.
    """

    name = "docker"

    def __init__(
        self,
        image: str = OPENMC_IMAGE,
        openmc_bin: str = OPENMC_BIN,
        cross_sections_path: str | None = CROSS_SECTIONS_IN_CONTAINER,
        cli: str = CONTAINER_CLI,
        jobs_base_dir: Path = JOBS_BASE_DIR,
        run_settings: OpenMCRunSettings | None = None,
    ):
        self._image              = image
        self._openmc_bin         = openmc_bin
        self._cross_sections     = cross_sections_path
        self._cli                = cli
        self._jobs_base_dir      = jobs_base_dir
        self._run_settings       = run_settings or OpenMCRunSettings()
        self._adapter            = OpenMCAdapter()

    # ------------------------------------------------------------------
    # ExecutionBackend interface
    # ------------------------------------------------------------------

    def submit(self, job: SimulationJob) -> SimulationJob:
        """Stage input files and launch the container.

        Sets job.working_dir if not already set, writes XML input files,
        then starts the container as a background subprocess.

        Args:
            job: Job to submit. working_dir may be None — we set it here.

        Returns:
            Job with status=RUNNING, started_at set, working_dir set.
        """
        # --- Assign working directory ---
        if job.working_dir is None:
            job.working_dir = self._jobs_base_dir / job.id

        job.input_dir().mkdir(parents=True, exist_ok=True)
        job.output_dir().mkdir(parents=True, exist_ok=True)

        # --- Write OpenMC input files ---
        self._adapter.write_input_files(
            geometry=job.geometry,
            materials=job.materials,
            output_dir=job.input_dir(),
            settings=self._run_settings,
        )

        # --- Build podman run command ---
        cmd = self._build_run_command(job)

        # --- Write command to a log file for debugging ---
        log_path = job.working_dir / "run.log"
        log_file = open(log_path, "w")
        log_file.write(f"Command: {' '.join(cmd)}\n")
        log_file.write(f"Started: {datetime.now(timezone.utc).isoformat()}\n")
        log_file.write("=" * 60 + "\n")
        log_file.flush()

        # --- Launch container as background process ---
        try:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            job.status    = JobStatus.FAILED
            job.error     = f"'{self._cli}' not found in PATH. Is Podman/Docker installed?"
            job.finished_at = datetime.now(timezone.utc)
            log_file.close()
            return job

        # --- Register the process so status() can poll it ---
        with _process_lock:
            _running_processes[job.id] = process

        job.status     = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)

        return job

    def status(self, job: SimulationJob) -> JobStatus:
        """Poll whether the container process is still running.

        Args:
            job: Previously submitted job.

        Returns:
            RUNNING, COMPLETED, or FAILED.
        """
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return job.status

        with _process_lock:
            process = _running_processes.get(job.id)

        if process is None:
            # Process not tracked — check if output files exist as a fallback
            if self._output_exists(job):
                job.status      = JobStatus.COMPLETED
                job.finished_at = datetime.now(timezone.utc)
            else:
                job.status  = JobStatus.FAILED
                job.error   = "Process not found and no output files detected."
            return job.status

        return_code = process.poll()

        if return_code is None:
            # Still running
            return JobStatus.RUNNING

        # Process finished
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
        """Kill the container process if still running.

        Args:
            job: Job to cancel.

        Returns:
            Job with status=CANCELLED.
        """
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
        """Return paths to output files for a completed job.

        For the Docker backend, results are already local — OpenMC writes
        them directly into the mounted volume (job.input_dir()), so no
        transfer is needed.

        Args:
            job: Completed job.

        Returns:
            List of Path objects for result files found in the job directory.
            Typically includes statepoint.*.h5 and tallies.out.
        """
        if job.status != JobStatus.COMPLETED:
            raise RuntimeError(
                f"fetch_results called on job '{job.id}' with status '{job.status}'. "
                "Job must be COMPLETED first."
            )

        result_files = []

        # OpenMC writes output to its working directory (/work in container = input_dir on host)
        for pattern in ("statepoint.*.h5", "tallies.out", "summary.h5"):
            result_files.extend(job.input_dir().glob(pattern))

        if not result_files:
            raise RuntimeError(
                f"No result files found in {job.input_dir()}. "
                "Job may have completed without producing output."
            )

        return sorted(result_files)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_run_command(self, job: SimulationJob) -> list[str]:
        """Build the full podman/docker run command for this job.

        Mounts input_dir as /work (read-write — OpenMC writes results there).
        Sets OPENMC_CROSS_SECTIONS if configured.
        Uses --rm so the container is deleted after it exits.

        Args:
            job: The job being submitted.

        Returns:
            Command as a list of strings, ready for subprocess.Popen.
        """
        cmd = [
            self._cli, "run",
            "--rm",                                          # clean up after exit
            "--name", f"cascade-job-{job.id[:8]}",          # identifiable name
            "--volume", f"{job.input_dir()}:/work:z",        # :z = SELinux relabel
            "--workdir", "/work",
        ]

        # Memory limit
        if CONTAINER_MEMORY_LIMIT:
            cmd += ["--memory", CONTAINER_MEMORY_LIMIT]

        # CPU limit
        if CONTAINER_CPU_LIMIT and CONTAINER_CPU_LIMIT != "0":
            cmd += ["--cpus", CONTAINER_CPU_LIMIT]

        # Cross-sections environment variable
        if self._cross_sections:
            cmd += ["--env", f"OPENMC_CROSS_SECTIONS={self._cross_sections}"]

        # Image and OpenMC command
        cmd += [self._image, self._openmc_bin]

        return cmd

    def _output_exists(self, job: SimulationJob) -> bool:
        """Check if any statepoint files exist in the job directory."""
        if job.working_dir is None:
            return False
        return any(job.input_dir().glob("statepoint.*.h5"))

    def _read_last_error(self, job: SimulationJob) -> str:
        """Read the last 20 lines of the run log for error reporting."""
        log_path = job.working_dir / "run.log"
        if not log_path.exists():
            return "No log file found."
        lines = log_path.read_text().splitlines()
        tail = lines[-20:] if len(lines) > 20 else lines
        return "\n".join(tail)