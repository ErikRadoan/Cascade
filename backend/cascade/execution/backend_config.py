"""Backend configuration models.

One Pydantic model per execution backend. These are the user-facing
configuration objects — what the frontend sends, what gets validated,
and what gets passed to the backend constructor.

The `type` discriminator field on each config lets Pydantic automatically
deserialize the correct subclass from a JSON payload like:

    {
        "type": "docker",
        "image": "cascade-openmc:latest",
        "memory_limit": "8g"
    }

Usage in the jobs API:
    config = BackendConfig.model_validate(raw_dict)   # auto-dispatches to subclass
    backend = BackendRegistry.create(config)           # instantiates the right backend

Adding a new backend:
    1. Add a new subclass of BackendConfig here
    2. Add it to the Annotated union in BackendConfig at the bottom
    3. Implement the corresponding ExecutionBackend subclass
    4. Register it in BackendRegistry.create()
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator



# ---------------------------------------------------------------------------
# Validation helpers (shared by the local-machine backends)
# ---------------------------------------------------------------------------

# '4g', '512m', '1024k', '2GiB', '100' (bytes). Empty string = no limit.
_MEMORY_LIMIT_RE = re.compile(r"^\d+(\.\d+)?([bkmgt]|[kmgt]i?b)?$", re.IGNORECASE)


def _absolutize_local_path(value: str) -> str:
    """Expand '~' and make a HOST path absolute.

    Why: the frontend sends defaults like '~/.cascade/data'. Nothing on the
    execution path goes through a shell, so '~' was never expanded: jobs were
    staged in a literal './~/.cascade/jobs' directory relative to the backend's
    cwd, and '~/...' was handed to the container runtime as a mount source.
    Normalizing here means every consumer (and the persisted profile) sees a
    real absolute path.

    NOT applied to SlurmBackendConfig.nuclear_data_path: that is a path on the
    cluster, where '~' and '/shared/...' must not be rewritten locally.
    """
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return os.path.abspath(os.path.expanduser(value))

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseBackendConfig(BaseModel):
    """Common fields shared by all backend configs."""

    # Nuclear data library — mounted read-only into every container/job
    # Points to a directory containing cross_sections.xml on the HOST machine.
    # For SLURM, this is a path on the cluster filesystem.
    nuclear_data_path: str = Field(
        default=str(Path.home() / ".cascade" / "data"),
        description=(
            "Host path to the nuclear data directory containing cross_sections.xml. "
            "Mounted read-only into every simulation environment."
        ),
    )

    # Jobs base directory — where per-job working directories are created
    jobs_base_dir: str = Field(
        default=str(Path.home() / ".cascade" / "jobs"),
        description="Host path where job working directories are created.",
    )

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Docker / Podman backend
# ---------------------------------------------------------------------------

class DockerBackendConfig(BaseBackendConfig):
    """Configuration for the Docker/Podman container backend.

    Runs OpenMC inside a container on the local machine.
    Suitable for development, single-machine research, and testing.

    Example frontend payload:
        backend_config:
          type: docker
          image: cascade-openmc:latest
          cli: podman
          memory_limit: 8g
          openmc_bin: /opt/miniconda/envs/openmc/bin/openmc
    """

    type: Literal["docker"] = "docker"

    # Container runtime
    cli: Literal["podman", "docker"] = Field(
        default="podman",
        description="Container CLI to use. 'podman' is recommended on Linux.",
    )

    # Container image
    image: str = Field(
        default="cascade-openmc:latest",
        description="Container image with OpenMC installed.",
    )

    # OpenMC binary path inside the container
    openmc_bin: str = Field(
        default="/opt/miniconda/envs/openmc/bin/openmc",
        description="Absolute path to the OpenMC binary inside the container.",
    )

    # Nuclear data path INSIDE the container (where the host path is mounted)
    nuclear_data_container_path: str = Field(
        default="/nuclear-data",
        description="Container path where nuclear_data_path is mounted.",
    )

    # Resource limits
    memory_limit: str = Field(
        default="4g",
        description="Container memory limit. e.g. '4g', '16g', '512m'.",
    )
    cpu_limit: str = Field(
        default="0",
        description=(
            "Container CPU limit. '0' = no limit. "
            "'2.0' = max 2 CPU cores."
        ),
    )

    # Explicit path to the runtime executable. Needed when the backend process's
    # PATH differs from the user's shell (IDE / service / launcher), or when the
    # runtime lives somewhere unusual. When set it takes precedence over PATH
    # lookup — see container_runtime.resolve_runtime().
    cli_path: str | None = Field(
        default=None,
        description=(
            "Optional full path to the podman/docker executable, e.g. "
            "'/usr/bin/podman'. Leave empty to look it up on PATH."
        ),
    )

    # --- validators ------------------------------------------------------

    @field_validator("nuclear_data_path", "jobs_base_dir")
    @classmethod
    def _expand_host_paths(cls, v: str) -> str:
        return _absolutize_local_path(v)

    @field_validator("cli_path")
    @classmethod
    def _blank_cli_path_is_none(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @field_validator("openmc_bin")
    @classmethod
    def _openmc_bin_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("nuclear_data_container_path")
    @classmethod
    def _container_path_is_safe(cls, v: str) -> str:
        # Must be absolute inside the container, and must not contain ':' or
        # whitespace — it is embedded in a colon-delimited --volume spec and in
        # an env var value.
        if not re.fullmatch(r"/[^:\s]*", v):
            raise ValueError("must be an absolute path without ':' or whitespace, e.g. '/nuclear-data'")
        return v

    @field_validator("memory_limit")
    @classmethod
    def _memory_limit_format(cls, v: str) -> str:
        v = v.strip()
        if v and not _MEMORY_LIMIT_RE.fullmatch(v):
            raise ValueError("must look like '4g', '512m' or '2GiB' (or be empty for no limit)")
        return v

    @field_validator("cpu_limit")
    @classmethod
    def _cpu_limit_format(cls, v: str) -> str:
        v = v.strip()
        try:
            ok = float(v) >= 0
        except ValueError:
            ok = False
        if not ok:
            raise ValueError("must be a non-negative number, e.g. '0' (no limit) or '2.0'")
        return v

    @property
    def cross_sections_container_path(self) -> str:
        return f"{self.nuclear_data_container_path}/cross_sections.xml"


# ---------------------------------------------------------------------------
# Local backend (OpenMC installed directly, no container)
# ---------------------------------------------------------------------------

class LocalBackendConfig(BaseBackendConfig):
    """Configuration for the local process backend.

    Runs OpenMC as a direct subprocess — no container required.
    Requires OpenMC to be installed and on PATH (or at openmc_bin path).

    Useful when the user has OpenMC installed via conda/pip on their machine
    and doesn't want to run a container.

    Example frontend payload:
        backend_config:
          type: local
          openmc_bin: /home/user/miniconda/envs/openmc/bin/openmc
    """

    type: Literal["local"] = "local"

    openmc_bin: str = Field(
        default="openmc",
        description=(
            "Path to the OpenMC binary. "
            "Use 'openmc' if it's on PATH, or an absolute path otherwise."
        ),
    )

    nuclear_data_container_path: str = Field(
        default="",
        description="Not used for local backend — nuclear_data_path is used directly.",
    )

    @field_validator("nuclear_data_path", "jobs_base_dir")
    @classmethod
    def _expand_host_paths(cls, v: str) -> str:
        return _absolutize_local_path(v)


# ---------------------------------------------------------------------------
# SLURM backend (university HPC clusters)
# ---------------------------------------------------------------------------

class SlurmBackendConfig(BaseBackendConfig):
    """Configuration for the SLURM HPC cluster backend.

    Submits jobs via SSH to a SLURM login node using sbatch.
    Results are transferred back via SCP after completion.

    Most university clusters (including MetaCentrum/MUNI) run SLURM.
    OpenMC is typically available as an environment module — no container needed.

    Example frontend payload:
        backend_config:
          type: slurm
          host: metacentrum.muni.cz
          username: xradovan
          ssh_key_path: /home/user/.ssh/id_ed25519
          partition: gpu
          nodes: 1
          tasks_per_node: 8
          walltime: "04:00:00"
          openmc_module: OpenMC/0.15.3-foss-2023a
          remote_work_dir: /scratch/xradovan/cascade_jobs
          nuclear_data_path: /shared/openmc_data
    """

    type: Literal["slurm"] = "slurm"

    # SSH connection
    host: str = Field(
        ...,
        description="SLURM login node hostname or IP address.",
    )
    username: str = Field(
        ...,
        description="SSH username on the cluster.",
    )
    ssh_key_path: str = Field(
        default=str(Path.home() / ".ssh" / "id_ed25519"),
        description="Path to the SSH private key on the LOCAL machine.",
    )
    ssh_port: int = Field(
        default=22,
        description="SSH port (default 22).",
    )

    # SLURM job parameters
    partition: str = Field(
        default="compute",
        description="SLURM partition/queue name. Cluster-specific.",
    )
    nodes: int = Field(
        default=1, gt=0,
        description="Number of nodes to request.",
    )
    tasks_per_node: int = Field(
        default=8, gt=0,
        description="MPI tasks per node (= CPU cores for OpenMC).",
    )
    walltime: str = Field(
        default="02:00:00",
        description="Maximum wall time in HH:MM:SS format.",
    )
    memory_per_node: str = Field(
        default="16G",
        description="Memory per node. e.g. '16G', '64G'.",
    )
    account: str | None = Field(
        default=None,
        description="SLURM account/project to charge. Leave None if not required.",
    )

    # OpenMC on the cluster
    openmc_module: str | None = Field(
        default=None,
        description=(
            "Environment module to load before running OpenMC. "
            "e.g. 'OpenMC/0.15.3-foss-2023a'. "
            "If None, openmc_bin is used directly."
        ),
    )
    openmc_bin: str = Field(
        default="openmc",
        description=(
            "OpenMC binary path on the CLUSTER (not local machine). "
            "Only used when openmc_module is None."
        ),
    )

    # Remote filesystem
    remote_work_dir: str = Field(
        ...,
        description=(
            "Directory on the CLUSTER where job files are staged. "
            "Must be writable and accessible from compute nodes. "
            "e.g. '/scratch/username/cascade_jobs'"
        ),
    )


# ---------------------------------------------------------------------------
# Discriminated union — Pydantic auto-dispatches on the `type` field
# ---------------------------------------------------------------------------

BackendConfig = Annotated[
    DockerBackendConfig | LocalBackendConfig | SlurmBackendConfig,
    Field(discriminator="type"),
]

# ---------------------------------------------------------------------------
# Backend registry — instantiates the right ExecutionBackend from a config
# ---------------------------------------------------------------------------

def create_backend(config: DockerBackendConfig | LocalBackendConfig | SlurmBackendConfig):
    """Instantiate the correct ExecutionBackend for the given config.

    Args:
        config: A validated backend config instance.

    Returns:
        An ExecutionBackend ready to call submit() on.

    Raises:
        NotImplementedError: If the backend type isn't implemented yet.
        ValueError: If the config type is unrecognised.
    """
    if isinstance(config, DockerBackendConfig):
        from .docker_backend import DockerBackend
        return DockerBackend.from_config(config)

    if isinstance(config, LocalBackendConfig):
        from .local import LocalBackend
        return LocalBackend.from_config(config)

    if isinstance(config, SlurmBackendConfig):
        from .slurm import SlurmBackend
        return SlurmBackend.from_config(config)

    raise ValueError(f"Unrecognised backend config type: {type(config).__name__}")