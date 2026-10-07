from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agente_crypto.config import load_risk_limits, load_strategy_config
from agente_crypto.domain import MarketSnapshot, OrderIntent, Side
from agente_crypto.portfolio import Portfolio

CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
PRICE = Decimal("350000")


@pytest.fixture(scope="session")
def limits():
    return load_risk_limits(CONFIG / "risk_limits.yaml")


@pytest.fixture
def cfg():
    return load_strategy_config(CONFIG / "strategy.yaml")


def snap(price=PRICE, ts=NOW, spread_pct=Decimal("0.1"), symbol="BTC/BRL"):
    half = price * spread_pct / 200
    return MarketSnapshot(symbol=symbol, ts=ts, last=price, bid=price - half, ask=price + half)


def intent(side=Side.BUY, notional="300", price=PRICE, symbol="BTC/BRL"):
    return OrderIntent(
        symbol=symbol, side=side, notional_brl=Decimal(notional),
        limit_price=Decimal(price), reason="teste", source="teste",
    )


def fresh_portfolio(cash="5000", prices=None):
    p = Portfolio(cash_brl=Decimal(cash))
    p.roll_day(NOW, prices or {"BTC": PRICE})
    return p
