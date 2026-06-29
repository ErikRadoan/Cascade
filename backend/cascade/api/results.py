"""Results API — parse OpenMC statepoint HDF5 files and return structured data.

Endpoints
---------
GET /results/{job_id}/summary
    k-eff (all three estimators), neutron balance, timing, batch convergence.

GET /results/{job_id}/tallies
    Scalar cell tallies — mean + relative std dev per cell per score.
    Returns the tally index in the same ID scheme used by export_tallies()
    (101+ for cells, 200 for mesh, 301+ for spectra) so the frontend can
    correlate results with request config without a separate mapping table.

GET /results/{job_id}/mesh
    3-D mesh tally as a flat array of {x, y, z, score, mean, std_dev} dicts.
    The mesh shape and bounds are included in the response envelope so the
    frontend can reconstruct the spatial grid.

GET /results/{job_id}/spectra
    Energy spectra per material — list of {material_id, group_boundaries, flux}
    where flux is a list of mean values matching the boundary bins.

GET /results/{job_id}/statepoint/path
    Returns the path to the statepoint file so the frontend can link to it.

All endpoints return 404 when the job doesn't exist or hasn't produced output
yet, and 422 when the statepoint exists but is malformed.

HDF5 layout reference:
    https://docs.openmc.org/en/stable/io_formats/statepoint.html
"""

from __future__ import annotations

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
    """Locate the statepoint HDF5 file in the job output directory.

    OpenMC names statepoints 'statepoint.<batch>.h5'.  We return the one
    with the highest batch number (the final, fully-converged result).

    Raises:
        HTTPException 404: No statepoint file found.
    """
    output_dir = job.output_dir()
    candidates = sorted(output_dir.glob("statepoint.*.h5"))
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No statepoint file found in '{output_dir}'. "
                "The job may have failed before writing output."
            ),
        )
    # Sort by batch number embedded in the filename
    def _batch_num(p: Path) -> int:
        m = re.search(r"statepoint\.(\d+)\.h5", p.name)
        return int(m.group(1)) if m else 0

    return max(candidates, key=_batch_num)


def _open_statepoint(job: SimulationJob) -> tuple[Path, h5py.File]:
    """Open the statepoint HDF5 file, raising 422 on corruption."""
    sp_path = _find_statepoint(job)
    try:
        f = h5py.File(sp_path, "r")
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not open statepoint '{sp_path}': {exc}",
        )
    return sp_path, f


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
          "k_effective": {
            "collision":  {"mean": 1.0023, "std_dev": 0.0004},
            "absorption": {"mean": 1.0019, "std_dev": 0.0005},
            "tracklength":{"mean": 1.0021, "std_dev": 0.0003},
            "combined":   {"mean": 1.0021, "std_dev": 0.0003}
          },
          "entropy_history":   [0.91, 0.93, ...],   # one per active batch
          "keff_history":      [1.01, 1.00, ...],   # one per active batch
          "neutron_balance":   {"absorption": 0.62, "fission": 0.38, "leakage": 0.0},
          "timing":            {"transport": 14.3, "total": 15.1}
        }
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    sp_path, sp = _open_statepoint(job)

    # with h5py.File(sp_path, "r") as f:
    #     for name, obj in f.items():
    #         if isinstance(obj, h5py.Dataset):
    #             if obj.shape == ():
    #                 print(name, "=", obj[()])
    #             else:
    #                 print(name, "=", obj[:])

    try:
        return _parse_summary(job_id, sp)
    finally:
        sp.close()


def _parse_summary(job_id: str, sp: h5py.File) -> dict[str, Any]:
    batches  = int(sp["n_batches"][()]) if "n_batches" in sp else 0
    inactive = int(sp["n_inactive"][()]) if "n_inactive" in sp else 0
    pps      = int(sp["n_particles"][()]) if "n_particles" in sp else 0

    # n_realizations = active batches actually accumulated; used to normalise
    # the raw sums that OpenMC stores in the HDF5 results datasets.
    n_real = int(sp["n_realizations"][()]) if "n_realizations" in sp else max(batches - inactive, 1)

    # k_combined is shape (2,) → [mean, std_dev]  (already normalised by OpenMC)
    def _keff_combined(key: str) -> dict[str, float] | None:
        node = sp.get(key)
        if node is None:
            return None
        arr = node[()]
        if arr.ndim == 1 and arr.shape[0] >= 2:
            return {"mean": float(arr[0]), "std_dev": float(arr[1])}
        return None

    # k_col_abs / k_col_tra / k_abs_tra are plain scalars (single float) —
    # OpenMC writes these as the ratio k already divided by n_realizations.
    def _keff_scalar(key: str) -> dict[str, float] | None:
        node = sp.get(key)
        if node is None:
            return None
        return {"mean": float(node[()]), "std_dev": None}

    k_effective: dict[str, Any] = {
        "col_absorption":  _keff_scalar("k_col_abs"),
        "col_tracklength": _keff_scalar("k_col_tra"),
        "abs_tracklength": _keff_scalar("k_abs_tra"),
        "combined":        _keff_combined("k_combined"),
    }
    # Remove missing estimators
    k_effective = {k: v for k, v in k_effective.items() if v is not None}

    # Per-batch histories (active batches only)
    entropy_history: list[float] = []
    keff_history:    list[float] = []

    if "entropy" in sp:
        entropy_history = sp["entropy"][()].tolist()
    if "k_generation" in sp:
        keff_history = sp["k_generation"][()].tolist()

    # Neutron balance
    # global_tallies shape: (4, 3) — columns are [sum, sum_sq, ???]
    # Row order per OpenMC docs: leakage, absorption, fission, nu-fission
    # Divide column 1 (sum) by n_realizations to get the per-batch mean.
    neutron_balance: dict[str, float] = {}
    if "global_tallies" in sp:
        gt = sp["global_tallies"][()]
        if gt.ndim == 2 and gt.shape[1] >= 2:
            labels = ["leakage", "absorption", "fission", "nu_fission"]
            for i, label in enumerate(labels):
                if i < gt.shape[0]:
                    neutron_balance[label] = float(gt[i, 1]) / n_real

    # Timing
    timing: dict[str, float] = {}
    if "runtime" in sp:
        rt = sp["runtime"]
        for key in rt.keys():
            timing[key] = float(rt[key][()])

    return {
        "job_id":               job_id,
        "batches":              batches,
        "inactive":             inactive,
        "particles_per_batch":  pps,
        "k_effective":          k_effective,
        "entropy_history":      entropy_history,
        "keff_history":         keff_history,
        "neutron_balance":      neutron_balance,
        "timing":               timing,
    }


