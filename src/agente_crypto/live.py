"""Paper trading ao vivo: preços reais do Mercado Bitcoin, ordens simuladas.

Não usa conta nem chave de API e não envia ordem nenhuma: a "exchange" é a
PaperExchange, a mesma do backtest. A cada `intervalo` segundos:

1. Lê o ticker público (bid/ask reais) e confere o kill switch.
2. Quando fecha um candle novo do timeframe da estratégia, confere as ordens
   que estavam no livro contra esse candle e roda um passo do motor.

O estado é reconstruído do ledger a cada início (as execuções gravadas são a
fonte da verdade), então dá para parar e ligar de novo sem perder nada.
As ordens que estão no livro da exchange simulada ficam em livro_paper.json.
"""

from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, List, Optional

from .config import RiskLimits, StrategyConfig
from .data import TIMEFRAMES, fetch_mb_candles, fetch_mb_ticker, resample
from .domain import Candle, Fill, MarketSnapshot, Side, base_asset
from .engine import TradingEngine
from .execution.paper import PaperExchange
from .killswitch import KillSwitch
from .ledger import Ledger
from .portfolio import Portfolio
from .strategy import make_strategy

FetchCandles = Callable[[str, str, int], List[Candle]]
FetchTicker = Callable[[str], MarketSnapshot]


def _restore(ledger: Ledger, cfg: StrategyConfig, now: datetime) -> Portfolio:
    """Refaz a carteira a partir do ledger. Na primeira vez, grava o saldo inicial."""
    inicio = ledger.events("inicio_ao_vivo")
    if not inicio:
        ledger.record("inicio_ao_vivo", now, saldo_inicial_brl=cfg.initial_cash_brl,
                      estrategia=f"{cfg.name}@{cfg.version}")
        return Portfolio(cash_brl=cfg.initial_cash_brl)
    portfolio = Portfolio(cash_brl=Decimal(inicio[0]["saldo_inicial_brl"]))
    for e in ledger.events("execucao"):
        portfolio.apply_fill(Fill(
            order_id=e["ordem"], symbol=e["par"], side=Side(e["lado"]),
            quantity=Decimal(e["quantidade"]), price=Decimal(e["preco"]),
            fee_brl=Decimal(e["taxa_brl"]), ts=datetime.fromisoformat(e["ts"]),
            exchange=f"paper:{cfg.fees.exchange}",
        ))
    return portfolio


class _PrecoAtual:
    """Envolve a estratégia: o sinal vem dos candles fechados, mas o preço limite é
    recalculado sobre o preço atual (mantendo a mesma folga que a estratégia usou).
    Sem isso, rodando horas depois do fechamento, o limite fica longe do mercado e o
    motor de risco recusa a ordem (desvio acima de 0,5%)."""

    def __init__(self, strategy, preco: Callable[[], Decimal]):
        self.inner, self.preco = strategy, preco

    @property
    def warmup(self) -> int:
        return self.inner.warmup

    def decide(self, candles, portfolio):
        intent = self.inner.decide(candles, portfolio)
        if intent is None:
            return None
        fator = self.preco() / candles[-1].close
        return dataclasses.replace(intent, limit_price=(intent.limit_price * fator).quantize(Decimal("0.01")))


