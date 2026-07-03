"""Shared filesystem path constants.

Deliberately has zero imports from the rest of the package — api/jobs.py
and execution/docker_backend.py both need these, and if either defined
them locally the other would have to import across a layer boundary that
already has enough traffic in the other direction (api/ -> execution/ for
create_backend/DockerBackendConfig). Putting them here avoids that
entirely.
"""

from pathlib import Path

JOBS_BASE_DIR = Path.home() / ".cascade" / "jobs"

# Staging area for files uploaded through the browser's file picker
# (depletion chain files, decay/activation libraries — see
# JobSubmitModal.svelte's file inputs and api/jobs.py's POST /jobs/files).
# Each upload gets its own {file_id} subdirectory to avoid filename
# collisions between different users/jobs picking a file with the same
# name. Referenced elsewhere as the string "{file_id}/{filename}".
UPLOADS_DIR = Path.home() / ".cascade" / "uploads"