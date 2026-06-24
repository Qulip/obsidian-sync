from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DomainValidationError(ValueError):
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
