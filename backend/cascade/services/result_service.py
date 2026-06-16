"""Result service."""
from __future__ import annotations
from ..domain.result import TallyResult
class ResultService:
    def __init__(self) -> None:
        self._results: dict[str, list[TallyResult]] = {}
    def record(self, result: TallyResult) -> TallyResult:
        self._results.setdefault(result.job_id, []).append(result)
        return result
    def list(self, job_id: str | None = None) -> list[TallyResult]:
        if job_id is None:
            return [result for results in self._results.values() for result in results]
        return list(self._results.get(job_id, []))

