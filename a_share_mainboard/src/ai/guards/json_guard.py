from __future__ import annotations

from pydantic import BaseModel, ValidationError


def validate_payload(model_type: type[BaseModel], payload: dict) -> BaseModel:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Invalid AI payload: {exc}") from exc

