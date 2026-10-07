"""Laço principal: mercado -> estratégia -> risco -> execução -> ledger -> reconciliação."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from .config import RiskLimits
from .domain import ApprovedOrder, Candle, MarketSnapshot, base_asset, quote_asset
from .execution.base import ExchangeAdapter
from .killswitch import KillSwitch
from .ledger import Ledger
from .portfolio import Portfolio
from .risk import evaluate

QTY_TOL = Decimal("0.00000001")
BRL_TOL = Decimal("0.01")
MAX_CONSECUTIVE_ERRORS = 3


def reconcile(portfolio: Portfolio, exchange_balances: dict[str, Decimal]) -> list[str]:
    """Compara o que o ledger acha que temos com o que a exchange diz."""
    diffs = []
    if abs(exchange_balances.get("BRL", Decimal(0)) - portfolio.cash_brl) > BRL_TOL:
        diffs.append(f"BRL: carteira {portfolio.cash_brl} vs exchange {exchange_balances.get('BRL', 0)}")
    assets = set(portfolio.positions) | (set(exchange_balances) - {"BRL"})
    for a in sorted(assets):
        mine, theirs = portfolio.qty(a), exchange_balances.get(a, Decimal(0))
        if abs(mine - theirs) > QTY_TOL:
            diffs.append(f"{a}: carteira {mine} vs exchange {theirs}")
    return diffs


class TradingEngine:
    def __init__(
        self,
        strategy,
        limits: RiskLimits,
        exchange: ExchangeAdapter,
        ledger: Ledger,
        kill_switch: KillSwitch,
        portfolio: Portfolio,
    ):
        self.strategy = strategy
        self.limits = limits
        self.exchange = exchange
        self.ledger = ledger
        self.kill_switch = kill_switch
        self.portfolio = portfolio
        self.rejections: Counter[str] = Counter()
        self._errors = 0
        self._killed_logged = False

    def _apply_fills(self, fills, intent_id: str) -> None:
        for f in fills:
            realized = self.portfolio.apply_fill(f)
            pos = self.portfolio.positions[base_asset(f.symbol)]
            self.ledger.record(
                "execucao", f.ts, id=intent_id, ordem=f.order_id, par=f.symbol, lado=f.side,
                quantidade=f.quantity, preco=f.price, taxa_brl=f.fee_brl, resultado_brl=realized,
            )
            self.ledger.record_fiscal(f, pos.avg_cost_brl, realized)

    def _reconcile(self, now: datetime) -> None:
        diffs = reconcile(self.portfolio, self.exchange.balances())
        if diffs:
            self.ledger.record("divergencia", now, detalhes=diffs)
            self.kill_switch.activate("divergência na reconciliação: " + "; ".join(diffs))

    def step(self, candles: Sequence[Candle], snapshot: MarketSnapshot, now: datetime) -> None:
        prices = {base_asset(snapshot.symbol): snapshot.last}
        self.portfolio.roll_day(now, prices)

        # ordens passivas que executaram desde o último passo
        poll = getattr(self.exchange, "poll_fills", None)
        late = poll() if poll else []
        if late:
            for f in late:
                self._apply_fills([f], f.order_id.rsplit("-", 1)[0])
            self._reconcile(now)

        if self.kill_switch.is_active():
            if not self._killed_logged:
                n = self.exchange.cancel_all()
                self.ledger.record("kill_switch", now, motivo=self.kill_switch.reason(), ordens_canceladas=n)
                self._killed_logged = True
            return
        self._killed_logged = False

        # uma ordem por vez: enquanto houver ordem no livro, não cria outra
        open_orders = getattr(self.exchange, "open_orders", None)
        if open_orders and open_orders() > 0:
            return

        intent = self.strategy.decide(candles, self.portfolio)
        if intent is None:
            return
        self.ledger.record(
            "intencao", now,
            id=intent.id, par=intent.symbol, lado=intent.side, valor_brl=intent.notional_brl,
            preco_limite=intent.limit_price, motivo=intent.reason, origem=intent.source,
            mercado={"last": snapshot.last, "bid": snapshot.bid, "ask": snapshot.ask, "ts": snapshot.ts},
        )

        decision = evaluate(
            intent, self.portfolio, snapshot, prices, now, self.limits,
            kill_switch_active=self.kill_switch.is_active(),
        )
        if not isinstance(decision, ApprovedOrder):
            for r in decision.reasons:
                self.rejections[re.sub(r"\d[\d.,]*", "N", r)] += 1
            self.ledger.record("risco_rejeitou", now, id=intent.id, motivos=list(decision.reasons),
                               limites=self.limits.version)
            return
        self.ledger.record("risco_aprovou", now, id=intent.id, quantidade=decision.quantity,
                           limites=self.limits.version)

        try:
            fills = self.exchange.place_order(decision)
            self._errors = 0
        except Exception as e:  # noqa: BLE001 - qualquer falha da exchange conta
            self._errors += 1
            self.ledger.record("erro_execucao", now, id=intent.id, erro=repr(e), seguidos=self._errors)
            if self._errors >= MAX_CONSECUTIVE_ERRORS:
                self.kill_switch.activate(f"{self._errors} erros seguidos da exchange")
            return

        if not fills:
            pendente = bool(open_orders and open_orders() > 0)
            self.ledger.record("ordem_no_livro" if pendente else "nao_executada", now, id=intent.id)
        self._apply_fills(fills, intent.id)
        self._reconcile(now)
