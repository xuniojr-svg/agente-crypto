"""Exchange simulada: executa ordens limitadas contra o preço observado,
aplicando taxa e slippage do FeeModel. Mantém os próprios saldos, que
servem para a reconciliação com o ledger."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from ..config import FeeModel
from ..domain import ApprovedOrder, Fill, MarketSnapshot, Side, base_asset, quote_asset
from .base import require_approved

CENT = Decimal("0.01")


class PaperExchange:
    def __init__(self, fees: FeeModel, initial_balances: dict[str, Decimal]):
        self.name = f"paper:{fees.exchange}"
        self.fees = fees
        self._balances: dict[str, Decimal] = defaultdict(Decimal, initial_balances)
        self._market: dict[str, MarketSnapshot] = {}
        self._seq = 0

    # o motor alimenta o preço atual
    def update_market(self, snap: MarketSnapshot) -> None:
        self._market[snap.symbol] = snap

    def balances(self) -> dict[str, Decimal]:
        return {k: v for k, v in self._balances.items() if v != 0}

    def cancel_all(self) -> int:
        return 0  # ordens simuladas são executadas ou descartadas na hora

    def place_order(self, order: ApprovedOrder) -> list[Fill]:
        order = require_approved(order)
        snap = self._market[order.symbol]
        slip = self.fees.slippage_pct / 100
        if order.side is Side.BUY:
            price = snap.ask * (1 + slip)
            if price > order.limit_price:
                return []  # limite não alcançado: não executa
        else:
            price = snap.bid * (1 - slip)
            if price < order.limit_price:
                return []
        qty = order.quantity
        gross = qty * price
        fee = (gross * self.fees.taker_pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        base, quote = base_asset(order.symbol), quote_asset(order.symbol)
        if order.side is Side.BUY:
            if self._balances[quote] < gross + fee:
                return []
            self._balances[quote] -= gross + fee
            self._balances[base] += qty
        else:
            if self._balances[base] < qty:
                return []
            self._balances[base] -= qty
            self._balances[quote] += gross - fee
        self._seq += 1
        return [
            Fill(
                order_id=f"{order.intent.id}-{self._seq}",
                symbol=order.symbol,
                side=order.side,
                quantity=qty,
                price=price,
                fee_brl=fee,
                ts=snap.ts,
                exchange=self.name,
            )
        ]
