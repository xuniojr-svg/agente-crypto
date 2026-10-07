"""Estratégia determinística de exemplo: cruzamento de médias móveis simples.

Existe para exercitar o sistema de ponta a ponta. Ela produz apenas
OrderIntent; quem decide se vira ordem é o motor de risco.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from .config import StrategyConfig
from .domain import Candle, OrderIntent, Side, base_asset
from .portfolio import Portfolio

LIMIT_OFFSET = Decimal("0.002")  # 0,2% de folga no preço limite
DUST_BRL = Decimal("1")  # posição abaixo disso conta como zerada


def sma(values: Sequence[Decimal], n: int) -> Decimal:
    return sum(values[-n:], Decimal("0")) / n


class SmaCross:
    def __init__(self, cfg: StrategyConfig):
        if cfg.fast >= cfg.slow:
            raise ValueError("fast deve ser menor que slow")
        self.cfg = cfg
        self.source = f"strategy:{cfg.name}@{cfg.version}"

    @property
    def warmup(self) -> int:
        return self.cfg.slow

    def decide(self, candles: Sequence[Candle], portfolio: Portfolio) -> OrderIntent | None:
        if len(candles) < self.warmup:
            return None
        closes = [c.close for c in candles]
        fast_now, slow_now = sma(closes, self.cfg.fast), sma(closes, self.cfg.slow)
        last = closes[-1]
        held = portfolio.qty(base_asset(self.cfg.symbol))
        if held * last < DUST_BRL:
            held = Decimal("0")

        maker = self.cfg.fees.order_type == "maker"
        # Estado, não evento: enquanto a média rápida estiver acima, queremos estar comprados.
        # Assim, se uma ordem maker não executar, a estratégia tenta de novo no próximo candle.
        if fast_now > slow_now and held == 0:
            limit = last if maker else last * (1 + LIMIT_OFFSET)
            return OrderIntent(
                symbol=self.cfg.symbol,
                side=Side.BUY,
                notional_brl=self.cfg.order_brl,
                limit_price=limit.quantize(Decimal("0.01")),
                reason=f"média {self.cfg.fast} acima da {self.cfg.slow} ({fast_now:.2f} > {slow_now:.2f})",
                source=self.source,
            )
        if fast_now < slow_now and held > 0:
            limit = (last if maker else last * (1 - LIMIT_OFFSET)).quantize(Decimal("0.01"))
            # vende em partes de no máximo order_brl, para respeitar o limite por ordem
            notional = min((held * limit).quantize(Decimal("0.01")), self.cfg.order_brl)
            return OrderIntent(
                symbol=self.cfg.symbol,
                side=Side.SELL,
                notional_brl=notional,
                limit_price=limit,
                reason=f"média {self.cfg.fast} abaixo da {self.cfg.slow} ({fast_now:.2f} < {slow_now:.2f})",
                source=self.source,
            )
        return None


class Swing:
    """Reversão à média: compra quedas fortes e vende na volta à média, com stop.

    - Sem posição: se o fechamento estiver entry_drop_pct abaixo da média de `slow`
      candles, compra (ordem maker no último preço, ou taker com folga).
    - Com posição: vende se o preço voltou à média (alvo) ou se caiu stop_loss_pct
      abaixo do custo médio (stop). O stop usa ordem agressiva para garantir a saída.
    """

    def __init__(self, cfg: StrategyConfig):
        if cfg.slow < 2:
            raise ValueError("slow (janela da média) deve ser >= 2")
        self.cfg = cfg
        self.source = f"strategy:swing@{cfg.version}"

    @property
    def warmup(self) -> int:
        return self.cfg.slow

    def decide(self, candles: Sequence[Candle], portfolio: Portfolio) -> OrderIntent | None:
        if len(candles) < self.warmup:
            return None
        closes = [c.close for c in candles]
        mean = sma(closes, self.cfg.slow)
        last = closes[-1]
        asset = base_asset(self.cfg.symbol)
        held = portfolio.qty(asset)
        if held * last < DUST_BRL:
            held = Decimal("0")
        maker = self.cfg.fees.order_type == "maker"
        q = Decimal("0.01")

        if held == 0:
            gatilho = mean * (1 - self.cfg.entry_drop_pct / 100)
            if last < gatilho:
                limit = last if maker else last * (1 + LIMIT_OFFSET)
                return OrderIntent(
                    symbol=self.cfg.symbol, side=Side.BUY, notional_brl=self.cfg.order_brl,
                    limit_price=limit.quantize(q),
                    reason=f"preço {last:.2f} está {self.cfg.entry_drop_pct}% abaixo da média {mean:.2f}",
                    source=self.source,
                )
            return None

        cost = portfolio.positions[asset].avg_cost_brl
        stop = cost * (1 - self.cfg.stop_loss_pct / 100)
        if last <= stop:
            limit = (last * (1 - LIMIT_OFFSET)).quantize(q)  # agressiva: precisa sair
            motivo = f"stop: preço {last:.2f} abaixo de {stop:.2f} ({self.cfg.stop_loss_pct}% do custo)"
        elif last >= mean:
            limit = (last if maker else last * (1 - LIMIT_OFFSET)).quantize(q)
            motivo = f"alvo: preço {last:.2f} voltou à média {mean:.2f}"
        else:
            return None
        return OrderIntent(
            symbol=self.cfg.symbol, side=Side.SELL,
            notional_brl=min((held * limit).quantize(q), self.cfg.order_brl),
            limit_price=limit, reason=motivo, source=self.source,
        )


def make_strategy(cfg: StrategyConfig):
    return Swing(cfg) if cfg.kind == "swing" else SmaCross(cfg)