# ---------------------------------------------------------------------------
# /tallies
# ---------------------------------------------------------------------------

@router.get("/{job_id}/tallies")
async def get_tallies(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return scalar cell tally results — mean + relative std dev per cell/score.

    Response schema::

        {
          "job_id": "...",
          "tallies": [
            {
              "tally_id": 101,
              "name": "cell_1_scalars",
              "scores": {
                "flux":       {"mean": 3.14e12, "std_dev": 0.0023},
                "fission":    {"mean": 1.02e11, "std_dev": 0.0031},
                ...
              }
            },
            ...
          ]
        }
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    sp_path, sp = _open_statepoint(job)
    try:
        return _parse_tallies(job_id, sp)
    finally:
        sp.close()


def _parse_tallies(job_id: str, sp: h5py.File) -> dict[str, Any]:
    tallies_grp = sp.get("tallies")
    if tallies_grp is None:
        return {"job_id": job_id, "tallies": []}

    # n_realizations is needed to convert raw sums → per-batch means.
    n_real = int(sp["n_realizations"][()]) if "n_realizations" in sp else 1

    result: list[dict] = []

    for tally_key in tallies_grp.keys():
        t = tallies_grp[tally_key]
        tally_id = int(t.attrs.get("id", 0))

        # Only surface scalar tallies (101–199); mesh and spectra have
        # dedicated endpoints.
        if not (100 < tally_id < 200):
            continue

        name = t.attrs.get("name", tally_key)
        if isinstance(name, bytes):
            name = name.decode()

        scores_list: list[str] = []
        if "score_bins" in t:
            scores_list = [
                s.decode() if isinstance(s, bytes) else s
                for s in t["score_bins"][()]
            ]

        # results shape: (n_filter_bins, n_score_bins, 2)
        # arr[..., 0] = sum of scores across realizations
        # arr[..., 1] = sum of squares across realizations
        results_data = t.get("results")
        if results_data is None:
            continue
        arr = results_data[()]

        print(f"[DEBUG] tally {tally_id} arr[0,:,:] =\n{arr[0, :, :]}")

        scores_out: dict[str, dict] = {}
        for si, score in enumerate(scores_list):
            if si >= arr.shape[1]:
                break
            raw_sum = float(arr[0, si, 0])
            raw_sum_sq = float(arr[0, si, 1]) if arr.shape[2] > 1 else 0.0

            mean = raw_sum / n_real
            # Population variance of the batch means, then std dev of the mean
            variance = (raw_sum_sq / n_real - mean ** 2) / n_real
            std_dev  = float(np.sqrt(max(variance, 0.0)))
            rel_err  = (std_dev / mean) if mean != 0.0 else 0.0

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
            {"ix": 0, "iy": 0, "iz": 0, "flux_mean": ..., "flux_std_dev": ...},
            ...
          ]
        }

    The ``data`` array is ordered (ix, iy, iz) with ix varying slowest —
    consistent with NumPy C-order flattening of a (nx, ny, nz) array.
    For large meshes (>50³) consider the binary /mesh/raw endpoint instead.
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
        return _parse_mesh(job_id, sp, job.results_config.mesh.mesh_type)
    finally:
        sp.close()


