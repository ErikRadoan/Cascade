---
hide:
  - navigation  # Hides the entire left navigation sidebar
---

# Cascade

Scientific Molten Salt Reactor (MSR) Simulation Platform

---

Cascade is a high-fidelity, multiphysics simulation framework designed specifically for Molten Salt Reactors. By coupling reactor physics, thermal hydraulics, and isotopic burnup evolution, Cascade provides researchers and engineers with a unified toolchain to analyze transient behavior, fuel cycle kinetics, and safety margins in liquid-fueled systems.

## Key Capabilities

* **Reactor Physics:** Neutronics modeling tailored for moving fluid fuels, accounting for delayed neutron precursor drift.
* **Thermal Hydraulics:** Convective heat transfer and fluid dynamics loops mapping salt-velocity profiles and temperature distributions.
* **Burnup Evolution:** Continuous online refueling and continuous fission product removal (reprocessing) simulations.

---

## Getting Started

Depending on your role, here is where you should dive into the documentation:

### 📖 User Guide
If you are looking to install Cascade, set up your first input files, or run an end-to-end reactor simulation.
* [Go to the User Guide Overview](User Guide/README.md)

### 🏗️ Architecture & Codebase
For developers and contributors wanting to understand how the platform components communicate or how to write custom plugins.
* [Explore the Architecture](Architecture/index.md)

### ⚙️ API Reference
Direct access to the underlying endpoints, schemas, and interactive Swagger/ReDoc console.
* [Open the API Explorer](API/index.md)

### 📐 Architecture Decision Records (ADRs)
Read up on why technical choices were made, including why we chose Svelte, FastAPI, and `uv`.
* [Review Design Decisions](Decisions/001-svelte-fastapi-uv.md)

---

## Project Layout

A quick look at how the documentation and codebase is organized:

* `docs/User Guide/` — Installation guides, tutorials, and configuration specs.
* `docs/Architecture/` — Subsystem designs and data-flow pipelines.
* `docs/Decisions/` — Architectural history and design trade-offs.
* `docs/API/` — ReDoc integration for backend interfaces.