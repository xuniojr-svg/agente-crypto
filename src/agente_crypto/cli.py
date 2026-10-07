"""Linha de comando.

  agente-crypto baixar --dias 180            baixa candles do Mercado Bitcoin para dados/
  agente-crypto backtest [--csv arquivo]     roda a estratégia sobre os dados
  agente-crypto backtest --sintetico         roda sobre dados aleatórios (sem rede)
  agente-crypto verificar dados/backtest/backtest.sqlite
  agente-crypto kill "motivo"                liga o kill switch
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .backtest import run_backtest
from .config import load_risk_limits, load_strategy_config
from .data import fetch_mb_candles, load_csv, save_csv, synthetic_candles
from .killswitch import KillSwitch
from .ledger import Ledger

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
DADOS = ROOT / "dados"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agente-crypto")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baixar")
    b.add_argument("--dias", type=int, default=180)

    bt = sub.add_parser("backtest")
    bt.add_argument("--csv")
    bt.add_argument("--sintetico", action="store_true")

    v = sub.add_parser("verificar")
    v.add_argument("db")

    k = sub.add_parser("kill")
    k.add_argument("motivo", nargs="?", default="acionado manualmente")

    a = p.parse_args(argv)
    cfg = load_strategy_config(CONFIG / "strategy.yaml")
    DADOS.mkdir(exist_ok=True)
    default_csv = DADOS / f"{cfg.symbol.replace('/', '-')}_{cfg.timeframe}.csv"

    if a.cmd == "baixar":
        candles = fetch_mb_candles(cfg.symbol, cfg.timeframe, a.dias)
        save_csv(candles, default_csv)
        print(f"{len(candles)} candles salvos em {default_csv}")
    elif a.cmd == "backtest":
        limits = load_risk_limits(CONFIG / "risk_limits.yaml")
        if a.sintetico:
            candles = synthetic_candles(24 * 180)
        else:
            candles = load_csv(a.csv or default_csv)
        report = run_backtest(candles, cfg, limits, DADOS / "backtest")
        print(report.texto())
        print(f"\nAuditoria: {DADOS / 'backtest' / 'backtest.sqlite'}")
        print(f"Livro fiscal: {DADOS / 'backtest' / 'livro_fiscal.csv'}")
    elif a.cmd == "verificar":
        ok, seq = Ledger(a.db).verify_chain()
        print("cadeia íntegra" if ok else f"ADULTERAÇÃO detectada no evento {seq}")
        return 0 if ok else 1
    elif a.cmd == "kill":
        KillSwitch(DADOS / "KILL").activate(a.motivo)
        print("kill switch ligado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