def _parse_mesh(
    job_id: str,
    sp: h5py.File,
    mesh_type: MeshType,
) -> dict[str, Any]:
    tallies_grp = sp.get("tallies")
    if tallies_grp is None:
        raise HTTPException(status_code=404, detail="No tallies in statepoint.")

    # Find tally ID 200
    mesh_tally = None
    for key in tallies_grp.keys():
        t = tallies_grp[key]
        if int(t.attrs.get("id", -1)) == 200:
            mesh_tally = t
            break

    if mesh_tally is None:
        raise HTTPException(status_code=404, detail="Mesh tally (id=200) not found.")

    # Locate mesh metadata under /tallies/meshes
    meshes_grp = tallies_grp.get("meshes")
    mesh_meta: dict[str, Any] = {}
    if meshes_grp is not None and "1" in meshes_grp:
        m = meshes_grp["1"]
        shape       = m["dimension"][()] if "dimension" in m else []
        lower_left  = m["lower_left"][()] .tolist() if "lower_left"  in m else []
        upper_right = m["upper_right"][()].tolist() if "upper_right" in m else []
        mesh_meta   = {
            "type":        mesh_type.value,
            "shape":       shape.tolist() if hasattr(shape, "tolist") else list(shape),
            "lower_left":  lower_left,
            "upper_right": upper_right,
        }

    # Score names
    scores_list: list[str] = []
    if "score_bins" in mesh_tally:
        scores_list = [
            s.decode() if isinstance(s, bytes) else s
            for s in mesh_tally["score_bins"][()]
        ]

    results = mesh_tally.get("results")
    if results is None:
        return {"job_id": job_id, "tally_id": 200, "mesh": mesh_meta,
                "scores": scores_list, "data": []}

    arr = results[()]  # shape: (nx*ny*nz, n_scores, 2)
    shape = mesh_meta.get("shape", [1, 1, 1])
    nx, ny, nz = (shape + [1, 1, 1])[:3]

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
                    mean    = float(arr[idx, si, 0])
                    rel_err = float(arr[idx, si, 1]) if arr.shape[2] > 1 else 0.0
                    row[f"{score}_mean"]    = mean
                    row[f"{score}_std_dev"] = rel_err * mean if mean != 0.0 else 0.0
                data.append(row)
                idx += 1

    return {
        "job_id":    job_id,
        "tally_id":  200,
        "mesh":      mesh_meta,
        "scores":    scores_list,
        "data":      data,
    }


# ---------------------------------------------------------------------------
# /spectra
# ---------------------------------------------------------------------------

@router.get("/{job_id}/spectra")
async def get_spectra(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return energy flux spectra per material (or global if per_material=False).

    Response schema::

        {
          "job_id": "...",
          "group_structure": "69",
          "spectra": [
            {
              "tally_id": 301,
              "name": "spectrum_fuel",
              "group_boundaries_ev": [1e-5, 1e-4, ...],
              "flux_mean":    [3.1e12, 2.8e12, ...],
              "flux_std_dev": [1.2e10, 9.8e9,  ...]
            },
            ...
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
        return _parse_spectra(job_id, sp, job.results_config)
    finally:
        sp.close()


def _parse_spectra(
    job_id: str,
    sp: h5py.File,
    results_config,
) -> dict[str, Any]:
    tallies_grp = sp.get("tallies")
    if tallies_grp is None:
        return {"job_id": job_id, "spectra": []}

    group_structure = results_config.spectra.group_structure.value
    boundaries      = results_config.spectra.boundaries()

    spectra_out: list[dict] = []

    for key in tallies_grp.keys():
        t = tallies_grp[key]
        tally_id = int(t.attrs.get("id", -1))
        if tally_id < 301:
            continue

        name = t.attrs.get("name", key)
        if isinstance(name, bytes):
            name = name.decode()

        results = t.get("results")
        if results is None:
            continue
        arr = results[()]   # shape: (n_energy_bins, 1_score, 2)

        n_bins = arr.shape[0]
        flux_mean    = [float(arr[i, 0, 0]) for i in range(n_bins)]
        flux_std_dev = [
            float(arr[i, 0, 1]) * float(arr[i, 0, 0])
            if arr.shape[2] > 1 and float(arr[i, 0, 0]) != 0.0 else 0.0
            for i in range(n_bins)
        ]

        spectra_out.append({
            "tally_id":           tally_id,
            "name":               name,
            "group_boundaries_ev": boundaries,
            "flux_mean":          flux_mean,
            "flux_std_dev":       flux_std_dev,
        })

    spectra_out.sort(key=lambda x: x["tally_id"])
    return {
        "job_id":          job_id,
        "group_structure": group_structure,
        "spectra":         spectra_out,
    }


# ---------------------------------------------------------------------------
# /statepoint/path  (utility — lets UI build a download link)
# ---------------------------------------------------------------------------

@router.get("/{job_id}/statepoint/path")
async def get_statepoint_path(
    job_id: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Return the filesystem path of the final statepoint file.

    Useful for the frontend to construct a download link or display
    the file location to advanced users who want to load it themselves.

    Response::

        {"job_id": "...", "path": "/home/user/.cascade/jobs/<id>/output/statepoint.100.h5"}
    """
    job = _get_job_or_404(job_id, db)
    _require_completed(job)

    sp_path = _find_statepoint(job)
    return {"job_id": job_id, "path": str(sp_path)}