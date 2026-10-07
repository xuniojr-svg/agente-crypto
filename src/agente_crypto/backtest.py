"""Roda o sistema completo sobre candles históricos e compara com comprar e segurar."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from .config import RiskLimits, StrategyConfig
from .data import snapshot_from_candle
from .domain import Candle, base_asset
from .engine import TradingEngine
from .execution.paper import PaperExchange
from .killswitch import KillSwitch
from .ledger import Ledger
from .portfolio import Portfolio
from .strategy import make_strategy

DEFAULT_SPREAD_PCT = Decimal("0.10")


@dataclass
class BacktestReport:
    periodo: str
    candles: int
    patrimonio_inicial: Decimal
    patrimonio_final: Decimal
    resultado_brl: Decimal
    retorno_pct: Decimal
    comprar_e_segurar_pct: Decimal
    segurar_mesmo_valor_brl: Decimal  # comprar order_brl no início e segurar até o fim
    exposicao_media_pct: Decimal  # % médio do patrimônio aplicado em cripto
    tempo_posicionado_pct: Decimal
    execucoes: int
    taxas_pagas: Decimal
    max_drawdown_pct: Decimal
    rejeicoes: Dict[str, int]
    cadeia_integra: bool
    kill_switch: Optional[str]
    estrategia: str = ""

    def texto(self) -> str:
        q = lambda d: f"{d:.2f}"  # noqa: E731
        linhas = [
            f"Estratégia: {self.estrategia}",
            f"Período: {self.periodo} ({self.candles} candles)",
            f"Patrimônio: R$ {q(self.patrimonio_inicial)} -> R$ {q(self.patrimonio_final)}"
            f" (resultado R$ {q(self.resultado_brl)}, {q(self.retorno_pct)}%)",
            f"Comparação justa, segurar o mesmo valor por ordem: R$ {q(self.segurar_mesmo_valor_brl)}",
            f"BTC no período (todo o capital comprado e segurado): {q(self.comprar_e_segurar_pct)}%",
            f"Exposição média: {q(self.exposicao_media_pct)}% do patrimônio | "
            f"tempo posicionado: {q(self.tempo_posicionado_pct)}%",
            f"Execuções: {self.execucoes} | Taxas pagas: R$ {q(self.taxas_pagas)}",
            f"Queda máxima do patrimônio: {q(self.max_drawdown_pct)}%",
            f"Cadeia de auditoria íntegra: {'sim' if self.cadeia_integra else 'NÃO'}",
        ]
        if self.rejeicoes:
            linhas.append("Bloqueios do motor de risco:")
            linhas += [f"  {n}x {m}" for m, n in sorted(self.rejeicoes.items(), key=lambda x: -x[1])]
        if self.kill_switch:
            linhas.append(f"KILL SWITCH acionado: {self.kill_switch}")
        return "\n".join(linhas)


def describe(cfg: StrategyConfig) -> str:
    if cfg.kind == "swing":
        regra = (f"swing: compra {cfg.entry_drop_pct}% abaixo da média de {cfg.slow} candles,"
                 f" vende na média, stop {cfg.stop_loss_pct}%")
    else:
        regra = f"tendência: médias {cfg.fast}/{cfg.slow}"
    return (f"{regra}, candles de {cfg.timeframe}, ordens {cfg.fees.order_type}"
            f" (taxa {cfg.fees.maker_pct if cfg.fees.order_type == 'maker' else cfg.fees.taker_pct}%),"
            f" R$ {cfg.order_brl} por ordem")


def run_backtest(
    candles: List[Candle],
    cfg: StrategyConfig,
    limits: RiskLimits,
    workdir: str | Path,
    spread_pct: Decimal = DEFAULT_SPREAD_PCT,
    start_index: int = 0,
) -> BacktestReport:
    """`start_index`: candles antes dele só servem de histórico (aquecimento das médias);
    a simulação e as métricas começam nele. Serve para a separação treino/validação."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    db = workdir / "backtest.sqlite"
    if db.exists():
        db.unlink()
    kill = KillSwitch(workdir / "KILL_backtest")
    if kill.path.exists():
        kill.path.unlink()

    ledger = Ledger(db, fast=True)
    cash = cfg.initial_cash_brl
    exchange = PaperExchange(cfg.fees, {"BRL": cash})
    portfolio = Portfolio(cash_brl=cash)
    strategy = make_strategy(cfg)
    window = strategy.warmup + 1  # a estratégia só precisa dos últimos candles
    engine = TradingEngine(strategy, limits, exchange, ledger, kill, portfolio)
    asset = base_asset(cfg.symbol)

    peak, max_dd = cash, Decimal(0)
    exposure_sum, in_market, steps = Decimal(0), 0, 0
    for i in range(start_index, len(candles)):
        # ordens que ficaram no livro são conferidas contra o candle seguinte (sem olhar o futuro)
        exchange.process_candle(cfg.symbol, candles[i])
        snap = snapshot_from_candle(cfg.symbol, candles[i], cfg.timeframe, spread_pct)
        exchange.update_market(snap)
        engine.step(candles[max(0, i + 1 - window): i + 1], snap, now=snap.ts)
        eq = portfolio.equity({asset: snap.last})
        held_value = portfolio.qty(asset) * snap.last
        exposure_sum += held_value / eq * 100
        in_market += held_value > 0
        steps += 1
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)

    first, last = candles[start_index].close, candles[-1].close
    final = portfolio.equity({asset: last})
    fee = cfg.fees.taker_pct / 100
    hold_factor = (last / first) * (1 - fee) * (1 - fee)
    ok, _ = ledger.verify_chain()
    report = BacktestReport(
        estrategia=describe(cfg),
        periodo=f"{candles[start_index].ts:%d/%m/%Y} a {candles[-1].ts:%d/%m/%Y}",
        candles=len(candles) - start_index,
        patrimonio_inicial=cash,
        patrimonio_final=final,
        resultado_brl=final - cash,
        retorno_pct=(final / cash - 1) * 100,
        comprar_e_segurar_pct=(hold_factor - 1) * 100,
        segurar_mesmo_valor_brl=cfg.order_brl * (hold_factor - 1),
        exposicao_media_pct=exposure_sum / max(steps, 1),
        tempo_posicionado_pct=Decimal(in_market) / max(steps, 1) * 100,
        execucoes=len(ledger.fiscal_rows()),
        taxas_pagas=portfolio.fees_paid_brl,
        max_drawdown_pct=max_dd,
        rejeicoes=dict(engine.rejections),
        cadeia_integra=ok,
        kill_switch=kill.reason(),
    )
    ledger.export_fiscal_csv(workdir / "livro_fiscal.csv")
    ledger.close()
    return report
