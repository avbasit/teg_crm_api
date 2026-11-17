"""Schemas shared by several areas."""

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base for response models built from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """A page of results."""

    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    """Body of every handled error."""

    detail: str
    code: str
