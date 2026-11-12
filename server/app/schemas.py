"""Public proxy input contract."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
