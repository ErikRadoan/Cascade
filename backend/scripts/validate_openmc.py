#!/usr/bin/env python3
"""
Validate OpenMC XML input files by running them inside the Podman container.

Usage:
    cd backend
    uv run python scripts/validate_openmc.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PODMAN_IMAGE = "cascade-openmc:latest"

# Host machine nuclear data directory
HOST_NUCLEAR_DATA_DIR = (
    Path.home() / ".cascade" / "data"
)

# Where it appears inside the container
CONTAINER_NUCLEAR_DATA_DIR = "/nuclear-data"

OPENMC_CROSS_SECTIONS = (
    f"{CONTAINER_NUCLEAR_DATA_DIR}/cross_sections.xml"
)

OPENMC_CONDA_ENV = "openmc"

VALIDATION_PARTICLES = 100
VALIDATION_BATCHES = 10
VALIDATION_INACTIVE = 5

# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def make_test_materials():
    from cascade.domain.material import Material

    return [
        Material(
            id="UO2",
            name="Uranium Dioxide",
            density=10.97,
            composition={
                "U235": 0.03072,
                "U238": 0.96928,
                "O16": 2.0,
            },
        ),
        Material(
            id="He",
            name="Helium Gap",
            density=0.0001786,
            composition={"He4": 1.0},
        ),
        Material(
            id="Zr4",
            name="Zircaloy-4",
            density=6.56,
            composition={
                "Zr90": 0.5145,
                "Zr91": 0.1122,
                "Zr92": 0.1715,
                "Zr94": 0.1738,
                "Zr96": 0.0280,
            },
        ),
        Material(
            id="H2O",
            name="Light Water",
            density=0.997,  # g/cm³ at ~25°C
            composition={
                "H1": 2.0,
                "O16": 1.0,
            },
        )
    ]


# ---------------------------------------------------------------------------
# Build XML
# ---------------------------------------------------------------------------

def build_test_input_files(work_dir: Path) -> list[Path]:
    from cascade.dsl.loader import load
    from cascade.dsl.expander import expand
    from cascade.adapters.openmc_adapter import (
        OpenMCAdapter,
        OpenMCRunSettings,
    )

    yaml_text = """
    my_fuel_pin:
      type: FuelPin
      pellet_radius: 0.4096
      pellet_height: 365.76
      pellet_material: UO2
      gap_thickness: 0.0082
      gap_material: He
      clad_thickness: 0.0572
      clad_material: Zr4

    my_box:
      type: Box
      x_size: 1.26
      y_size: 1.26
      z_size: 365.76
      material: H2O
      boundary_type: reflective

    center_pin:
      type: SinglePlacement
      template: my_fuel_pin
      x: 0.0
      y: 0.0
      z: 0.0

    boundary:
      type: SinglePlacement
      template: my_box
      x: 0.0
      y: 0.0
      z: 0.0
    """

    print("  [1/3] Parsing YAML...")
    schemas = load(yaml_text)

    print("  [2/3] Expanding geometry...")
    geometry = expand(schemas)

    print("  [3/3] Exporting XML...")

    settings = OpenMCRunSettings(
        particles=VALIDATION_PARTICLES,
        inactive=VALIDATION_INACTIVE,
        batches=VALIDATION_BATCHES,
        seed=42,
        run_mode="eigenvalue",
    )

    adapter = OpenMCAdapter()

    written = adapter.write_input_files(
        geometry=geometry,
        materials=make_test_materials(),
        output_dir=work_dir,
        settings=settings,
    )

    return written


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_image_exists() -> bool:
    result = subprocess.run(
        ["podman", "image", "exists", PODMAN_IMAGE],
        capture_output=True,
    )
    return result.returncode == 0


def check_openmc_in_image() -> bool:
    cmd = [
        "podman",
        "run",
        "--rm",
        "--volume",
        f"{HOST_NUCLEAR_DATA_DIR}:{CONTAINER_NUCLEAR_DATA_DIR}:z",
        "--env",
        f"OPENMC_CROSS_SECTIONS={OPENMC_CROSS_SECTIONS}",
        PODMAN_IMAGE,
        "bash",
        "-lc",
        (
            "source /opt/miniconda/etc/profile.d/conda.sh && "
            f"conda activate {OPENMC_CONDA_ENV} && "
            "openmc --version"
        ),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        print(result.stdout)
        return True

    print(result.stderr)
    return False


# ---------------------------------------------------------------------------
# OpenMC execution
# ---------------------------------------------------------------------------

def run_openmc_in_container(work_dir: Path) -> tuple[bool, str]:
    cmd = [
        "podman",
        "run",
        "--rm",

        "--volume",
        f"{work_dir}:/work:z",

        "--volume",
        f"{HOST_NUCLEAR_DATA_DIR}:{CONTAINER_NUCLEAR_DATA_DIR}:z",

        "--env",
        f"OPENMC_CROSS_SECTIONS={OPENMC_CROSS_SECTIONS}",

        "--workdir",
        "/work",

        PODMAN_IMAGE,

        "bash",
        "-lc",
        (
            "source /opt/miniconda/etc/profile.d/conda.sh && "
            f"conda activate {OPENMC_CONDA_ENV} && "
            "openmc --geometry-debug"
        ),
    ]

    print("\nRunning:")
    print(" ".join(str(x) for x in cmd))
    print()

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )

    output = result.stdout + result.stderr

    return result.returncode == 0, output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    GREEN = "\033[0;32m"
    RED = "\033[0;31m"
    CYAN = "\033[0;36m"
    NC = "\033[0m"

    print(f"\n{CYAN}{'=' * 60}")
    print("Cascade OpenMC Validation")
    print(f"{'=' * 60}{NC}\n")

    print(f"{CYAN}[1] Checking image...{NC}")

    if not check_image_exists():
        print(f"{RED}Image not found: {PODMAN_IMAGE}{NC}")
        return 1

    print(f"{GREEN}OK{NC}\n")

    print(f"{CYAN}[2] Checking OpenMC installation...{NC}")

    if not check_openmc_in_image():
        print(f"{RED}OpenMC check failed.{NC}")
        return 1

    print(f"{GREEN}OK{NC}\n")

    print(f"{CYAN}[3] Checking nuclear data...{NC}")

    if not HOST_NUCLEAR_DATA_DIR.exists():
        print(
            f"{RED}Missing nuclear data directory:\n"
            f"  {HOST_NUCLEAR_DATA_DIR}{NC}"
        )
        return 1

    print(f"{GREEN}OK{NC}\n")

    with tempfile.TemporaryDirectory(
        prefix="cascade_validate_"
    ) as tmp:
        work_dir = Path(tmp)

        print(f"{CYAN}[4] Building XML files...{NC}")

        try:
            build_test_input_files(work_dir)
        except Exception as e:
            print(f"{RED}{e}{NC}")
            return 1

        print(f"{GREEN}OK{NC}\n")

        print(f"{CYAN}[5] Running OpenMC geometry validation...{NC}")

        try:
            success, output = run_openmc_in_container(work_dir)
        except subprocess.TimeoutExpired:
            print(f"{RED}OpenMC timed out.{NC}")
            return 1

        print(output)

        if success:
            print(f"\n{GREEN}{'=' * 60}")
            print("VALIDATION PASSED")
            print(f"{'=' * 60}{NC}")
            return 0

        print(f"\n{RED}{'=' * 60}")
        print("VALIDATION FAILED")
        print(f"{'=' * 60}{NC}")

        return 1


if __name__ == "__main__":
    sys.exit(main())