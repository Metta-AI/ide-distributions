"""The text-only OpenAI user-message shape accepted by this application."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user"]
    content: str = Field(min_length=1, max_length=50000)