class LivePaper:
    def __init__(
        self,
        cfg: StrategyConfig,
        limits: RiskLimits,
        workdir: str | Path,
        fetch_candles: FetchCandles = fetch_mb_candles,
        fetch_ticker: FetchTicker = fetch_mb_ticker,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.cfg, self.limits = cfg, limits
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.fetch_candles, self.fetch_ticker, self.clock = fetch_candles, fetch_ticker, clock
        self.ledger = Ledger(self.workdir / "ao_vivo.sqlite")
        self.kill = KillSwitch(self.workdir / "KILL")
        now = clock()
        self.portfolio = _restore(self.ledger, cfg, now)
        balances = {"BRL": self.portfolio.cash_brl}
        balances.update({a: p.quantity for a, p in self.portfolio.positions.items() if p.quantity > 0})
        self.exchange = PaperExchange(cfg.fees, balances)
        # o livro da exchange simulada sobrevive entre execuções (rodada diária)
        self.livro = self.workdir / "livro_paper.json"
        if self.livro.exists():
            self.exchange.import_book(json.loads(self.livro.read_text()))
        self._snap: Optional[MarketSnapshot] = None
        self.strategy = _PrecoAtual(make_strategy(cfg), lambda: self._snap.last)
        self.engine = TradingEngine(self.strategy, limits, self.exchange, self.ledger, self.kill, self.portfolio)
        passos = self.ledger.events("candle_processado")
        self.last_candle: Optional[datetime] = (
            datetime.fromisoformat(passos[-1]["candle"]) if passos else None
        )

    def _closed_candles(self) -> List[Candle]:
        step_days = TIMEFRAMES[self.cfg.timeframe] / 86400
        dias = int((self.strategy.warmup + 3) * step_days) + 2
        return resample(self.fetch_candles(self.cfg.symbol, "1h", dias), self.cfg.timeframe)

    def tick(self) -> str:
        """Uma volta do laço. Devolve uma linha de log."""
        now = self.clock()
        snap = self._snap = self.fetch_ticker(self.cfg.symbol)
        self.exchange.update_market(snap)
        asset = base_asset(self.cfg.symbol)

        if self.kill.is_active():
            self.engine.step([], snap, now)  # cancela ordens e registra, sem decidir nada
            self.livro.write_text(json.dumps(self.exchange.export_book(), indent=1))
            return f"{now:%d/%m %H:%M} KILL SWITCH ligado: {self.kill.reason()}"

        # só decide quando há um candle fechado que ainda não foi processado
        step = timedelta(seconds=TIMEFRAMES[self.cfg.timeframe])
        # last_candle é a abertura do último candle processado; o seguinte fecha em +2 passos
        if self.last_candle is None or now >= self.last_candle + 2 * step:
            candles = self._closed_candles()
            novos = [c for c in candles if self.last_candle is None or c.ts > self.last_candle]
            if novos:
                # ordens que ficaram no livro são conferidas contra os candles novos
                for c in novos:
                    self.exchange.process_candle(self.cfg.symbol, c)
                janela = candles[-(self.strategy.warmup + 1):]
                self.engine.step(janela, snap, now)
                self.last_candle = candles[-1].ts
                self.ledger.record("candle_processado", now, candle=self.last_candle,
                                   fechamento=candles[-1].close)

        self.livro.write_text(json.dumps(self.exchange.export_book(), indent=1))
        eq = self.portfolio.equity({asset: snap.last})
        return (f"{now:%d/%m %H:%M} BTC R$ {snap.last:,.0f} | patrimônio simulado R$ {eq:,.2f}"
                f" | {asset} {self.portfolio.qty(asset)} | ordens no livro {self.exchange.open_orders()}")

    def run(self, intervalo: int = 60, voltas: Optional[int] = None, log=print) -> bool:
        """Devolve True se a última volta funcionou."""
        log(f"Robô ao vivo SIMULADO ({self.cfg.name}, candles de {self.cfg.timeframe}). "
            f"Nenhuma ordem real é enviada.")
        n, ok = 0, True
        while voltas is None or n < voltas:
            try:
                log(self.tick())
                ok = True
            except Exception as e:  # noqa: BLE001 - internet caiu, API fora do ar etc.
                self.ledger.record("erro_dados", self.clock(), erro=repr(e))
                log(f"erro ao ler dados ({e!r}); tento de novo na próxima volta")
                ok = False
            n += 1
            if voltas is None or n < voltas:
                time.sleep(intervalo)
        return ok

    def situacao(self) -> str:
        asset = base_asset(self.cfg.symbol)
        inicio = Decimal(self.ledger.events("inicio_ao_vivo")[0]["saldo_inicial_brl"])
        ultimo = self.ledger.events("candle_processado")
        preco = Decimal(ultimo[-1]["fechamento"]) if ultimo else None
        linhas = [f"Robô ao vivo SIMULADO desde {self.ledger.events('inicio_ao_vivo')[0]['ts'][:16]}"]
        if preco is not None:
            eq = self.portfolio.equity({asset: preco})
            linhas.append(f"Patrimônio simulado: R$ {eq:,.2f} (começou com R$ {inicio:,.2f},"
                          f" resultado R$ {eq - inicio:+,.2f}) pelo último fechamento R$ {preco:,.0f}")
        linhas.append(f"Posição: {self.portfolio.qty(asset)} {asset} | caixa R$ {self.portfolio.cash_brl:,.2f}")
        linhas.append(f"Execuções: {len(self.ledger.events('execucao'))} | taxas R$ {self.portfolio.fees_paid_brl:,.2f}"
                      f" | candles processados: {len(ultimo)}")
        if self.exchange.open_orders():
            linhas.append(f"Ordens esperando no livro: {self.exchange.open_orders()}")
        if self.kill.is_active():
            linhas.append(f"KILL SWITCH ligado: {self.kill.reason()}")
        ok, _ = self.ledger.verify_chain()
        linhas.append(f"Cadeia de auditoria íntegra: {'sim' if ok else 'NÃO'}")
        return "\n".join(linhas)
