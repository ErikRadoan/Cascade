"""Standalone launcher: start the backend (which also serves the built
frontend) and open the browser.

Used by the packaged executable (see scripts/build_standalone.py) and handy
from source too:   uv run python -m cascade.app_launcher

Dev mode is unchanged: keep using `uvicorn cascade.main:app --reload` plus
`npm run dev` for separate backend/frontend.

Options (env vars): CASCADE_PORT (default 8000, falls back to a free port),
CASCADE_HOST (default 127.0.0.1 — keep it local; the API has no auth),
CASCADE_NO_BROWSER=1 (don't open a browser tab).
"""

from __future__ import annotations

import os
import socket
import threading
import time
import urllib.request
import webbrowser

import uvicorn

# Imported at module level (not as a "cascade.main:app" string) so PyInstaller's
# static analysis sees and bundles the whole application.
from cascade.main import _STATIC_DIR, app


def _pick_port(host: str, preferred: int) -> int:
    for port in (preferred, 0):  # 0 = let the OS choose a free one
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No free TCP port available.")


def _open_browser_when_ready(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{url}/health", timeout=1).close()
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.25)


def main() -> None:
    host = os.getenv("CASCADE_HOST", "127.0.0.1")
    port = _pick_port(host, int(os.getenv("CASCADE_PORT", "8000")))
    url = f"http://{host}:{port}"

    if _STATIC_DIR is None:
        print("WARNING: no built frontend found — only the API (/docs) will be available.\n"
              "Run `npm run build` in frontend/ or set CASCADE_STATIC_DIR.")
    print(f"Cascade running at {url}  (Ctrl+C to quit)")

    if not os.getenv("CASCADE_NO_BROWSER"):
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
