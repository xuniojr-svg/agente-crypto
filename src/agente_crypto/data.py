"""Dados de mercado.

- Mercado Bitcoin API v4 pública (não precisa de conta nem de chave).
- Cache em CSV para backtests reprodutíveis.
- Gerador sintético para testes sem rede.
"""

from __future__ import annotations

import csv
import json
import math
import random
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .domain import Candle, MarketSnapshot

MB_BASE = "https://api.mercadobitcoin.net/api/v4"
TIMEFRAMES = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "agente-crypto/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_mb_candles(symbol: str, timeframe: str, days: int) -> list[Candle]:
    """Baixa candles do Mercado Bitcoin em blocos. symbol no formato 'BTC/BRL'."""
    mb_symbol = symbol.replace("/", "-")
    step = TIMEFRAMES[timeframe]
    end = int(time.time())
    start = end - days * 86400
    out: dict[int, Candle] = {}
    chunk = 500 * step
    t = start
    while t < end:
        to = min(t + chunk, end)
        url = f"{MB_BASE}/candles?symbol={mb_symbol}&resolution={timeframe}&from={t}&to={to}"
        data = _get_json(url)
        for i, ts in enumerate(data.get("t", [])):
            out[int(ts)] = Candle(
                ts=datetime.fromtimestamp(int(ts), tz=timezone.utc),
                open=Decimal(str(data["o"][i])),
                high=Decimal(str(data["h"][i])),
                low=Decimal(str(data["l"][i])),
                close=Decimal(str(data["c"][i])),
                volume=Decimal(str(data["v"][i])),
            )
        t = to
        time.sleep(0.3)  # respeita o limite de requisições
    return [out[k] for k in sorted(out)]


def fetch_mb_ticker(symbol: str) -> MarketSnapshot:
    data = _get_json(f"{MB_BASE}/tickers?symbols={symbol.replace('/', '-')}")[0]
    return MarketSnapshot(
        symbol=symbol,
        ts=datetime.fromtimestamp(int(data["date"]), tz=timezone.utc),
        last=Decimal(str(data["last"])),
        bid=Decimal(str(data["buy"])),
        ask=Decimal(str(data["sell"])),
    )


def save_csv(candles: list[Candle], path: str | Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.ts.isoformat(), c.open, c.high, c.low, c.close, c.volume])


def load_csv(path: str | Path) -> list[Candle]:
    with open(path, newline="") as f:
        return [
            Candle(
                ts=datetime.fromisoformat(r["ts"]),
                open=Decimal(r["open"]),
                high=Decimal(r["high"]),
                low=Decimal(r["low"]),
                close=Decimal(r["close"]),
                volume=Decimal(r["volume"]),
            )
            for r in csv.DictReader(f)
        ]


def synthetic_candles(
    n: int,
    start_price: float = 350_000.0,
    vol_per_step: float = 0.01,
    drift: float = 0.0,
    seed: int = 42,
    timeframe: str = "1h",
    start: datetime | None = None,
) -> list[Candle]:
    """Passeio aleatório (movimento browniano geométrico) para testes."""
    rng = random.Random(seed)
    step = timedelta(seconds=TIMEFRAMES[timeframe])
    ts = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = start_price
    out = []
    for _ in range(n):
        o = price
        price = price * math.exp(drift + vol_per_step * rng.gauss(0, 1))
        hi = max(o, price) * (1 + abs(rng.gauss(0, vol_per_step / 4)))
        lo = min(o, price) * (1 - abs(rng.gauss(0, vol_per_step / 4)))
        q = Decimal("0.01")
        out.append(
            Candle(ts, Decimal(o).quantize(q), Decimal(hi).quantize(q), Decimal(lo).quantize(q),
                   Decimal(price).quantize(q), Decimal("1"))
        )
        ts += step
    return out


def snapshot_from_candle(symbol: str, c: Candle, timeframe: str, spread_pct: Decimal) -> MarketSnapshot:
    """No backtest, o 'agora' é o fechamento do candle."""
    half = spread_pct / 200
    return MarketSnapshot(
        symbol=symbol,
        ts=c.ts + timedelta(seconds=TIMEFRAMES[timeframe]),
        last=c.close,
        bid=(c.close * (1 - half)).quantize(Decimal("0.01")),
        ask=(c.close * (1 + half)).quantize(Decimal("0.01")),
    )
