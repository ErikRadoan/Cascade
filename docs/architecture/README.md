# Architecture Overview

This directory contains high-level design architecture diagrams.

## Geometry Definition Language (GDL)

The Geometry Definition Language (GDL) is a type-based YAML format used to define simulation geometries.

The language is designed for academic and research environments where users may not be familiar with programming languages or complex geometry formats. By using YAML and predefined component types, GDL provides a human-readable way to describe geometries while still supporting validation, parameter studies, and automated geometry generation.

Rather than constructing surfaces and cells directly, users define higher-level components and their properties. The system then translates these definitions into the internal geometry representation used by the simulation framework.

## Architecture

Geometry definitions pass through several processing stages before becoming simulation-ready objects.

```text
YAML Document
     ↓
Schema Validation
     ↓
Sweep Expansion
     ↓
Component Resolution
     ↓
CascadeGeometry
     ↓
Adapters
```

### Schema Validation

Input documents are validated using Pydantic models. Each component type defines its own schema, allowing errors to be detected early and reported with clear messages.

### Sweep Expansion

Parameter sweeps are expanded before geometry generation. Any values defined using `sweep()` expressions are detected and expanded into all required parameter combinations.

### Component Resolution

Validated component definitions are resolved into concrete geometry primitives using the ComponentDef system. For example, a `FuelPin` component may generate multiple surfaces and cells required to represent the final geometry.

### CascadeGeometry

Resolved geometry primitives are assembled into a `CascadeGeometry` instance. This serves as the canonical internal representation of the geometry and remains independent of any specific simulation backend.

### Adapters

Adapters convert the internal geometry representation into formats required by downstream tools and simulation engines. Because all geometries pass through the same internal representation, additional backends can be supported without modifying the language itself.

## Design Goals

The architecture is designed around the following principles:

* **Human readability** — geometry definitions should be easy to read and edit.
* **Strong validation** — invalid configurations should be detected before geometry generation.
* **Extensibility** — new component types can be introduced without redesigning the language.
* **Backend independence** — geometry definitions should not depend on a specific simulation engine.
* **Reproducibility** — geometry generation should follow a deterministic and well-defined pipeline.
