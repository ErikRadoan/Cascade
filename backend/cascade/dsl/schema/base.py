from fastapi.openapi.models import Schema


class BaseComponentSchema(Schema):
    """Base class for all component schemas."""
    type: str