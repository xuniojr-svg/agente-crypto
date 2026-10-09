"""Exchange simulada.

- Ordem que cruza o livro (compra com limite >= ask): executa na hora, paga taxa taker e slippage.
- Ordem passiva (compra com limite abaixo do ask): fica no livro. Se o próximo candle
  passar ESTRITAMENTE do preço limite, executa no limite pagando taxa maker; senão é cancelada.
  Exigir que o preço passe do limite (e não só encoste) é conservador: na vida real
  há fila de ordens no mesmo preço.

Os saldos da exchange servem para a reconciliação com o ledger.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List

from ..config import FeeModel
from ..domain import ApprovedOrder, Candle, Fill, MarketSnapshot, Side, base_asset, quote_asset
from .base import require_approved

CENT = Decimal("0.01")


@dataclass(frozen=True)
class _Aceita:
    """Ordem que a exchange já aceitou. A partir daqui ela é estado da exchange
    (como numa exchange real), e pode ser salva e recarregada entre execuções."""

    intent_id: str
    symbol: str
    side: Side
    quantity: Decimal
    limit_price: Decimal

    @classmethod
    def de(cls, order: ApprovedOrder) -> "_Aceita":
        return cls(order.intent.id, order.symbol, order.side, order.quantity, order.limit_price)


class PaperExchange:
    def __init__(self, fees: FeeModel, initial_balances: Dict[str, Decimal], resting_ttl: int = 1):
        self.name = f"paper:{fees.exchange}"
        self.fees = fees
        self.resting_ttl = resting_ttl  # quantos candles uma ordem passiva espera
        self._balances: Dict[str, Decimal] = defaultdict(Decimal, initial_balances)
        self._market: Dict[str, MarketSnapshot] = {}
        self._resting: List[list] = []  # [_Aceita, candles restantes]
        self._pending_fills: List[Fill] = []
        self._seq = 0

    # o motor alimenta o preço atual
    def update_market(self, snap: MarketSnapshot) -> None:
        self._market[snap.symbol] = snap

    def balances(self) -> Dict[str, Decimal]:
        return {k: v for k, v in self._balances.items() if v != 0}

    def open_orders(self) -> int:
        return len(self._resting)

    def cancel_all(self) -> int:
        n = len(self._resting)
        self._resting.clear()
        return n

    def export_book(self) -> dict:
        """Ordens no livro e contador, para guardar entre execuções (modo diário)."""
        return {
            "seq": self._seq,
            "ordens": [
                {**{k: str(v) for k, v in asdict(o).items()}, "side": o.side.value, "ttl": ttl}
                for o, ttl in self._resting
            ],
        }

    def import_book(self, book: dict) -> None:
        self._seq = int(book.get("seq", 0))
        self._resting = [
            [_Aceita(o["intent_id"], o["symbol"], Side(o["side"]), Decimal(o["quantity"]),
                     Decimal(o["limit_price"])), int(o["ttl"])]
            for o in book.get("ordens", [])
        ]

    def poll_fills(self) -> List[Fill]:
        out, self._pending_fills = self._pending_fills, []
        return out

    def process_candle(self, symbol: str, candle: Candle) -> None:
        """Confere as ordens passivas contra um candle novo (posterior à ordem)."""
        keep = []
        for entry in self._resting:
            order, ttl = entry
            if order.symbol != symbol:
                keep.append(entry)
                continue
            touched = candle.low < order.limit_price if order.side is Side.BUY else candle.high > order.limit_price
            fill = None
            if touched:
                fill = self._settle(order, order.limit_price, self.fees.maker_pct, candle.ts)
            if fill:
                self._pending_fills.append(fill)
            elif ttl > 1 and not touched:
                keep.append([order, ttl - 1])
        self._resting = keep

    def place_order(self, order: ApprovedOrder) -> List[Fill]:
        order = _Aceita.de(require_approved(order))
        snap = self._market[order.symbol]
        slip = self.fees.slippage_pct / 100
        if order.side is Side.BUY:
            price = snap.ask * (1 + slip)
            marketable = price <= order.limit_price
        else:
            price = snap.bid * (1 - slip)
            marketable = price >= order.limit_price
        if marketable:
            fill = self._settle(order, price, self.fees.taker_pct, snap.ts)
            return [fill] if fill else []
        if self.fees.order_type == "maker":
            self._resting.append([order, self.resting_ttl])
        return []

    def _settle(self, order: _Aceita, price: Decimal, fee_pct: Decimal, ts) -> Fill | None:
        qty = order.quantity
        gross = qty * price
        fee = (gross * fee_pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        base, quote = base_asset(order.symbol), quote_asset(order.symbol)
        if order.side is Side.BUY:
            if self._balances[quote] < gross + fee:
                return None
            self._balances[quote] -= gross + fee
            self._balances[base] += qty
        else:
            if self._balances[base] < qty:
                return None
            self._balances[base] -= qty
            self._balances[quote] += gross - fee
        self._seq += 1
        return Fill(
            order_id=f"{order.intent_id}-{self._seq}",
            symbol=order.symbol,
            side=order.side,
            quantity=qty,
            price=price,
            fee_brl=fee,
            ts=ts,
            exchange=self.name,
        )
