"""Modo ao vivo simulado, com relógio e dados falsos (sem rede)."""

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agente_crypto.data import synthetic_candles
from agente_crypto.domain import MarketSnapshot
from agente_crypto.live import LivePaper

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeMarket:
    """Candles de 1h sintéticos; o 'agora' anda conforme o teste manda."""

    def __init__(self, drift=0.002):
        self.candles = synthetic_candles(24 * 120, drift=drift, vol_per_step=0.002, start=START)
        self.now = START + timedelta(days=60)
        self.fetches = 0

    def clock(self):
        return self.now

    def fetch_candles(self, symbol, timeframe, days):
        self.fetches += 1
        start = self.now - timedelta(days=days)
        # só candles que já fecharam
        return [c for c in self.candles if start <= c.ts and c.ts + timedelta(hours=1) <= self.now]

    def fetch_ticker(self, symbol):
        last = [c for c in self.candles if c.ts + timedelta(hours=1) <= self.now][-1].close
        half = last * Decimal("0.0005")
        return MarketSnapshot(symbol, self.now, last, last - half, last + half)


def robo(tmp_path, cfg, limits, market):
    return LivePaper(cfg, limits, tmp_path, fetch_candles=market.fetch_candles,
                     fetch_ticker=market.fetch_ticker, clock=market.clock)


def taker(cfg):
    return dataclasses.replace(cfg, fees=dataclasses.replace(cfg.fees, order_type="taker"))


def test_compra_na_alta_e_so_decide_uma_vez_por_candle(tmp_path, cfg, limits):
    m = FakeMarket(drift=0.002)  # alta: média curta acima da longa
    r = robo(tmp_path, taker(cfg), limits, m)
    r.tick()
    assert r.portfolio.qty("BTC") > 0
    assert len(r.ledger.events("execucao")) == 1
    fetches = m.fetches
    # minutos depois, no mesmo dia: nenhuma decisão nova e nenhum download de candles
    m.now += timedelta(minutes=5)
    r.tick()
    assert m.fetches == fetches
    assert len(r.ledger.events("candle_processado")) == 1
    # no dia seguinte, processa o candle novo
    m.now += timedelta(days=1)
    r.tick()
    assert len(r.ledger.events("candle_processado")) == 2


def test_religar_recupera_a_carteira_do_ledger(tmp_path, cfg, limits):
    m = FakeMarket(drift=0.002)
    r = robo(tmp_path, taker(cfg), limits, m)
    r.tick()
    qty, cash = r.portfolio.qty("BTC"), r.portfolio.cash_brl
    r.ledger.close()

    r2 = robo(tmp_path, taker(cfg), limits, m)
    assert r2.portfolio.qty("BTC") == qty
    assert r2.portfolio.cash_brl == cash
    assert r2.exchange.balances()["BTC"] == qty
    assert len(r2.ledger.events("inicio_ao_vivo")) == 1
    # o mesmo candle não é processado de novo depois de religar
    r2.tick()
    assert len(r2.ledger.events("execucao")) == 1
    assert r2.ledger.verify_chain()[0]


def test_ordem_maker_espera_o_candle_seguinte(tmp_path, cfg, limits):
    m = FakeMarket(drift=0.002)
    r = robo(tmp_path, cfg, limits, m)  # config padrão é maker
    r.tick()
    assert r.exchange.open_orders() == 1
    assert r.portfolio.qty("BTC") == 0
    m.now += timedelta(days=1)
    r.tick()  # o candle novo é conferido; se passou do limite, a compra executou
    assert r.exchange.open_orders() <= 1
    assert r.portfolio.qty("BTC") == r.exchange.balances().get("BTC", 0)


def test_kill_switch_para_o_robo(tmp_path, cfg, limits):
    m = FakeMarket(drift=0.002)
    r = robo(tmp_path, taker(cfg), limits, m)
    r.kill.activate("teste")
    linha = r.tick()
    assert "KILL" in linha
    assert r.portfolio.qty("BTC") == 0
    assert not r.ledger.events("execucao")


def test_erro_de_rede_nao_derruba_o_robo(tmp_path, cfg, limits):
    m = FakeMarket()

    def quebrado(symbol):
        raise OSError("sem internet")

    r = LivePaper(cfg, limits, tmp_path, fetch_candles=m.fetch_candles,
                  fetch_ticker=quebrado, clock=m.clock)
    logs = []
    assert r.run(intervalo=0, voltas=2, log=logs.append) is False
    assert len(r.ledger.events("erro_dados")) == 2
    assert "Cadeia de auditoria íntegra: sim" in r.situacao()


def test_rodada_diaria_recupera_dias_perdidos(tmp_path, cfg, limits):
    """Agendado uma vez por dia: se pular dias, processa o candle mais novo e segue."""
    m = FakeMarket(drift=0.002)
    r = robo(tmp_path, taker(cfg), limits, m)
    assert r.run(voltas=1, log=lambda _: None)
    r.ledger.close()
    m.now += timedelta(days=3)  # ficou 3 dias sem rodar
    r2 = robo(tmp_path, taker(cfg), limits, m)
    assert r2.run(voltas=1, log=lambda _: None)
    passos = r2.ledger.events("candle_processado")
    assert len(passos) == 2
    assert datetime.fromisoformat(passos[-1]["candle"]) == START + timedelta(days=62)
