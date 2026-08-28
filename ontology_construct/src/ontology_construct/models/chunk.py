from __future__ import annotations

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    id: str
    text: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    token_count: int = Field(ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)
