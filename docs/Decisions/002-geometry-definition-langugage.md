Title: Choose a type based YAML format for defining geometries
Date: 2026-6-16
Status: Accepted

Context
-------
This is an academic research project with users who may be less familiar with programming languages and existing complex geometry definition formats (think openMC xml format). We need a simple, human-readable language for defining geometries that can be easily parsed and validated.

Decision
--------
We will use a type-based YAML format for defining geometries. This format will allow users to define geometries in a structured way, with clear types and properties, while remaining human-readable and easy to edit.

Consequences
------------
- Users will be able to define geometries in a clear and structured way, reducing the likelihood of errors.
- The YAML format is widely used and supported, making it easier for users to learn and adopt.
- The type-based approach will allow for better validation and error checking, ensuring that geometries are defined correctly before being used in simulations.
- The format will be extensible, allowing for future additions and modifications as the project evolves.
- The choice of YAML may introduce some complexity in parsing and validation, but the benefits of human readability and ease of use outweigh these concerns.
- The necessity of having predefined components and types may limit flexibility for advanced users, but this trade-off is mitigated by the ability to extend the format in the future.

