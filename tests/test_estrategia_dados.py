import dataclasses
from datetime import timedelta
from decimal import Decimal

from agente_crypto.data import resample, synthetic_candles
from agente_crypto.domain import Fill, Side
from agente_crypto.estudo import run_estudo, vantagem
from agente_crypto.portfolio import Portfolio
from agente_crypto.strategy import SmaCross

from conftest import NOW


def test_resample_1h_para_1d():
    hs = synthetic_candles(24 * 3 + 5, seed=1)  # 3 dias completos + 5 horas
    ds = resample(hs, "1d")
    assert len(ds) == 3  # o dia incompleto é descartado
    dia = hs[:24]
    assert ds[0].open == dia[0].open and ds[0].close == dia[-1].close
    assert ds[0].high == max(c.high for c in dia) and ds[0].low == min(c.low for c in dia)
    assert ds[1].ts - ds[0].ts == timedelta(days=1)


def test_resample_mesmo_timeframe_nao_muda():
    hs = synthetic_candles(10)
    assert resample(hs, "1h") == hs


def test_venda_em_partes_quando_posicao_passa_do_limite_por_ordem(cfg):
    cfg = dataclasses.replace(cfg, timeframe="1h", fast=2, slow=4)
    p = Portfolio(cash_brl=Decimal("0"))
    p.apply_fill(Fill("o", "BTC/BRL", Side.BUY, Decimal("0.01"), Decimal("100000"), Decimal(0), NOW, "x"))
    # preço caindo: média rápida abaixo da lenta -> vender; posição vale ~R$1.000
    candles = synthetic_candles(4, start_price=110000, drift=-0.02, vol_per_step=0.0001)
    i = SmaCross(cfg).decide(candles, p)
    assert i.side is Side.SELL and i.notional_brl == cfg.order_brl


def test_estudo_sintetico_roda_e_separa_treino_e_validacao(tmp_path, cfg, limits):
    hs = synthetic_candles(24 * 200, seed=11)
    linhas, texto = run_estudo(hs, cfg, limits, tmp_path)
    assert linhas and "Melhor no treino" in texto
    # ordenado pela vantagem no treino
    vs = [vantagem(t) for _, t, _ in linhas]
    assert vs == sorted(vs, reverse=True)
    for _, t, v in linhas:
        assert t.cadeia_integra and v.cadeia_integra and v.kill_switch is None
