"""Public proxy input contract."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from uuid import UUID
from typing import ClassVar, Literal, Optional
from visicom_data_api.core.operations import validate_request


class RegistrationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: UUID


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=3, max_length=500)

    @field_validator("text")
    @classmethod
    def meaningful(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("Address is too short")
        return value


class ReverseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)


class OperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def canonical(cls, value):
        return validate_request(cls.operation, value)


class ObjectSearchInput(OperationInput):
    operation = "objects"
    categories: list[str]
    text: str = ""
    limit: int = 100
    near: Optional[tuple[float, float]] = None
    radius: Optional[float] = None
    area: Optional[dict] = None


class FeaturesInput(OperationInput):
    operation = "features"
    ids: list[str]


class RouteInput(OperationInput):
    operation = "route"
    points: list[tuple[float, float]]
    locks: list[tuple[float, float]] = Field(default_factory=list)
    mode: Literal["driving", "driving-shortest", "direct"] = "driving"


class MatrixInput(OperationInput):
    operation = "matrix"
    origins: list[tuple[float, float]]
    destinations: list[tuple[float, float]]
    locks: list[tuple[float, float]] = Field(default_factory=list)
    mode: Literal["driving", "driving-shortest", "direct"] = "driving"


class OptimizeInput(RouteInput):
    operation = "optimize"
    round_trip: bool = True


class SnapInput(OperationInput):
    operation = "snap"
    points: list[tuple[float, float]]
    mode: Literal["driving", "walking"] = "driving"
    separate: bool = False
    interpolate: bool = False


OPERATION_INPUTS = {
    "objects": ObjectSearchInput,
    "features": FeaturesInput,
    "route": RouteInput,
    "matrix": MatrixInput,
    "optimize": OptimizeInput,
    "snap": SnapInput,
}
