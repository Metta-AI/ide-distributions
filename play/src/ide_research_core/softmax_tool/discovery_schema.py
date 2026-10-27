"""Discovery arguments shared with the Hermes host, without research-runtime imports."""

from pydantic import BaseModel, ConfigDict, Field


class SupportRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    league_id: str = Field(min_length=1)
    document: str | None = Field(default=None, min_length=1, max_length=160)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=6000)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")



class StarterRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    league_id: str = Field(min_length=1)
    starter_id: str = Field(min_length=1, max_length=160)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=6000)
