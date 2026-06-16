"""Result HTTP routes."""
from __future__ import annotations
from fastapi import APIRouter, Query
from ..domain.result import TallyResult
from ..services.result_service import ResultService
router = APIRouter(prefix="/results", tags=["results"])
result_service = ResultService()
result_service.record(TallyResult(job_id="demo-job", tally="flux", value=1.23, uncertainty=0.04, units="n/cm^2/s"))
@router.get("/")
def list_results(job_id: str | None = Query(default=None)) -> list[dict[str, object]]:
    return [result.to_dict() for result in result_service.list(job_id)]
@router.post("/record")
def record_result(
    job_id: str,
    tally: str,
    value: float,
    uncertainty: float = 0.0,
    units: str | None = None,
) -> dict[str, object]:
    result = result_service.record(
        TallyResult(job_id=job_id, tally=tally, value=value, uncertainty=uncertainty, units=units)
    )
    return result.to_dict()

