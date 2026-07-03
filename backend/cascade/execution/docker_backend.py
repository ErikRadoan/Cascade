"""Docker/Podman execution backend.

See PLAN_depletion_r2s_execution.md for the architecture this file
implements (Tasks 3 & 4). Summary of the dispatch:

    eigenvalue / fixed_source -> ONE process, XML+CLI `openmc` binary.
                                 job.steps stays empty; status is tracked
                                 on `job.status` directly (legacy path,
                                 UNCHANGED from before this pass).
    depletion                 -> ONE process, but running a generated
                                 Python driver script instead of the CLI
                                 binary (openmc.deplete owns its own
                                 timestep loop — see openmc_adapter.py's
                                 write_depletion_driver()). Modeled as a
                                 single JobStep(kind=DEPLETION_DRIVER) so
                                 it shares the same step-status machinery
                                 as r2s, even though it's only one step.
    r2s                        -> THREE JobSteps run in sequence: neutron
                                 leg (TRANSPORT) -> activation (ACTIVATION,
                                 not implemented yet — see Task 5) ->
                                 photon leg (TRANSPORT, source derived from
                                 the activation step's output file).
                                 status() advances the pipeline: when the
                                 running step's process exits 0, its
                                 outputs are finalized, and the next
                                 runnable step (domain.job_step.next_runnable_step)
                                 is staged and launched.

For any job with `job.steps` populated, `job.status` (the plain field) is
NOT the source of truth — `job.effective_status()` is (see domain/job.py).
This file always returns/relies on that, never the raw field, for stepped
jobs.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..adapters.openmc_adapter import OpenMCAdapter, OpenMCRunSettings
from ..domain.job import JobStatus, SimulationJob
from ..domain.job_step import JobStep, StepKind, StepStatus, next_runnable_step
from ..domain.results_config import R2SResultsConfig
from ..domain.run_settings import RunMode, SourceDef, SourceSpaceType
from ..domain.paths import UPLOADS_DIR
from .backend_config import DockerBackendConfig
from .base import ExecutionBackend

# Keyed by job.id for legacy single-leg jobs, by step.id for stepped jobs.
# No collision risk — both are independently-generated UUID strings — and
# at most one process per job is ever running at a time in both cases
# (steps execute strictly sequentially), so a single flat dict is enough.
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

    # ------------------------------------------------------------------
    # submit()
    # ------------------------------------------------------------------

    def submit(self, job: SimulationJob) -> SimulationJob:
        """Stage input files and launch `job`. Dispatches by run_mode."""
        if job.working_dir is None:
            job.working_dir = Path(self._config.jobs_base_dir) / job.id

        if job.run_mode == RunMode.DEPLETION:
            return self._submit_depletion(job)
        if job.run_mode == RunMode.R2S:
            return self._submit_r2s(job)
        return self._submit_single_leg(job)

    def _submit_single_leg(self, job: SimulationJob) -> SimulationJob:
        """eigenvalue / fixed_source — unchanged from before this pass.

        CHANGE (prior pass): previously called write_input_files() with no
        `settings=`/`results_config=`, so every job silently ran with the
        adapter's hardcoded defaults regardless of what the user
        submitted. Fixed by building `settings` from job.monte_carlo/
        job.source and passing job.results_config through. That fix is
        preserved as-is here.
        """
        job.input_dir().mkdir(parents=True, exist_ok=True)
        job.output_dir().mkdir(parents=True, exist_ok=True)

        settings = OpenMCRunSettings.for_leg(
            job.monte_carlo, job.run_mode, source=job.source,
        )
        self._adapter.write_input_files(
            geometry=job.geometry,
            materials=job.materials,
            output_dir=job.input_dir(),
            settings=settings,
            results_config=job.results_config,
        )

        cmd = self._build_run_command(job.input_dir(), job.output_dir(), job.id)
        try:
            process = self._launch(cmd, job.working_dir / "run.log")
        except FileNotFoundError:
            job.status      = JobStatus.FAILED
            job.error       = f"'{self._config.cli}' not found in PATH."
            job.finished_at = datetime.now(timezone.utc)
            return job

        with _process_lock:
            _running_processes[job.id] = process

        job.status     = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        return job

    def _submit_depletion(self, job: SimulationJob) -> SimulationJob:
        """depletion — one JobStep(kind=DEPLETION_DRIVER).

        Still exactly one execution unit (see openmc_adapter.py's
        write_depletion_driver() docstring for why this must NOT be a
        per-timestep step list), but modeled as a JobStep anyway so it
        shares status/advancement machinery with r2s rather than needing
        its own separate code path in status()/cancel()/fetch_results().
        """
        step = JobStep(
            id=_new_id(), job_id=job.id, sequence=0,
            kind=StepKind.DEPLETION_DRIVER, label="depletion",
            working_dir=job.working_dir / "step_0_depletion",
        )
        job.steps = [step]

        step.input_dir().mkdir(parents=True, exist_ok=True)
        step.output_dir().mkdir(parents=True, exist_ok=True)

        # Stage the uploaded chain file directly into this step's input_dir
        # (already mounted at /work) rather than assuming it exists at some
        # fixed host path — see _stage_uploaded_file() docstring.
        chain_filename = self._stage_uploaded_file(job.mode_specific.chain_file, step.input_dir())

        self._adapter.write_depletion_driver(
            geometry=job.geometry,
            materials=job.materials,
            output_dir=step.input_dir(),
            mc=job.monte_carlo,
            depletion=job.mode_specific,
            chain_file_container_path=chain_filename,  # relative — resolved against /work in-container
            results_config=job.results_config,
        )

        cmd = self._build_run_command(
            step.input_dir(), step.output_dir(), step.id,
            driver_script="run_depletion.py",
        )
        self._launch_step(step, cmd)
        return job

    def _submit_r2s(self, job: SimulationJob) -> SimulationJob:
        """r2s — 3 JobSteps. Only the neutron leg is staged/launched here;
        activation and the photon leg are staged by status() as the
        pipeline advances (their inputs don't exist until the previous
        step finishes)."""
        r2s = job.mode_specific
        rc = job.results_config
        if not isinstance(rc, R2SResultsConfig):
            raise TypeError("r2s job must have an R2SResultsConfig — this should have been caught at SimulationJob construction.")

        steps = [
            JobStep(id=_new_id(), job_id=job.id, sequence=0, kind=StepKind.TRANSPORT,
                    label="neutron leg", working_dir=job.working_dir / "step_0_neutron"),
            JobStep(id=_new_id(), job_id=job.id, sequence=1, kind=StepKind.ACTIVATION,
                    label="activation", working_dir=job.working_dir / "step_1_activation"),
            JobStep(id=_new_id(), job_id=job.id, sequence=2, kind=StepKind.TRANSPORT,
                    label="photon leg", working_dir=job.working_dir / "step_2_photon"),
        ]
        job.steps = steps

        neutron_step = steps[0]
        neutron_step.input_dir().mkdir(parents=True, exist_ok=True)
        neutron_step.output_dir().mkdir(parents=True, exist_ok=True)

        settings = OpenMCRunSettings.for_leg(
            r2s.neutron_leg_mc, RunMode.FIXED_SOURCE, source=r2s.neutron_leg_source,
        )
        self._adapter.write_input_files(
            geometry=job.geometry,
            materials=job.materials,
            output_dir=neutron_step.input_dir(),
            settings=settings,
            results_config=rc.neutron_leg,
        )

        cmd = self._build_run_command(neutron_step.input_dir(), neutron_step.output_dir(), neutron_step.id)
        self._launch_step(neutron_step, cmd)
        return job

    # ------------------------------------------------------------------
    # status()
    # ------------------------------------------------------------------

    def status(self, job: SimulationJob) -> JobStatus:
        if not job.steps:
            return self._status_single_leg(job)
        return self._status_stepped(job)

    def _status_single_leg(self, job: SimulationJob) -> JobStatus:
        """Unchanged legacy behavior for eigenvalue/fixed_source."""
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return job.status

        with _process_lock:
            process = _running_processes.get(job.id)

        if process is None:
            if self._output_exists(job.output_dir()):
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
            try:
                self._finalize_outputs(job.input_dir(), job.output_dir())
                job.status = JobStatus.COMPLETED
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = f"Post-processing failed: {e}"
        else:
            job.status = JobStatus.FAILED
            job.error = self._read_last_error(job.working_dir)

        return job.status

    def _status_stepped(self, job: SimulationJob) -> JobStatus:
        running_step = next((s for s in job.steps if s.status == StepStatus.RUNNING), None)

        if running_step is None:
            # Nothing in-flight: either the pipeline is done, stopped
            # (a step FAILED/CANCELLED), or — shouldn't happen — nothing
            # was ever launched. Nothing to advance; just report.
            return self._finalize_job_timestamp(job)

        with _process_lock:
            process = _running_processes.get(running_step.id)

        if process is None:
            running_step.status      = StepStatus.FAILED
            running_step.error       = "Process handle lost unexpectedly."
            running_step.finished_at = datetime.now(timezone.utc)
            return self._finalize_job_timestamp(job)

        return_code = process.poll()
        if return_code is None:
            return JobStatus.RUNNING

        running_step.finished_at = datetime.now(timezone.utc)
        with _process_lock:
            _running_processes.pop(running_step.id, None)

        if return_code != 0:
            running_step.status = StepStatus.FAILED
            running_step.error  = self._read_last_error(running_step.working_dir)
            return self._finalize_job_timestamp(job)

        try:
            self._finalize_outputs(running_step.input_dir(), running_step.output_dir())
        except Exception as e:
            running_step.status = StepStatus.FAILED
            running_step.error  = f"Post-processing failed: {e}"
            return self._finalize_job_timestamp(job)

        running_step.status = StepStatus.COMPLETED

        nxt = next_runnable_step(job.steps)
        if nxt is None:
            return self._finalize_job_timestamp(job)

        try:
            self._stage_and_launch_next(job, nxt, completed_step=running_step)
        except NotImplementedError as e:
            nxt.status      = StepStatus.FAILED
            nxt.error       = str(e)
            nxt.finished_at = datetime.now(timezone.utc)
        return self._finalize_job_timestamp(job)

    def _finalize_job_timestamp(self, job: SimulationJob) -> JobStatus:
        """Set job.finished_at exactly once, the moment the job's derived
        status first becomes terminal — called at every exit point of
        _status_stepped instead of each branch setting it individually.

        This is a direct fix for a real bug: several failure branches
        above (process handle lost, non-zero exit, post-processing
        failure, activation-not-implemented) each independently forgot to
        set job.finished_at, which meant `GET /jobs/{id}` (see
        api/jobs.py's get_job()) could report a FAILED job with
        finished_at still None. Centralizing it here means every current
        and future exit point gets this for free instead of needing to
        remember it.

        Also propagates the failing step's error message up to job.error:
        each branch above sets `running_step.error`/`nxt.error`, but
        nothing previously copied that onto the job-level `error` field
        that JobDetail actually surfaces to the frontend — a failed
        stepped job would correctly show status=FAILED but with no
        explanation of why, since the real message only lived on the
        step object.
        """
        status = _derive(job)
        if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            if job.finished_at is None:
                job.finished_at = datetime.now(timezone.utc)
            if status == JobStatus.FAILED and job.error is None:
                failed_step = next((s for s in job.steps if s.status == StepStatus.FAILED), None)
                if failed_step is not None:
                    job.error = f"[{failed_step.label}] {failed_step.error}"
        return status

    def _stage_and_launch_next(self, job: SimulationJob, step: JobStep, completed_step: JobStep) -> None:
        """Stage inputs for `step` from `completed_step`'s outputs, then launch it.

        Only r2s reaches this (depletion is always a single step, launched
        entirely within _submit_depletion). At present the only real
        transition this can perform is activation -> photon leg; neutron
        leg -> activation always raises NotImplementedError (Task 5).
        """
        if step.kind == StepKind.ACTIVATION:
            raise NotImplementedError(
                "r2s's activation step is not implemented yet — no decay/"
                "activation solver is wired in (PLAN_depletion_r2s_execution.md "
                "Task 5: ALARA vs. a custom decay calc is an open decision). "
                "The neutron leg ran successfully; its reaction-rate mesh is "
                f"available at {completed_step.output_dir()} for whichever "
                "tool ends up implementing this step."
            )

        if step.kind == StepKind.TRANSPORT:
            # The only TRANSPORT step reached via advancement (not directly
            # at submit()) is r2s's photon leg, coming after activation.
            self._stage_r2s_photon_leg(job, step, activation_step=completed_step)
            return

        raise NotImplementedError(f"Don't know how to advance to a step of kind {step.kind}.")

    def _stage_r2s_photon_leg(self, job: SimulationJob, photon_step: JobStep, activation_step: JobStep) -> None:
        r2s = job.mode_specific
        rc  = job.results_config

        photon_step.input_dir().mkdir(parents=True, exist_ok=True)
        photon_step.output_dir().mkdir(parents=True, exist_ok=True)

        # The activation step must have written a photon source file to its
        # output_dir. Copy it into the photon leg's input_dir so it's
        # visible inside that step's container mount (which maps
        # input_dir -> /work) — same reasoning as why geometry/materials/
        # settings XML live in input_dir, not output_dir.
        source_filename = "photon_source.h5"
        src = activation_step.output_dir() / source_filename
        if not src.exists():
            raise RuntimeError(
                f"Activation step completed but did not produce {source_filename} "
                f"in {activation_step.output_dir()} — cannot stage the photon leg."
            )
        shutil.copy2(src, photon_step.input_dir() / source_filename)

        photon_source = SourceDef(
            particle="photon", space_type=SourceSpaceType.FILE,
            file_path=source_filename,  # relative — resolved against /work in-container
        )
        settings = OpenMCRunSettings.for_leg(
            r2s.photon_leg_mc, RunMode.FIXED_SOURCE, source=photon_source,
        )
        self._adapter.write_input_files(
            geometry=job.geometry,
            materials=job.materials,
            output_dir=photon_step.input_dir(),
            settings=settings,
            results_config=rc.photon_leg,
        )

        cmd = self._build_run_command(photon_step.input_dir(), photon_step.output_dir(), photon_step.id)
        self._launch_step(photon_step, cmd)

    # ------------------------------------------------------------------
    # cancel()
    # ------------------------------------------------------------------

    def cancel(self, job: SimulationJob) -> SimulationJob:
        if not job.steps:
            return self._cancel_single_leg(job)

        now = datetime.now(timezone.utc)
        running_step = next((s for s in job.steps if s.status == StepStatus.RUNNING), None)
        if running_step is not None:
            self._terminate(running_step.id)
            running_step.status      = StepStatus.CANCELLED
            running_step.finished_at = now

        for s in job.steps:
            if s.status == StepStatus.QUEUED:
                s.status      = StepStatus.CANCELLED
                s.finished_at = now

        job.finished_at = now
        return job

    def _cancel_single_leg(self, job: SimulationJob) -> SimulationJob:
        self._terminate(job.id)
        job.status      = JobStatus.CANCELLED
        job.finished_at = datetime.now(timezone.utc)
        return job

    # ------------------------------------------------------------------
    # fetch_results()
    # ------------------------------------------------------------------

    def fetch_results(self, job: SimulationJob) -> list[Path]:
        if not job.steps:
            return self._fetch_results_dir(job.output_dir(), job.input_dir())

        if job.effective_status() != JobStatus.COMPLETED:
            raise RuntimeError(f"Job '{job.id}' is not completed.")

        # The last (highest-sequence) step holds the job's "final" result —
        # for r2s that's the photon leg's dose mesh; for depletion (single
        # step) it's the only step there is.
        last_step = max(job.steps, key=lambda s: s.sequence)
        return self._fetch_results_dir(last_step.output_dir(), last_step.input_dir())

    def _fetch_results_dir(self, output_dir: Path, input_dir_for_error: Path) -> list[Path]:
        result_files = []
        for pattern in ("statepoint.*.h5", "tallies.out", "summary.h5", "depletion_results.h5"):
            result_files.extend(output_dir.glob(pattern))

        if not result_files:
            raise RuntimeError(f"No result files found in {input_dir_for_error}.")

        return sorted(result_files)

    # ------------------------------------------------------------------
    # Process management (shared by legacy and stepped paths)
    # ------------------------------------------------------------------

    def _launch(self, cmd: list[str], log_path: Path) -> subprocess.Popen:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w")
        log_file.write(f"Command: {' '.join(cmd)}\n")
        log_file.write(f"Started: {datetime.now(timezone.utc).isoformat()}\n")
        log_file.write("=" * 60 + "\n")
        log_file.flush()
        return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)

    def _launch_step(self, step: JobStep, cmd: list[str]) -> None:
        try:
            process = self._launch(cmd, step.working_dir / "run.log")
        except FileNotFoundError:
            step.status      = StepStatus.FAILED
            step.error       = f"'{self._config.cli}' not found in PATH."
            step.finished_at = datetime.now(timezone.utc)
            return

        with _process_lock:
            _running_processes[step.id] = process

        step.status     = StepStatus.RUNNING
        step.started_at = datetime.now(timezone.utc)

    def _terminate(self, unit_id: str) -> None:
        with _process_lock:
            process = _running_processes.pop(unit_id, None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    # ------------------------------------------------------------------
    # Container command building
    # ------------------------------------------------------------------

    def _build_run_command(
        self,
        input_dir: Path,
        output_dir: Path,
        name: str,
        driver_script: str | None = None,
        extra_mounts: list[str] | None = None,
    ) -> list[str]:
        """Build the container invocation for one execution unit (a legacy
        job or a single step).

        `driver_script`: if set (depletion), runs `python {driver_script}`
        inside the conda env instead of the `openmc` CLI binary — see
        openmc_adapter.py's write_depletion_driver().
        `extra_mounts`: additional `--volume` args, for any future case that
        genuinely needs a host-side mount rather than a file staged into
        input_dir (uploaded files — chain files, decay libraries — no
        longer need this; see _stage_uploaded_file()).
        """
        cfg = self._config
        nuclear_host = Path(cfg.nuclear_data_path)

        cmd = [
            cfg.cli, "run", "--rm",
            "--name", f"cascade-{name[:8]}",
            "--volume", f"{input_dir}:/work:z",
            "--volume", f"{nuclear_host}:{cfg.nuclear_data_container_path}:ro,z",
            "--volume", f"{output_dir}:/output:z",
            "--workdir", "/work",
            "--env", f"OPENMC_CROSS_SECTIONS={cfg.cross_sections_container_path}",
        ]

        for mount in (extra_mounts or []):
            cmd += ["--volume", mount]

        if cfg.memory_limit:
            cmd += ["--memory", cfg.memory_limit]

        if cfg.cpu_limit and cfg.cpu_limit != "0":
            cmd += ["--cpus", cfg.cpu_limit]

        inner = f"{cfg.openmc_bin}" if driver_script is None else f"python {driver_script}"

        cmd += [
            cfg.image,
            "bash", "-lc",
            (
                f"source /opt/miniconda/etc/profile.d/conda.sh && "
                f"conda activate openmc && "
                f"{inner}"
            ),
        ]

        return cmd

    def _stage_uploaded_file(self, upload_ref: str, dest_dir: Path) -> str:
        """Copy a previously-uploaded file into an execution unit's input_dir.

        `upload_ref` is the value produced by POST /jobs/files and stored
        directly in DepletionSettings.chain_file / ActivationSettings.
        decay_library — a "{file_id}/{filename}" relative path under
        UPLOADS_DIR (see api/jobs.py's upload_job_file()). This replaces
        the previous provisional approach of guessing a fixed host
        directory and mounting it read-only: the file's actual bytes were
        uploaded through the browser's file picker (JobSubmitModal.svelte),
        so staging it is a plain copy into the input_dir that's already
        mounted at /work — no separate mount needed, no host-side
        directory convention to document or get wrong.

        Returns the bare filename (e.g. "chain_endfb71_pwr.xml"), which is
        what should be used as a relative, in-container path (resolved
        against /work) when referencing this file from generated XML or
        driver scripts.
        """
        upload_path = UPLOADS_DIR / upload_ref
        if not upload_path.is_file():
            raise RuntimeError(
                f"Uploaded file reference '{upload_ref}' not found at "
                f"{upload_path}. It may have been cleaned up, or the "
                f"reference is stale — re-upload the file and resubmit."
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / upload_path.name
        shutil.copy2(upload_path, dest_path)
        return upload_path.name

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _output_exists(self, output_dir: Path) -> bool:
        return any(output_dir.glob("statepoint*.h5")) or any(output_dir.glob("depletion_results.h5"))

    def _read_last_error(self, working_dir: Path | None) -> str:
        if working_dir is None:
            return "No working directory set."
        log_path = working_dir / "run.log"
        if not log_path.exists():
            return "No log file found."
        lines = log_path.read_text().splitlines()
        return "\n".join(lines[-20:] if len(lines) > 20 else lines)

    def _finalize_outputs(self, src_dir: Path, dst_dir: Path) -> None:
        """Copy OpenMC outputs from an execution unit's input dir (where
        the container wrote them, mounted at /work) to its output dir."""
        dst_dir.mkdir(parents=True, exist_ok=True)

        patterns = ("statepoint*.h5", "tallies.out", "summary.h5", "depletion_results.h5", "photon_source.h5")

        copied_any = False
        for pattern in patterns:
            for file in src_dir.glob(pattern):
                shutil.copy2(file, dst_dir / file.name)
                copied_any = True

        if not copied_any:
            raise RuntimeError(f"Run finished but no output files found in {src_dir}")


def _new_id() -> str:
    import uuid
    return str(uuid.uuid4())


def _derive(job: SimulationJob) -> JobStatus:
    return job.effective_status()