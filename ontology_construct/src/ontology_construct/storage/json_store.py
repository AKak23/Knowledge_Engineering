from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)


def save_json_model(model: BaseModel, path: Path | str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(model.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_json_model(model_type: type[T], path: Path | str) -> T:
    input_path = Path(path)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    return model_type.model_validate(data)
