"""Tipos do domínio. Dinheiro e quantidades sempre em Decimal."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Candle:
    ts: datetime  # abertura do candle, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class MarketSnapshot:
    """O que o sistema sabe do mercado num instante."""

    symbol: str
    ts: datetime  # quando o dado foi observado
    last: Decimal
    bid: Decimal
    ask: Decimal

    @property
    def spread_pct(self) -> Decimal:
        mid = (self.bid + self.ask) / 2
        return (self.ask - self.bid) / mid * 100


@dataclass(frozen=True)
class OrderIntent:
    """O que a estratégia (ou uma IA) GOSTARIA de fazer. Não é executável."""

    symbol: str
    side: Side
    notional_brl: Decimal  # valor em BRL desejado
    limit_price: Decimal
    reason: str
    source: str  # quem propôs: "strategy:sma_cross@0.1", "llm:..." etc.
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


# Token privado: só o motor de risco tem acesso a ele.
# O executor recusa qualquer ordem que não seja ApprovedOrder com este token.
_RISK_TOKEN = object()


@dataclass(frozen=True)
class ApprovedOrder:
    intent: OrderIntent
    quantity: Decimal  # quantidade do ativo base
    approved_at: datetime
    limits_version: str
    _token: object = field(repr=False, compare=False, default=None)

    def __post_init__(self) -> None:
        if self._token is not _RISK_TOKEN:
            raise PermissionError("ApprovedOrder só pode ser criada pelo motor de risco")

    @property
    def symbol(self) -> str:
        return self.intent.symbol

    @property
    def side(self) -> Side:
        return self.intent.side

    @property
    def limit_price(self) -> Decimal:
        return self.intent.limit_price


@dataclass(frozen=True)
class Rejection:
    intent: OrderIntent
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    price: Decimal
    fee_brl: Decimal
    ts: datetime
    exchange: str

    @property
    def gross_brl(self) -> Decimal:
        return self.quantity * self.price


def base_asset(symbol: str) -> str:
    return symbol.split("/")[0]


def quote_asset(symbol: str) -> str:
    return symbol.split("/")[1]
