import unittest

from ...cascade.domain.geometry import CascadeGeometry
from ...cascade.domain.material import Material
from ...cascade.domain.result import TallyResult
from ...cascade.services.geometry_service import GeometryService
from ...cascade.services.material_service import MaterialService
from ...cascade.services.job_service import JobService
from ...cascade.services.result_service import ResultService
class ServiceTests(unittest.TestCase):
    def test_register_and_list_geometry(self) -> None:
        service = GeometryService()
        geometry = CascadeGeometry(id="geom-1", name="Geometry")
        service.register(geometry)
        self.assertEqual(service.list()[0].id, "geom-1")

    def test_register_and_list_material(self) -> None:
        service = MaterialService()
        service.register(Material(id="mat-1", name="Steel"))
        self.assertEqual(service.get("mat-1").name, "Steel")

    def test_job_and_result_services(self) -> None:
        job_service = JobService()
        result_service = ResultService()
        job = job_service.create_job("geom-1", ["mat-1"])
        result_service.record(TallyResult(job_id=job.id, tally="flux", value=1.0))
        self.assertEqual(len(result_service.list(job.id)), 1)


if __name__ == "__main__":
    unittest.main()

