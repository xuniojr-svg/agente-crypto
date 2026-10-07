"""Kill switch: se o arquivo existir, nada novo é executado e ordens abertas são canceladas.

Ligar:    touch dados/KILL   (ou `agente-crypto kill`)
Desligar: apagar o arquivo, conscientemente.
"""

from __future__ import annotations

from pathlib import Path


class KillSwitch:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def is_active(self) -> bool:
        return self.path.exists()

    def activate(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(reason + "\n")

    def reason(self) -> str | None:
        return self.path.read_text().strip() if self.is_active() else None
