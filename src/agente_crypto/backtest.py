"""Roda o sistema completo sobre candles históricos e compara com comprar e segurar."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .config import RiskLimits, StrategyConfig
from .data import snapshot_from_candle
from .domain import Candle, base_asset
from .engine import TradingEngine
from .execution.paper import PaperExchange
from .killswitch import KillSwitch
from .ledger import Ledger
from .portfolio import Portfolio
from .strategy import SmaCross

DEFAULT_SPREAD_PCT = Decimal("0.10")


@dataclass
class BacktestReport:
    periodo: str
    candles: int
    patrimonio_inicial: Decimal
    patrimonio_final: Decimal
    retorno_pct: Decimal
    comprar_e_segurar_pct: Decimal
    execucoes: int
    taxas_pagas: Decimal
    resultado_realizado: Decimal
    max_drawdown_pct: Decimal
    rejeicoes: dict[str, int]
    cadeia_integra: bool
    kill_switch: str | None

    def texto(self) -> str:
        q = lambda d: f"{d:.2f}"  # noqa: E731
        linhas = [
            f"Período: {self.periodo} ({self.candles} candles)",
            f"Patrimônio: R$ {q(self.patrimonio_inicial)} -> R$ {q(self.patrimonio_final)}",
            f"Retorno da estratégia (após taxas): {q(self.retorno_pct)}%",
            f"Comprar e segurar no mesmo período:  {q(self.comprar_e_segurar_pct)}%",
            f"Execuções: {self.execucoes} | Taxas pagas: R$ {q(self.taxas_pagas)} | "
            f"Resultado realizado: R$ {q(self.resultado_realizado)}",
            f"Queda máxima do patrimônio: {q(self.max_drawdown_pct)}%",
            f"Cadeia de auditoria íntegra: {'sim' if self.cadeia_integra else 'NÃO'}",
        ]
        if self.rejeicoes:
            linhas.append("Bloqueios do motor de risco:")
            linhas += [f"  {n}x {m}" for m, n in sorted(self.rejeicoes.items(), key=lambda x: -x[1])]
        if self.kill_switch:
            linhas.append(f"KILL SWITCH acionado: {self.kill_switch}")
        return "\n".join(linhas)


def run_backtest(
    candles: list[Candle],
    cfg: StrategyConfig,
    limits: RiskLimits,
    workdir: str | Path,
    spread_pct: Decimal = DEFAULT_SPREAD_PCT,
) -> BacktestReport:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    db = workdir / "backtest.sqlite"
    if db.exists():
        db.unlink()
    kill = KillSwitch(workdir / "KILL_backtest")
    if kill.path.exists():
        kill.path.unlink()

    ledger = Ledger(db)
    cash = cfg.initial_cash_brl
    exchange = PaperExchange(cfg.fees, {"BRL": cash})
    portfolio = Portfolio(cash_brl=cash)
    strategy = SmaCross(cfg)
    engine = TradingEngine(strategy, limits, exchange, ledger, kill, portfolio)
    asset = base_asset(cfg.symbol)

    peak, max_dd = cash, Decimal(0)
    for i in range(len(candles)):
        snap = snapshot_from_candle(cfg.symbol, candles[i], cfg.timeframe, spread_pct)
        exchange.update_market(snap)
        engine.step(candles[: i + 1], snap, now=snap.ts)
        eq = portfolio.equity({asset: snap.last})
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100)

    final = portfolio.equity({asset: candles[-1].close})
    fee = cfg.fees.taker_pct / 100
    bh = ((candles[-1].close / candles[0].close) * (1 - fee) * (1 - fee) - 1) * 100
    ok, _ = ledger.verify_chain()
    report = BacktestReport(
        periodo=f"{candles[0].ts:%d/%m/%Y} a {candles[-1].ts:%d/%m/%Y}",
        candles=len(candles),
        patrimonio_inicial=cash,
        patrimonio_final=final,
        retorno_pct=(final / cash - 1) * 100,
        comprar_e_segurar_pct=bh,
        execucoes=len(ledger.fiscal_rows()),
        taxas_pagas=portfolio.fees_paid_brl,
        resultado_realizado=portfolio.realized_pnl_brl,
        max_drawdown_pct=max_dd,
        rejeicoes=dict(engine.rejections),
        cadeia_integra=ok,
        kill_switch=kill.reason(),
    )
    ledger.export_fiscal_csv(workdir / "livro_fiscal.csv")
    ledger.close()
    return report
