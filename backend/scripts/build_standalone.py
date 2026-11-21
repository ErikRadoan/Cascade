"""Build the standalone Cascade app (backend + built frontend in one bundle).

    cd backend
    uv sync --group build
    uv run python scripts/build_standalone.py            # folder bundle (recommended)
    uv run python scripts/build_standalone.py --onefile  # single executable (slower start)
    uv run python scripts/build_standalone.py --skip-frontend   # reuse frontend/build

Output: <repo>/dist/cascade/cascade   (or <repo>/dist/cascade with --onefile)

Steps: (1) `npm ci` (if needed) + `npm run build` in frontend/  ->  frontend/build
       (2) PyInstaller bundles backend/cascade/app_launcher.py plus frontend/build
           (placed at <bundle>/static, which cascade.main._find_static_dir() looks for).

Notes for future editors:
  * PyInstaller does NOT cross-compile: build on Linux for Linux, on Windows for
    Windows, etc. User data (DB, jobs, uploads) still lives in ~/.cascade — the
    bundle itself stays read-only.
  * Dev workflow (separate `uvicorn --reload` + `npm run dev`) is untouched.
  * If a new runtime-imported module/package is missed (ImportError only in the
    frozen app), add it to HIDDEN below.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND  = Path(__file__).resolve().parents[1]
REPO     = BACKEND.parent
FRONTEND = REPO / "frontend"
DIST     = REPO / "dist"
BUILD    = REPO / "build_pyinstaller"

# Packages PyInstaller can't discover statically (dynamic imports / plugins / data).
HIDDEN = [
    "--collect-submodules", "uvicorn",                    # protocol/loop impls chosen by string
    "--collect-submodules", "sqlalchemy.dialects.sqlite",
    "--collect-submodules", "cascade",                    # routers, adapters, repositories
    "--collect-data",       "cascade",                    # any non-.py files shipped in the package
    "--collect-all",        "h5py",                       # compiled ext + bundled libs
]


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(cmd)}   (in {cwd})", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def build_frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.exit(
            "ERROR: 'npm' not found on PATH. Install Node.js 20+, or — if you run this from a "
            "Flatpak PyCharm — run the frontend step in a host terminal "
            "(`cd frontend && npm ci && npm run build`) and re-run with --skip-frontend."
        )
    if not (FRONTEND / "node_modules").is_dir():
        run([npm, "ci"], FRONTEND)
    run([npm, "run", "build"], FRONTEND)


def build_bundle(onefile: bool) -> None:
    static = FRONTEND / "build"
    if not (static / "index.html").is_file():
        sys.exit(f"ERROR: {static} has no index.html — run without --skip-frontend.")

    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "cascade",
        "--onefile" if onefile else "--onedir",
        "--paths", str(BACKEND),
        "--add-data", f"{static}{sep}static",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        *HIDDEN,
        str(BACKEND / "cascade" / "app_launcher.py"),
    ]
    try:
        run(cmd, BACKEND)
    except subprocess.CalledProcessError:
        sys.exit("ERROR: PyInstaller failed. Did you run `uv sync --group build`?")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--onefile", action="store_true", help="single-file executable (slower startup)")
    ap.add_argument("--skip-frontend", action="store_true", help="reuse existing frontend/build")
    args = ap.parse_args()

    if not args.skip_frontend:
        build_frontend()
    build_bundle(args.onefile)

    exe = DIST / ("cascade.exe" if os.name == "nt" and args.onefile else "cascade")
    exe = exe if args.onefile else DIST / "cascade" / ("cascade.exe" if os.name == "nt" else "cascade")
    print(f"\nDone. Run: {exe}")


if __name__ == "__main__":
    main()
