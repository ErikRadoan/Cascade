"""Container runtime helpers (podman / docker) for DockerBackend.

WHY THIS FILE EXISTS
--------------------
DockerBackend used to do `subprocess.Popen(["podman", "run", ...])` and treat
any FileNotFoundError as "'podman' not found in PATH". That had three problems:

  1. The *backend process* PATH is not the user's shell PATH. When uvicorn is
     started from an IDE, a systemd unit, a GUI launcher or a venv wrapper,
     `podman` can be perfectly installed and still be invisible. There was no
     way to point Cascade at the binary (the config field was a
     Literal["podman", "docker"], so absolute paths were rejected).
  2. FileNotFoundError was caught around code that also opens the log file,
     so unrelated failures were reported as "podman not found".
  3. Other launch failures (PermissionError, exec format error, a broken
     install) were not caught at all and surfaced as opaque HTTP 500s.

This module is deliberately free of Cascade domain imports (no jobs, no
adapters) so it can be unit-tested with nothing but the standard library and
reused by a future LocalBackend / SlurmBackend if they need to shell out.

EDITING NOTES (for future human/agent editors)
----------------------------------------------
* Everything here that talks to the runtime is *best effort* or raises
  ContainerRuntimeError with a message meant to be shown verbatim to the
  end user (it ends up in `job.error` / `step.error` in the UI).
* Never build a shell string from untrusted pieces without `quote_shell()`.
  The only place a shell is involved is the `bash -lc "<script>"` inside the
  container; the host-side command is always an argv list (no host shell).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


class ContainerRuntimeError(RuntimeError):
    """The container runtime is missing, unusable, or misconfigured.

    The message is user-facing: it is stored on the job/step error field.
    """


# ---------------------------------------------------------------------------
# Locating the runtime executable
# ---------------------------------------------------------------------------

def _fallback_search_dirs() -> list[str]:
    """Well-known install locations that are often missing from the PATH of a
    non-interactive process (IDE, service manager, GUI launcher).

    Kept as a function (not a constant) so tests can patch it and so that
    `~` / env vars are evaluated at call time.
    """
    if sys.platform == "win32":
        dirs = []
        for env, sub in (
            ("ProgramFiles", r"RedHat\Podman"),
            ("ProgramFiles", r"Docker\Docker\resources\bin"),
            ("LOCALAPPDATA", r"Programs\Podman"),
        ):
            base = os.environ.get(env)
            if base:
                dirs.append(os.path.join(base, sub))
        return dirs

    return [
        "/usr/bin",
        "/usr/local/bin",
        "/opt/homebrew/bin",                      # Homebrew on Apple Silicon
        "/opt/podman/bin",                        # official podman macOS pkg
        "/snap/bin",
        "/home/linuxbrew/.linuxbrew/bin",
        os.path.expanduser("~/.local/bin"),
    ]


def resolve_runtime(cli: str, cli_path: str | None = None) -> str:
    """Return an absolute path to the container runtime executable.

    Resolution order:
      1. `cli_path` if given. An explicit path is authoritative: if it is
         wrong we raise instead of silently falling back to something else
         (the user asked for that exact binary).
         A bare name (no path separator) in `cli_path` is looked up like `cli`.
      2. `cli` on the backend process's PATH (shutil.which; honours PATHEXT on
         Windows).
      3. `cli` in the well-known install dirs from _fallback_search_dirs().

    Raises:
        ContainerRuntimeError: with a message explaining what was tried and
        how to fix it.
    """
    name = cli
    if cli_path:
        candidate = os.path.expanduser(cli_path.strip())
        has_sep = os.sep in candidate or (os.altsep is not None and os.altsep in candidate)
        if has_sep:
            if os.path.isdir(candidate):
                raise ContainerRuntimeError(
                    f"Configured cli_path '{cli_path}' is a directory, not an executable."
                )
            if not os.path.isfile(candidate):
                raise ContainerRuntimeError(
                    f"Configured cli_path '{cli_path}' does not exist."
                )
            if not os.access(candidate, os.X_OK):
                raise ContainerRuntimeError(
                    f"Configured cli_path '{cli_path}' is not executable (check file permissions)."
                )
            return os.path.abspath(candidate)
        name = candidate  # bare command name -> normal lookup below

    found = shutil.which(name)
    if found:
        return os.path.abspath(found)

    fallback_dirs = _fallback_search_dirs()
    found = shutil.which(name, path=os.pathsep.join(fallback_dirs))
    if found:
        return os.path.abspath(found)

    raise ContainerRuntimeError(
        f"Container runtime '{name}' was not found. "
        f"Searched the backend process's PATH ({os.environ.get('PATH', '') or 'empty'}) "
        f"and common install locations ({', '.join(fallback_dirs)}). "
        f"Install {name}, or set 'cli_path' in the backend profile to the full path "
        f"of the executable (find it in a terminal with `which {name}`). "
        f"Note: the Cascade backend can see a different PATH than your interactive shell "
        f"when it was started from an IDE, a service, or a launcher."
    )


def verify_runtime(runtime_path: str, timeout: float = 20.0) -> str:
    """Run `<runtime> --version` to prove the binary actually executes.

    Catches exec-format errors, permission problems and broken installs *before*
    we try to launch a job, with a readable message. Returns the version line.

    Deliberately does NOT call `info`/`ps`: with podman on macOS/Windows those
    require a running `podman machine` and can hang; `--version` is local-only.
    """
    try:
        proc = subprocess.run(
            [runtime_path, "--version"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ContainerRuntimeError(
            f"'{runtime_path} --version' did not respond within {timeout:.0f}s."
        ) from e
    except OSError as e:
        raise ContainerRuntimeError(f"Cannot execute '{runtime_path}': {e}") from e

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise ContainerRuntimeError(
            f"'{runtime_path} --version' exited with code {proc.returncode}: {detail}"
        )
    return (proc.stdout or "").strip()


# ---------------------------------------------------------------------------
# Naming / quoting / formatting
# ---------------------------------------------------------------------------

_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")


def container_name(unit_id: str) -> str:
    """Deterministic container name for a job/step id.

    Uses the FULL id (the old code truncated to 8 chars, so two units whose ids
    shared a prefix, or a leftover container from a crashed run, could collide
    with "name already in use"). Container names allow [a-zA-Z0-9_.-] and must
    start with an alphanumeric; the fixed 'cascade-' prefix guarantees that.
    """
    return "cascade-" + _INVALID_NAME_CHARS.sub("-", unit_id)


def quote_shell(value: str) -> str:
    """Quote one value for inclusion in a POSIX `bash -c` script."""
    return shlex.quote(value)


def format_command(cmd: list[str]) -> str:
    """Render an argv list as a copy-pasteable shell line (for run.log only)."""
    return shlex.join(cmd)


def volume_spec(host_path: str | Path, container_path: str, options: str = "") -> str:
    """Build a `--volume` value, refusing inputs that would be mis-parsed.

    `--volume SRC:DST[:OPTS]` is colon-delimited, so a host path containing ':'
    silently turns into a different mount (or a runtime error that points at the
    wrong thing). On Windows a drive letter ('C:\\...') legitimately contains one,
    so the check is POSIX-only.
    """
    host = str(Path(host_path).resolve())
    if sys.platform != "win32" and ":" in host:
        raise ContainerRuntimeError(
            f"Host path '{host}' contains ':', which container runtimes cannot mount "
            f"via --volume. Move the jobs/nuclear-data directory to a path without ':'."
        )
    if ":" in container_path:
        raise ContainerRuntimeError(f"Container path '{container_path}' must not contain ':'.")
    return f"{host}:{container_path}" + (f":{options}" if options else "")


# ---------------------------------------------------------------------------
# Stopping a container
# ---------------------------------------------------------------------------

def stop_container(runtime_path: str, name: str, grace_seconds: int = 5) -> None:
    """Best-effort stop + force-remove of a named container.

    Terminating the *client* process (`podman run ...`) is not enough: SIGKILL
    to the client leaves the container running, and a hung container ignores
    SIGTERM. We therefore ask the runtime itself to stop it, then force-remove
    as a backstop (e.g. when `--rm` never fired). All failures are swallowed:
    "container already gone" is the normal case and must not raise.
    """
    for args in (
        [runtime_path, "stop", "--time", str(grace_seconds), name],
        [runtime_path, "rm", "--force", name],
    ):
        try:
            subprocess.run(args, capture_output=True, timeout=grace_seconds + 15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
