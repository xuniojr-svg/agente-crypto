"""Estado da carteira derivado apenas dos fills registrados."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .domain import Fill, Side, base_asset

ZERO = Decimal("0")


@dataclass
class Position:
    quantity: Decimal = ZERO
    avg_cost_brl: Decimal = ZERO  # custo médio por unidade, incluindo taxas de compra


@dataclass
class Portfolio:
    cash_brl: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    # controle diário (dia UTC)
    day: str | None = None
    day_start_equity: Decimal = ZERO
    day_bought_brl: Decimal = ZERO
    order_times: list[datetime] = field(default_factory=list)
    realized_pnl_brl: Decimal = ZERO
    fees_paid_brl: Decimal = ZERO

    def qty(self, asset: str) -> Decimal:
        p = self.positions.get(asset)
        return p.quantity if p else ZERO

    def open_assets(self) -> set[str]:
        return {a for a, p in self.positions.items() if p.quantity > 0}

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        total = self.cash_brl
        for asset, p in self.positions.items():
            if p.quantity > 0:
                total += p.quantity * prices[asset]
        return total

    def roll_day(self, now: datetime, prices: dict[str, Decimal]) -> None:
        d = now.date().isoformat()
        if d != self.day:
            self.day = d
            self.day_start_equity = self.equity(prices)
            self.day_bought_brl = ZERO

    def orders_last_hour(self, now: datetime) -> int:
        cutoff = now - timedelta(hours=1)
        self.order_times = [t for t in self.order_times if t > cutoff]
        return len(self.order_times)

    def apply_fill(self, fill: Fill) -> Decimal:
        """Aplica um fill. Devolve o resultado realizado (venda) ou zero (compra)."""
        asset = base_asset(fill.symbol)
        pos = self.positions.setdefault(asset, Position())
        self.fees_paid_brl += fill.fee_brl
        self.order_times.append(fill.ts)
        if fill.side is Side.BUY:
            cost = fill.gross_brl + fill.fee_brl
            new_qty = pos.quantity + fill.quantity
            pos.avg_cost_brl = (pos.avg_cost_brl * pos.quantity + cost) / new_qty
            pos.quantity = new_qty
            self.cash_brl -= cost
            self.day_bought_brl += fill.gross_brl
            return ZERO
        if fill.quantity > pos.quantity:
            raise ValueError("venda maior que a posição (venda a descoberto não é permitida)")
        proceeds = fill.gross_brl - fill.fee_brl
        realized = proceeds - pos.avg_cost_brl * fill.quantity
        pos.quantity -= fill.quantity
        if pos.quantity == 0:
            pos.avg_cost_brl = ZERO
        self.cash_brl += proceeds
        self.realized_pnl_brl += realized
        return realized
