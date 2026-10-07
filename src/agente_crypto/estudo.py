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

TIPOS_ORDEM = ["maker", "taker"]
# Conservadora: segue a tendência, opera pouco.
TENDENCIA_TIMEFRAMES = ["4h", "1d"]
MEDIAS = [(5, 20), (10, 30), (10, 50), (20, 50)]
# Swing (trade ativo): compra quedas e vende na volta à média, com stop de 5%.
SWING_TIMEFRAMES = ["1h", "4h"]
SWING_JANELAS = [24, 48]          # média de N candles
SWING_QUEDAS = [Decimal("2"), Decimal("4")]  # % abaixo da média para comprar
SWING_STOP = Decimal("5")
TIMEFRAMES_TESTADOS = sorted(set(TENDENCIA_TIMEFRAMES + SWING_TIMEFRAMES))
FRACAO_TREINO = Decimal("0.6")
# Para considerar a vantagem "relevante" na validação: pelo menos 10% do valor de uma ordem
# e pelo menos 10 execuções. É um filtro grosseiro contra sorte, não um teste estatístico.
MARGEM_MINIMA = Decimal("0.10")
MIN_EXECUCOES = 10


def vantagem(r: BacktestReport) -> Decimal:
    """Quanto a estratégia ganhou a mais do que comprar o mesmo valor e segurar."""
    return r.resultado_brl - r.segurar_mesmo_valor_brl


def _configs(base: StrategyConfig):
    for tf, (fast, slow), tipo in itertools.product(TENDENCIA_TIMEFRAMES, MEDIAS, TIPOS_ORDEM):
        yield dataclasses.replace(
            base, kind="tendencia", timeframe=tf, fast=fast, slow=slow,
            fees=dataclasses.replace(base.fees, order_type=tipo),
        )
    for tf, janela, queda, tipo in itertools.product(SWING_TIMEFRAMES, SWING_JANELAS, SWING_QUEDAS, TIPOS_ORDEM):
        yield dataclasses.replace(
            base, kind="swing", timeframe=tf, fast=1, slow=janela,
            entry_drop_pct=queda, stop_loss_pct=SWING_STOP,
            fees=dataclasses.replace(base.fees, order_type=tipo),
        )


def nome(cfg: StrategyConfig) -> str:
    if cfg.kind == "swing":
        return f"swing {cfg.timeframe} média {cfg.slow} queda {cfg.entry_drop_pct}% {cfg.fees.order_type}"
    return f"tendência {cfg.timeframe} médias {cfg.fast}/{cfg.slow} {cfg.fees.order_type}"


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


def _conclusao(cfg, v_rep) -> str:
    v = vantagem(v_rep)
    minimo = cfg.order_brl * MARGEM_MINIMA
    if v >= minimo and v_rep.execucoes >= MIN_EXECUCOES:
        return ("manteve vantagem relevante em dados que não viu. "
                "Candidata a paper trading ao vivo (ainda não prova nada sozinha).")
    if v > 0:
        return (f"vantagem pequena demais (menos de R$ {minimo:.2f}) ou com poucas operações "
                f"({v_rep.execucoes}) para separar de sorte. Não há evidência de que funcione.")
    return "perdeu para segurar o mesmo valor na validação. Não há evidência de que funcione."


def _texto(linhas) -> str:
    q = lambda d: f"{d:+.2f}"  # noqa: E731
    if not linhas:
        return "Dados insuficientes para o estudo."
    out = [
        f"Treino: {linhas[0][1].periodo} | Validação: {linhas[0][2].periodo}",
        "Vantagem = resultado da estratégia (já sem as taxas) menos o de comprar o mesmo valor e segurar (R$).",
        "",
        f"{'configuração':<46} {'treino':>9} {'validação':>10} {'ops':>4} {'taxas':>7}",
    ]
    for cfg, t, v in linhas:
        out.append(f"{nome(cfg):<46} {q(vantagem(t)):>9} {q(vantagem(v)):>10} {v.execucoes:>4} {v.taxas_pagas:>7.2f}")
    out.append("")
    out.append("LADO A LADO (melhor de cada tipo escolhida no treino, conferida na validação):")
    for kind, titulo in (("tendencia", "Conservadora (tendência)"), ("swing", "Trade ativo (swing)")):
        fam = [x for x in linhas if x[0].kind == kind]
        if not fam:
            continue
        cfg, t, v = fam[0]
        out += [
            f"- {titulo}: {t.estrategia}",
            f"    validação: resultado R$ {q(v.resultado_brl)} | segurar o mesmo valor R$ {q(v.segurar_mesmo_valor_brl)}"
            f" | vantagem R$ {q(vantagem(v))} | {v.execucoes} execuções | taxas R$ {v.taxas_pagas:.2f}",
            f"    {_conclusao(cfg, v)}",
        ]
    return "\n".join(out)
