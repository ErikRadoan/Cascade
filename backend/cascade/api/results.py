"""Results API — parse OpenMC statepoint HDF5 files and return structured data.

Endpoints
---------
GET /results/{job_id}/summary
    k-eff (all three estimators + combined), neutron balance, timing,
    per-batch k history, Shannon entropy history.

GET /results/{job_id}/tallies
    Scalar cell tallies — mean + std dev per cell per score (tally IDs 101–199).

GET /results/{job_id}/mesh
    3-D mesh tally as a structured response (tally ID 200).

GET /results/{job_id}/spectra
    Energy flux spectra per material (tally IDs 301+).

GET /results/{job_id}/statepoint/path
    Filesystem path of the final statepoint file (for download links).

HDF5 layout reference:
    https://docs.openmc.org/en/stable/io_formats/statepoint.html

Key HDF5 facts that bit us during development:
  - global_tallies shape is (4, 3): columns are [sum, sum_sq, ???].
    Mean = sum / n_realizations. The four rows are:
    [0] leakage, [1] absorption, [2] fission, [3] nu-fission.
  - k_combined = [mean, std_dev] (already computed by OpenMC).
  - k_col_abs, k_abs_tra, k_col_tra are pairwise combined estimators,
    each [mean, std_dev]. There is no separate k_col/k_abs/k_tracklength.
  - Individual batch k-eff values live in k_generation (length = n_batches).
  - Shannon entropy lives in source_shannon_entropy (not "entropy").
  - results datasets shape: (n_filter_bins, n_score_bins, 2) where
    dim-2 is [sum, sum_sq]. NOT [mean, rel_err].
    mean    = sum / n_realizations
    std_dev = sqrt(max(0, sum_sq/n - mean^2) / (n - 1))
  - Statepoints are written to the OpenMC working directory, which is
    job.input_dir() (the /work mount), NOT job.output_dir().
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..repositories.db import get_db
from ..repositories.job_repository import JobRepository
from ..domain.job import JobStatus, SimulationJob
from ..domain.results_config import MeshType

router = APIRouter(prefix="/results", tags=["results"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_job_or_404(job_id: str, db: Session) -> SimulationJob:
    job = JobRepository(db).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


def _require_completed(job: SimulationJob) -> None:
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job '{job.id}' is {job.status.value} — results are only "
                "available for completed jobs."
            ),
        )


def _find_statepoint(job: SimulationJob) -> Path:
    """Locate the statepoint HDF5 file in the job's OpenMC working directory.

    OpenMC writes statepoint.<batch>.h5 to the directory it runs in,
    which for the Docker backend is job.input_dir() (the /work mount).
    We return the file with the highest batch number — the final result.
    """
    # OpenMC runs in input_dir (the /work container mount) so statepoints
    # land there, not in output_dir.
    search_dirs = [job.input_dir(), job.output_dir()]

    candidates: list[Path] = []
    for d in search_dirs:
        if d.exists():
            candidates.extend(d.glob("statepoint.*.h5"))

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No statepoint file found for job '{job.id}'. "
                "The job may have failed before writing output, or is still running."
            ),
        )

    def _batch_num(p: Path) -> int:
        m = re.search(r"statepoint\.(\d+)\.h5", p.name)
        return int(m.group(1)) if m else 0

    return max(candidates, key=_batch_num)


def _open_statepoint(job: SimulationJob) -> tuple[Path, h5py.File]:
    sp_path = _find_statepoint(job)
    try:
        f = h5py.File(sp_path, "r")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not open statepoint '{sp_path}': {exc}",
        )
    return sp_path, f


def _kstat(arr: np.ndarray) -> dict[str, float]:
    """Unpack a 2-element [mean, std_dev] k-eff estimator array."""
    return {"mean": float(arr[0]), "std_dev": float(arr[1])}


def _tally_mean_stddev(
    total: float,
    sum_sq: float,
    n: int,
) -> tuple[float, float]:
    """Convert OpenMC's raw sum and sum-of-squares to mean and std dev.

    OpenMC stores per-batch running sums, not means. Conversion:
        mean    = total / n
        std_dev = sqrt(max(0, sum_sq/n - mean^2) / (n - 1))

    The max(0, ...) guard prevents tiny negative values due to floating
    point cancellation when variance is near zero.
    """
    if n <= 0:
        return 0.0, 0.0
    mean = total / n
    variance_estimate = (sum_sq / n - mean ** 2) / max(1, n - 1)
    std_dev = math.sqrt(max(0.0, variance_estimate))
    return mean, std_dev


# ---------------------------------------------------------------------------
# /summary
# ---------------------------------------------------------------------------

@router.get("/{job_id}/summary")
async def get_summary(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return k-eff, neutron balance, timing, and per-batch convergence history.

    Response schema::

        {
          "job_id": "...",
          "batches": 100,
          "inactive": 20,
          "particles_per_batch": 1000,
          "n_realizations": 80,
          "k_effective": {
            "combined":   {"mean": 1.3437, "std_dev": 0.0034},
            "col_abs":    {"mean": 1.3438, "std_dev": 0.0035},
            "abs_tra":    {"mean": 1.4413, "std_dev": 0.0001},
            "col_tra":    {"mean": 1.4352, "std_dev": 0.0001}
          },
          "entropy_history":   [0.91, 0.93, ...],
          "keff_history":      [1.40, 1.37, ...],
          "neutron_balance":   {
            "leakage":     0.0,
            "absorption":  107.25,
            "fission":     143.99,
            "nu_fission":  0.0
          },
          "timing":            {}
        }
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)
    sp_path, sp = _open_statepoint(job)
    try:
        return _parse_summary(job_id, sp)
    finally:
        sp.close()


def _parse_summary(job_id: str, sp: h5py.File) -> dict[str, Any]:
    # ── Basic run parameters ────────────────────────────────────────────────
    batches       = int(sp["n_batches"][()]) if "n_batches" in sp else 0
    inactive      = int(sp["n_inactive"][()]) if "n_inactive" in sp else 0
    pps           = int(sp["n_particles"][()]) if "n_particles" in sp else 0
    n_realizations = int(sp["n_realizations"][()]) if "n_realizations" in sp else max(1, batches - inactive)

    # ── k-effective estimators ───────────────────────────────────────────────
    # k_combined, k_col_abs, k_abs_tra, k_col_tra are all [mean, std_dev] arrays.
    # Individual estimator values are encoded in global_tallies (see below).
    k_effective: dict[str, dict] = {}

    for key, label in [
        ("k_combined", "combined"),
        ("k_col_abs",  "col_abs"),
        ("k_abs_tra",  "abs_tra"),
        ("k_col_tra",  "col_tra"),
    ]:
        if key in sp:
            arr = sp[key][()]
            if arr.ndim == 1 and len(arr) >= 2:
                k_effective[label] = _kstat(arr)

    # ── Per-batch histories ──────────────────────────────────────────────────
    # k_generation has one value per batch (inactive + active combined).
    # source_shannon_entropy has one value per active batch.
    keff_history: list[float] = []
    entropy_history: list[float] = []

    if "k_generation" in sp:
        keff_history = sp["k_generation"][()].tolist()

    # OpenMC uses "source_shannon_entropy" in recent versions; fall back to
    # "entropy" for older builds.
    for entropy_key in ("source_shannon_entropy", "entropy"):
        if entropy_key in sp:
            entropy_history = sp[entropy_key][()].tolist()
            break

    # ── Neutron balance from global_tallies ─────────────────────────────────
    # global_tallies shape: (4, 3) — rows are [leakage, absorption, fission, nu-fission]
    # Columns are [sum, sum_sq, <unused>].
    # Mean per source neutron = sum / n_realizations.
    neutron_balance: dict[str, float] = {}
    if "global_tallies" in sp:
        gt = sp["global_tallies"][()]
        labels = ["leakage", "absorption", "fission", "nu_fission"]
        for i, label in enumerate(labels):
            if i < gt.shape[0]:
                raw_sum = float(gt[i, 0])
                neutron_balance[label] = raw_sum / n_realizations if n_realizations > 0 else 0.0

    # ── Timing ───────────────────────────────────────────────────────────────
    timing: dict[str, float] = {}
    if "runtime" in sp:
        rt = sp["runtime"]
        for key in rt.keys():
            try:
                timing[key] = float(rt[key][()])
            except Exception:
                pass

    return {
        "job_id":              job_id,
        "batches":             batches,
        "inactive":            inactive,
        "particles_per_batch": pps,
        "n_realizations":      n_realizations,
        "k_effective":         k_effective,
        "entropy_history":     entropy_history,
        "keff_history":        keff_history,
        "neutron_balance":     neutron_balance,
        "timing":              timing,
    }


# ---------------------------------------------------------------------------
# /tallies
# ---------------------------------------------------------------------------

@router.get("/{job_id}/tallies")
async def get_tallies(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return scalar cell tally results — mean + std dev per cell per score.

    Response schema::

        {
          "job_id": "...",
          "tallies": [
            {
              "tally_id": 101,
              "name": "cell_1_scalars",
              "scores": {
                "flux":    {"mean": 3.14e12, "std_dev": 7.2e10, "rel_err": 0.023},
                "fission": {"mean": 1.02e11, "std_dev": 3.2e9,  "rel_err": 0.031}
              }
            }
          ]
        }
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)
    sp_path, sp = _open_statepoint(job)
    try:
        n_real = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1
        return _parse_tallies(job_id, sp, n_real)
    finally:
        sp.close()


def _parse_tallies(job_id: str, sp: h5py.File, n_realizations: int) -> dict[str, Any]:
    tallies_grp = sp.get("tallies")
    if tallies_grp is None:
        return {"job_id": job_id, "tallies": []}

    result: list[dict] = []

    for tally_key in tallies_grp.keys():
        t = tallies_grp[tally_key]
        if not isinstance(t, h5py.Group):
            continue
        if not tally_key.startswith("tally"):
            continue
        # OpenMC statepoints don't store an "id" attr on tally groups;
        # the id is embedded in the group name, e.g. "tally 101"
        try:
            tally_id = int(tally_key.split()[1])
        except (IndexError, ValueError):
            continue

        # Only scalar tallies (101–199); mesh and spectra have dedicated endpoints.
        if not (100 < tally_id < 200):
            continue

        name = t.attrs.get("name", tally_key)
        if isinstance(name, bytes):
            name = name.decode()

        # Decode score names — only one read, no double-fetch
        scores_list: list[str] = []
        if "score_bins" in t:
            scores_list = [
                s.decode() if isinstance(s, bytes) else str(s)
                for s in t["score_bins"][()]
            ]

        results_data = t.get("results")
        if results_data is None:
            continue
        # arr shape: (n_filter_bins, n_score_bins, 2)  — [sum, sum_sq]
        arr = results_data[()]

        scores_out: dict[str, dict] = {}
        for si, score in enumerate(scores_list):
            if si >= arr.shape[1]:
                break
            total  = float(arr[0, si, 0])
            sum_sq = float(arr[0, si, 1]) if arr.shape[2] > 1 else 0.0
            mean, std_dev = _tally_mean_stddev(total, sum_sq, n_realizations)
            rel_err = (std_dev / mean) if mean != 0.0 else 0.0
            scores_out[score] = {
                "mean":    mean,
                "std_dev": std_dev,
                "rel_err": rel_err,
            }

        result.append({
            "tally_id": tally_id,
            "name":     name,
            "scores":   scores_out,
        })

    result.sort(key=lambda x: x["tally_id"])
    return {"job_id": job_id, "tallies": result}


# ---------------------------------------------------------------------------
# /mesh
# ---------------------------------------------------------------------------

@router.get("/{job_id}/mesh")
async def get_mesh(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the 3-D mesh tally as a structured response.

    Response schema::

        {
          "job_id": "...",
          "tally_id": 200,
          "mesh": {
            "type": "regular",
            "shape": [nx, ny, nz],
            "lower_left":  [x0, y0, z0],
            "upper_right": [x1, y1, z1]
          },
          "scores": ["flux", "fission", "heating-local"],
          "data": [
            {"ix": 0, "iy": 0, "iz": 0,
             "flux_mean": 1.2e13, "flux_std_dev": 3.1e11, ...},
            ...
          ]
        }
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    if not job.results_config.mesh.enabled:
        raise HTTPException(
            status_code=404,
            detail="Mesh tally was not requested for this job.",
        )

    sp_path, sp = _open_statepoint(job)
    try:
        import h5py
        tallies = sp["tallies"]
        for key in tallies.keys():
            t = tallies[key]
            if isinstance(t, h5py.Group):
                print(key, dict(t.attrs))

        meshes = tallies.get("meshes")
        if meshes is not None:
            for k in meshes.keys():
                m = meshes[k]
                print(k, dict(m.attrs) if isinstance(m, h5py.Group) else m[()])

        n_real = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1
        return _parse_mesh(job_id, sp, job.results_config.mesh.mesh_type, n_real)
    finally:
        sp.close()


def _parse_mesh(
    job_id: str,
    sp: h5py.File,
    mesh_type: MeshType,
    n_realizations: int,
) -> dict[str, Any]:
    tallies_grp = sp.get("tallies")
    if tallies_grp is None:
        raise HTTPException(status_code=404, detail="No tallies in statepoint.")

    # Find tally with id=200
    mesh_tally = None
    for key in tallies_grp.keys():
        t = tallies_grp[key]
        if not isinstance(t, h5py.Group) or not key.startswith("tally"):
            continue
        # OpenMC statepoints don't store an "id" attr on tally groups;
        # the id is embedded in the group name, e.g. "tally 200"
        try:
            tally_id = int(key.split()[1])
        except (IndexError, ValueError):
            continue
        if tally_id == 200:
            mesh_tally = t
            break

    if mesh_tally is None:
        raise HTTPException(status_code=404, detail="Mesh tally (id=200) not found.")

    # Mesh metadata lives under /tallies/meshes/1 (or the mesh id)
    mesh_meta: dict[str, Any] = {}
    meshes_grp = tallies_grp.get("meshes")
    if meshes_grp is not None:
        for mesh_key in meshes_grp.keys():
            m = meshes_grp[mesh_key]
            if not isinstance(m, h5py.Group):
                continue
            shape       = m["dimension"][()] if "dimension" in m else np.array([1, 1, 1])
            lower_left  = m["lower_left"][()].tolist() if "lower_left" in m else [0, 0, 0]
            upper_right = m["upper_right"][()].tolist() if "upper_right" in m else [1, 1, 1]
            mesh_meta   = {
                "type":        mesh_type.value,
                "shape":       shape.tolist() if hasattr(shape, "tolist") else list(shape),
                "lower_left":  lower_left,
                "upper_right": upper_right,
            }
            break  # use the first mesh found

    # Score names
    scores_list: list[str] = []
    if "score_bins" in mesh_tally:
        scores_list = [
            s.decode() if isinstance(s, bytes) else str(s)
            for s in mesh_tally["score_bins"][()]
        ]

    results_data = mesh_tally.get("results")
    if results_data is None:
        return {"job_id": job_id, "tally_id": 200, "mesh": mesh_meta,
                "scores": scores_list, "data": []}

    # arr shape: (nx*ny*nz, n_scores, 2) — [sum, sum_sq]
    arr = results_data[()]
    shape = mesh_meta.get("shape", [1, 1, 1])
    nx, ny, nz = (list(shape) + [1, 1, 1])[:3]

    data: list[dict] = []
    idx = 0
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                if idx >= arr.shape[0]:
                    break
                row: dict[str, Any] = {"ix": ix, "iy": iy, "iz": iz}
                for si, score in enumerate(scores_list):
                    if si >= arr.shape[1]:
                        break
                    total  = float(arr[idx, si, 0])
                    sum_sq = float(arr[idx, si, 1]) if arr.shape[2] > 1 else 0.0
                    mean, std_dev = _tally_mean_stddev(total, sum_sq, n_realizations)
                    row[f"{score}_mean"]    = mean
                    row[f"{score}_std_dev"] = std_dev
                data.append(row)
                idx += 1

    return {
        "job_id":   job_id,
        "tally_id": 200,
        "mesh":     mesh_meta,
        "scores":   scores_list,
        "data":     data,
    }


# ---------------------------------------------------------------------------
# /spectra
# ---------------------------------------------------------------------------

@router.get("/{job_id}/spectra")
async def get_spectra(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return energy flux spectra per material (tally IDs 301+).

    Response schema::

        {
          "job_id": "...",
          "group_structure": "69",
          "spectra": [
            {
              "tally_id": 301,
              "name": "spectrum_H2O",
              "group_boundaries_ev": [1e-5, ...],
              "flux_mean":    [3.1e12, ...],
              "flux_std_dev": [7.2e10, ...]
            }
          ]
        }
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    if not job.results_config.spectra.enabled:
        raise HTTPException(
            status_code=404,
            detail="Energy spectra were not requested for this job.",
        )

    sp_path, sp = _open_statepoint(job)
    try:
        n_real = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1
        return _parse_spectra(job_id, sp, job.results_config, n_real)
    finally:
        sp.close()


def _parse_spectra(
    job_id: str,
    sp: h5py.File,
    results_config: Any,
    n_realizations: int,
) -> dict[str, Any]:
    tallies_grp = sp.get("tallies")

    if tallies_grp is None:
        return {"job_id": job_id, "spectra": []}

    group_structure = results_config.spectra.group_structure.value
    boundaries      = results_config.spectra.boundaries()
    spectra_out: list[dict] = []

    for key in tallies_grp.keys():
        t = tallies_grp[key]

        if not isinstance(t, h5py.Group):
            continue

        # ONLY real tallies
        if not key.startswith("tally"):
            continue

        try:
            tally_id = int(key.split()[1])
        except (IndexError, ValueError):
            continue

        name = t.attrs.get("name", key)
        if isinstance(name, bytes):
            name = name.decode()

        results_data = t.get("results")
        if results_data is None:
            continue

        # arr shape: (n_material_bins * n_energy_bins, n_scores, 2)
        # Each tally has exactly one material bin, so shape[0] == n_energy_bins.
        # OpenMC stores one value per *group* (interval between boundaries),
        # so n_energy_bins == len(boundaries) - 1.
        arr = results_data[()]
        n_energy_bins = arr.shape[0]

        flux_mean:    list[float] = []
        flux_std_dev: list[float] = []

        for i in range(n_energy_bins):
            total  = float(arr[i, 0, 0])
            sum_sq = float(arr[i, 0, 1]) if arr.shape[2] > 1 else 0.0
            mean, std_dev = _tally_mean_stddev(total, sum_sq, n_realizations)
            flux_mean.append(mean)
            flux_std_dev.append(std_dev)

        # group_boundaries_ev has len = n_energy_bins + 1 (fence-post values).
        # flux_mean/flux_std_dev have len = n_energy_bins (one per group).
        spectra_out.append({
            "tally_id":            tally_id,
            "name":                name,
            "group_boundaries_ev": boundaries,
            "group_midpoints_ev":  [
                math.sqrt(boundaries[i] * boundaries[i + 1])
                for i in range(len(boundaries) - 1)
            ],
            "flux_mean":           flux_mean,
            "flux_std_dev":        flux_std_dev,
        })

    spectra_out.sort(key=lambda x: x["tally_id"])
    return {
        "job_id":          job_id,
        "group_structure": group_structure,
        "spectra":         spectra_out,
    }


# ---------------------------------------------------------------------------
# /statepoint/path
# ---------------------------------------------------------------------------

@router.get("/{job_id}/statepoint/path")
async def get_statepoint_path(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Return the filesystem path of the final statepoint file."""
    job = _get_job_or_404(job_id, db)
    _require_completed(job)
    sp_path = _find_statepoint(job)
    return {"job_id": job_id, "path": str(sp_path)}