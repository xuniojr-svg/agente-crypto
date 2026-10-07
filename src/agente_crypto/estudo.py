"""Compara várias configurações da estratégia sem se enganar.

Os dados são divididos em dois pedaços:
- treino (primeiros 60%): onde escolhemos a melhor configuração;
- validação (últimos 40%): onde conferimos se ela continua boa em dados que não viu.

Se a melhor do treino não ganhar de "segurar o mesmo valor" na validação,
a conclusão honesta é que a estratégia não tem vantagem.
"""

from __future__ import annotations

import dataclasses
import itertools
from decimal import Decimal
from pathlib import Path
from typing import List, Tuple

from .backtest import BacktestReport, run_backtest
from .config import RiskLimits, StrategyConfig
from .data import TIMEFRAMES, resample
from .domain import Candle

TIMEFRAMES_TESTADOS = ["4h", "1d"]
MEDIAS = [(5, 20), (10, 30), (10, 50), (20, 50)]
TIPOS_ORDEM = ["maker", "taker"]
FRACAO_TREINO = Decimal("0.6")
# Para considerar a vantagem "relevante" na validação: pelo menos 10% do valor de uma ordem
# e pelo menos 10 execuções. É um filtro grosseiro contra sorte, não um teste estatístico.
MARGEM_MINIMA = Decimal("0.10")
MIN_EXECUCOES = 10


def vantagem(r: BacktestReport) -> Decimal:
    """Quanto a estratégia ganhou a mais do que comprar o mesmo valor e segurar."""
    return r.resultado_brl - r.segurar_mesmo_valor_brl


def _configs(base: StrategyConfig):
    for tf, (fast, slow), tipo in itertools.product(TIMEFRAMES_TESTADOS, MEDIAS, TIPOS_ORDEM):
        yield dataclasses.replace(
            base, timeframe=tf, fast=fast, slow=slow,
            fees=dataclasses.replace(base.fees, order_type=tipo),
        )


def run_estudo(
    candles_1h: List[Candle], base: StrategyConfig, limits: RiskLimits, workdir: str | Path
) -> Tuple[List[Tuple[StrategyConfig, BacktestReport, BacktestReport]], str]:
    workdir = Path(workdir)
    linhas = []
    cache = {tf: resample(candles_1h, tf) for tf in TIMEFRAMES_TESTADOS}
    for n, cfg in enumerate(_configs(base)):
        candles = cache[cfg.timeframe]
        split = int(len(candles) * FRACAO_TREINO)
        if split <= cfg.slow or len(candles) - split < 10:
            continue
        treino = run_backtest(candles[:split], cfg, limits, workdir / f"{n}_treino")
        valid = run_backtest(candles, cfg, limits, workdir / f"{n}_validacao", start_index=split)
        linhas.append((cfg, treino, valid))

    linhas.sort(key=lambda x: vantagem(x[1]), reverse=True)
    return linhas, _texto(linhas)


def _texto(linhas) -> str:
    q = lambda d: f"{d:+.2f}"  # noqa: E731
    if not linhas:
        return "Dados insuficientes para o estudo."
    out = [
        f"Treino: {linhas[0][1].periodo} | Validação: {linhas[0][2].periodo}",
        "Vantagem = resultado da estratégia menos o de comprar o mesmo valor e segurar (R$).",
        "",
        f"{'configuração':<46} {'treino':>9} {'validação':>10} {'ops':>4} {'taxas':>7}",
    ]
    for cfg, t, v in linhas:
        nome = f"{cfg.timeframe} médias {cfg.fast}/{cfg.slow} {cfg.fees.order_type}"
        out.append(f"{nome:<46} {q(vantagem(t)):>9} {q(vantagem(v)):>10} {v.execucoes:>4} {v.taxas_pagas:>7.2f}")
    melhor_cfg, melhor_t, melhor_v = linhas[0]
    out += [
        "",
        f"Melhor no treino: {melhor_t.estrategia}",
        f"  treino: vantagem R$ {q(vantagem(melhor_t))} | validação: vantagem R$ {q(vantagem(melhor_v))}",
    ]
    v = vantagem(melhor_v)
    minimo = melhor_cfg.order_brl * MARGEM_MINIMA
    if v >= minimo and melhor_v.execucoes >= MIN_EXECUCOES:
        out.append("  Conclusão: manteve vantagem relevante em dados que não viu. "
                   "Candidata a paper trading ao vivo (ainda não prova nada sozinha).")
    elif v > 0:
        out.append(f"  Conclusão: vantagem pequena demais (menos de R$ {minimo:.2f}) ou com poucas operações "
                   f"({melhor_v.execucoes}) para separar de sorte. Não há evidência de que funcione.")
    else:
        out.append("  Conclusão: perdeu a vantagem na validação. Não há evidência de que funcione.")
    return "\n".join(out)
