"""Cenários de ponta a ponta com o motor completo."""

from datetime import timedelta
from decimal import Decimal

from agente_crypto.backtest import run_backtest
from agente_crypto.data import snapshot_from_candle, synthetic_candles
from agente_crypto.domain import Side
from agente_crypto.engine import TradingEngine
from agente_crypto.execution.paper import PaperExchange
from agente_crypto.killswitch import KillSwitch
from agente_crypto.ledger import Ledger
from agente_crypto.portfolio import Portfolio

from conftest import NOW, intent, snap


class Fixed:
    """Estratégia de teste que sempre propõe a mesma intenção."""

    def __init__(self, i):
        self.i = i

    def decide(self, candles, portfolio):
        return self.i


def make(tmp_path, cfg, limits, strategy, exchange=None):
    ex = exchange or PaperExchange(cfg.fees, {"BRL": Decimal("5000")})
    led = Ledger(tmp_path / "e.sqlite")
    ks = KillSwitch(tmp_path / "KILL")
    eng = TradingEngine(strategy, limits, ex, led, ks, Portfolio(cash_brl=Decimal("5000")))
    return eng, ex, led, ks


def test_compra_aprovada_executa_e_registra(tmp_path, cfg, limits):
    eng, ex, led, _ = make(tmp_path, cfg, limits, Fixed(intent(price="350500")))
    s = snap()
    ex.update_market(s)
    eng.step([], s, NOW)
    tipos = [e["tipo"] for e in led.events()]
    assert tipos == ["intencao", "risco_aprovou", "execucao"]
    f = led.fiscal_rows()[0]
    # taxa taker 0,70% sobre ~R$300
    assert Decimal(f["taxa_brl"]) == Decimal("2.10")
    assert led.verify_chain()[0]


def test_kill_switch_impede_novas_ordens(tmp_path, cfg, limits):
    eng, ex, led, ks = make(tmp_path, cfg, limits, Fixed(intent(price="350500")))
    ks.activate("teste")
    s = snap()
    ex.update_market(s)
    for _ in range(3):
        eng.step([], s, NOW)
    tipos = [e["tipo"] for e in led.events()]
    assert tipos == ["kill_switch"]  # registra uma vez e não faz mais nada
    assert led.fiscal_rows() == []


def test_divergencia_de_reconciliacao_liga_kill_switch(tmp_path, cfg, limits):
    eng, ex, led, ks = make(tmp_path, cfg, limits, Fixed(intent(price="350500")))
    ex._balances["BRL"] -= Decimal("50")  # alguém mexeu na conta por fora
    s = snap()
    ex.update_market(s)
    eng.step([], s, NOW)
    assert ks.is_active()
    assert led.events("divergencia")


def test_erros_seguidos_da_exchange_ligam_kill_switch(tmp_path, cfg, limits):
    class Quebrada(PaperExchange):
        def place_order(self, order):
            raise ConnectionError("API fora do ar")

    ex = Quebrada(cfg.fees, {"BRL": Decimal("5000")})
    eng, ex, led, ks = make(tmp_path, cfg, limits, Fixed(intent(price="350500")), exchange=ex)
    for k in range(3):
        t = NOW + timedelta(minutes=k)
        s = snap(ts=t)
        ex.update_market(s)
        eng.step([], s, t)
    assert ks.is_active()
    assert len(led.events("erro_execucao")) == 3


def test_ordem_limite_nao_alcancada_nao_executa(tmp_path, cfg, limits):
    # limite de compra abaixo do ask: não executa, mas fica registrado
    eng, ex, led, _ = make(tmp_path, cfg, limits, Fixed(intent(price="349000")))
    s = snap()
    ex.update_market(s)
    eng.step([], s, NOW)
    assert led.events("nao_executada") and not led.fiscal_rows()


def test_ia_maliciosa_nao_passa_dos_limites(tmp_path, cfg, limits):
    """Uma 'IA' que tenta vender tudo a descoberto e comprar R$ 50 mil é barrada."""
    for i in (intent(notional="50000", price="350500"), intent(side=Side.SELL, price="349500")):
        d = tmp_path / i.id
        d.mkdir()
        eng, ex, led, _ = make(d, cfg, limits, Fixed(i))
        s = snap()
        ex.update_market(s)
        eng.step([], s, NOW)
        assert led.events("risco_rejeitou") and not led.fiscal_rows()


def test_backtest_sintetico_ponta_a_ponta(tmp_path, cfg, limits):
    candles = synthetic_candles(24 * 60, seed=7)
    rep = run_backtest(candles, cfg, limits, tmp_path)
    assert rep.cadeia_integra
    assert rep.kill_switch is None
    assert rep.execucoes > 0
    assert (tmp_path / "livro_fiscal.csv").exists()
    # o patrimônio fecha: caixa + posição = relatório
    assert rep.patrimonio_final > 0


def test_snapshot_do_backtest_usa_fechamento(cfg):
    c = synthetic_candles(1)[0]
    s = snapshot_from_candle("BTC/BRL", c, "1h", Decimal("0.1"))
    assert s.ts == c.ts + timedelta(hours=1) and s.last == c.close and s.bid < s.ask
