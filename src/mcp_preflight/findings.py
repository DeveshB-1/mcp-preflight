from __future__ import annotations

from dataclasses import dataclass

LEVELS = ("info", "warn", "error")


@dataclass(frozen=True)
class Finding:
    level: str  # info | warn | error
    code: str
    subject: str  # tool name, "transport", "oauth", ...
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "subject": self.subject, "message": self.message}


def worst(findings: list[Finding]) -> str:
    rank = {lvl: i for i, lvl in enumerate(LEVELS)}
    return max((f.level for f in findings), key=rank.__getitem__, default="info")
