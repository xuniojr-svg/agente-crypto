"""Carrega a configuração. Os limites ficam congelados depois de carregados."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RiskLimits:
    max_order_brl: Decimal
    max_daily_buy_brl: Decimal
    max_daily_loss_pct: Decimal
    max_exposure_pct_per_asset: Decimal
    max_open_positions: int
    max_orders_per_hour: int
    max_price_deviation_pct: Decimal
    max_data_age_seconds: int
    max_spread_pct: Decimal
    min_order_brl: Decimal
    allowed_symbols: frozenset[str]
    version: str  # hash do arquivo, gravado em cada decisão

    def __post_init__(self) -> None:
        if self.max_order_brl <= 0 or self.min_order_brl <= 0:
            raise ValueError("limites de ordem devem ser positivos")
        if self.min_order_brl > self.max_order_brl:
            raise ValueError("min_order_brl > max_order_brl")
        if not (0 < self.max_exposure_pct_per_asset <= 100):
            raise ValueError("max_exposure_pct_per_asset fora de (0, 100]")
        if not self.allowed_symbols:
            raise ValueError("allowed_symbols vazio")


@dataclass(frozen=True)
class FeeModel:
    exchange: str
    taker_pct: Decimal
    maker_pct: Decimal
    slippage_pct: Decimal
    # "taker": executa na hora contra o livro (paga taker_pct).
    # "maker": ordem limitada fica no livro até o próximo candle (paga maker_pct se executar).
    order_type: str = "taker"

    def __post_init__(self) -> None:
        if self.order_type not in ("taker", "maker"):
            raise ValueError("order_type deve ser 'taker' ou 'maker'")


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    version: str
    symbol: str
    timeframe: str
    fast: int
    slow: int
    order_brl: Decimal
    fees: FeeModel
    initial_cash_brl: Decimal


def _d(v) -> Decimal:
    return Decimal(str(v))


def load_risk_limits(path: str | Path) -> RiskLimits:
    raw_bytes = Path(path).read_bytes()
    raw = yaml.safe_load(raw_bytes)
    return RiskLimits(
        max_order_brl=_d(raw["max_order_brl"]),
        max_daily_buy_brl=_d(raw["max_daily_buy_brl"]),
        max_daily_loss_pct=_d(raw["max_daily_loss_pct"]),
        max_exposure_pct_per_asset=_d(raw["max_exposure_pct_per_asset"]),
        max_open_positions=int(raw["max_open_positions"]),
        max_orders_per_hour=int(raw["max_orders_per_hour"]),
        max_price_deviation_pct=_d(raw["max_price_deviation_pct"]),
        max_data_age_seconds=int(raw["max_data_age_seconds"]),
        max_spread_pct=_d(raw["max_spread_pct"]),
        min_order_brl=_d(raw["min_order_brl"]),
        allowed_symbols=frozenset(raw["allowed_symbols"]),
        version=hashlib.sha256(raw_bytes).hexdigest()[:12],
    )


def load_strategy_config(path: str | Path) -> StrategyConfig:
    raw = yaml.safe_load(Path(path).read_text())
    f = raw["fees"]
    return StrategyConfig(
        name=raw["name"],
        version=str(raw["version"]),
        symbol=raw["symbol"],
        timeframe=raw["timeframe"],
        fast=int(raw["fast"]),
        slow=int(raw["slow"]),
        order_brl=_d(raw["order_brl"]),
        fees=FeeModel(
            exchange=f["exchange"],
            taker_pct=_d(f["taker_pct"]),
            maker_pct=_d(f["maker_pct"]),
            slippage_pct=_d(f["slippage_pct"]),
            order_type=f.get("order_type", "taker"),
        ),
        initial_cash_brl=_d(raw["initial_cash_brl"]),
    )
