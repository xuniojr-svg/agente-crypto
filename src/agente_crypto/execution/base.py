"""Interface que toda exchange (simulada ou real) implementa.

Por projeto, NÃO existe método de saque, transferência ou alteração de chave.
O teste tests/test_no_withdrawal.py falha se alguém adicionar um.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from ..domain import ApprovedOrder, Fill


class ExchangeAdapter(Protocol):
    name: str

    def balances(self) -> dict[str, Decimal]: ...

    def place_order(self, order: ApprovedOrder) -> list[Fill]: ...

    def cancel_all(self) -> int: ...


def require_approved(order: object) -> ApprovedOrder:
    """Toda implementação chama isto antes de enviar qualquer coisa."""
    if not isinstance(order, ApprovedOrder):
        raise PermissionError(f"executor recusou {type(order).__name__}: só aceita ApprovedOrder")
    return order
