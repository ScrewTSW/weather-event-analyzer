from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class BaseDataResult:
    source: str
    acquired_at: float = field(default_factory=time.time)

    def cache_key(self) -> str:
        raise NotImplementedError
