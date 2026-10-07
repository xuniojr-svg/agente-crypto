from datetime import timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from agente_crypto.domain import ApprovedOrder, Fill, Rejection, Side, base_asset
from agente_crypto.risk import CASH_BUFFER, evaluate

from conftest import NOW, PRICE, fresh_portfolio, intent, snap

PRICES = {"BTC": PRICE}


def ev(i, p=None, s=None, limits=None, kill=False, now=NOW, prices=PRICES):
    return evaluate(i, p or fresh_portfolio(), s or snap(), prices, now, limits, kill)


def buy_fill(qty, price=PRICE, ts=NOW):
    return Fill("x", "BTC/BRL", Side.BUY, Decimal(qty), price, Decimal("1"), ts, "paper")


def test_ordem_normal_aprovada(limits):
    d = ev(intent(), limits=limits)
    assert isinstance(d, ApprovedOrder)
    assert d.quantity == Decimal("0.00085714")
    assert d.limits_version == limits.version


def test_kill_switch_bloqueia(limits):
    d = ev(intent(), limits=limits, kill=True)
    assert isinstance(d, Rejection) and "kill switch ativo" in d.reasons


def test_acima_do_maximo_por_ordem(limits):
    d = ev(intent(notional="500.01"), limits=limits)
    assert isinstance(d, Rejection)


def test_par_nao_permitido(limits):
    d = ev(intent(symbol="DOGE/BRL"), s=snap(symbol="DOGE/BRL"), limits=limits)
    assert isinstance(d, Rejection)


def test_dado_velho_bloqueia(limits):
    d = ev(intent(), s=snap(ts=NOW - timedelta(seconds=limits.max_data_age_seconds + 1)), limits=limits)
    assert isinstance(d, Rejection) and "dado de mercado velho" in d.reasons


def test_spread_anormal_bloqueia(limits):
    d = ev(intent(), s=snap(spread_pct=Decimal("3")), limits=limits)
    assert isinstance(d, Rejection)


def test_preco_limite_longe_do_mercado(limits):
    d = ev(intent(price=PRICE * Decimal("1.02")), limits=limits)
    assert isinstance(d, Rejection)


def test_limite_diario_de_compras(limits):
    p = fresh_portfolio(cash="100000")
    p.day_bought_brl = Decimal("900")
    assert isinstance(ev(intent(notional="200"), p=p, limits=limits), Rejection)
    assert isinstance(ev(intent(notional="100"), p=p, limits=limits), ApprovedOrder)


def test_exposicao_maxima_por_ativo(limits):
    p = fresh_portfolio(cash="1000")  # patrimônio 1000, 30% = 300
    assert isinstance(ev(intent(notional="301"), p=p, limits=limits), Rejection)


def test_perda_diaria_so_permite_vender(limits):
    p = fresh_portfolio(cash="100000")
    p.apply_fill(buy_fill("0.001"))
    p.order_times.clear()
    p.day_start_equity = Decimal("110000")  # já caiu mais de 2% no dia
    assert isinstance(ev(intent(notional="100"), p=p, limits=limits), Rejection)
    sell = ev(intent(side=Side.SELL, notional="100"), p=p, limits=limits)
    assert isinstance(sell, ApprovedOrder)


def test_venda_sem_posicao_proibida(limits):
    d = ev(intent(side=Side.SELL), limits=limits)
    assert isinstance(d, Rejection)


def test_venda_maior_que_posicao_e_reduzida(limits):
    p = fresh_portfolio()
    p.apply_fill(buy_fill("0.0005"))
    p.order_times.clear()
    d = ev(intent(side=Side.SELL, notional="400"), p=p, limits=limits)
    assert isinstance(d, ApprovedOrder) and d.quantity == Decimal("0.0005")


def test_ordens_por_hora(limits):
    p = fresh_portfolio(cash="100000")
    p.order_times = [NOW - timedelta(minutes=m) for m in range(limits.max_orders_per_hour)]
    assert isinstance(ev(intent(), p=p, limits=limits), Rejection)


def test_max_posicoes(limits):
    p = fresh_portfolio(cash="100000", prices={"BTC": PRICE, "ETH": Decimal("1"), "SOL": Decimal("1")})
    for a in ("ETH", "SOL"):
        p.apply_fill(Fill("x", f"{a}/BRL", Side.BUY, Decimal("1"), Decimal("1"), Decimal(0), NOW, "paper"))
    p.order_times.clear()
    prices = {"BTC": PRICE, "ETH": Decimal("1"), "SOL": Decimal("1")}
    assert isinstance(ev(intent(), p=p, limits=limits, prices=prices), Rejection)


# ---- propriedade: nenhuma ordem aprovada viola um limite, para qualquer entrada ----

# Faixas escolhidas para cruzar as bordas dos limites: boa parte dos casos é
# aprovada e boa parte é rejeitada, então a propriedade não passa "no vazio".
def dec(lo, hi, places=2):
    return st.decimals(min_value=Decimal(lo), max_value=Decimal(hi), places=places)


@settings(max_examples=3000, deadline=None)
@given(
    side=st.sampled_from([Side.BUY, Side.SELL]),
    notional=dec("-10", "700"),
    price_dev=dec("-0.8", "0.8", 3),
    cash=dec("0", "6000"),
    held=dec("0", "0.006", 8),
    day_bought=st.one_of(dec("0", "1200"), dec("550", "1000")),
    day_start=dec("0", "6500"),
    spread=dec("0", "1.3"),
    age=st.integers(min_value=-10, max_value=400),
    n_orders=st.integers(min_value=0, max_value=5),
    kill=st.sampled_from([False] * 9 + [True]),
    symbol=st.sampled_from(["BTC/BRL"] * 8 + ["ETH/BRL", "DOGE/BRL"]),
)
def test_propriedade_aprovacoes_respeitam_limites(
    limits, side, notional, price_dev, cash, held, day_bought, day_start, spread, age, n_orders, kill, symbol
):
    p = fresh_portfolio(cash=str(max(cash, Decimal(0))))
    if held > 0:
        p.apply_fill(buy_fill(held, ts=NOW - timedelta(days=1)))
        p.cash_brl = max(cash, Decimal(0))
    p.day_bought_brl = max(day_bought, Decimal(0))
    p.day_start_equity = max(day_start, Decimal(0))
    p.order_times = [NOW - timedelta(minutes=i) for i in range(n_orders)]
    limit_price = PRICE * (1 + price_dev / 100)
    i = intent(side=side, notional=str(notional), price=limit_price.quantize(Decimal("0.01")), symbol=symbol)
    s = snap(ts=NOW - timedelta(seconds=age), spread_pct=spread, symbol=symbol)
    d = evaluate(i, p, s, {"BTC": PRICE, "ETH": PRICE, "DOGE": PRICE}, NOW, limits, kill)

    if isinstance(d, Rejection):
        assert d.reasons
        RESULTADOS["rejeitadas"] += 1
        return
    RESULTADOS["aprovadas"] += 1
    assert not kill
    assert d.symbol in limits.allowed_symbols
    assert limits.min_order_brl <= d.intent.notional_brl <= limits.max_order_brl
    assert d.quantity > 0
    assert abs(d.limit_price - s.last) / s.last * 100 <= limits.max_price_deviation_pct
    assert s.spread_pct <= limits.max_spread_pct
    assert NOW - s.ts <= timedelta(seconds=limits.max_data_age_seconds)
    assert n_orders < limits.max_orders_per_hour
    if side is Side.BUY:
        assert p.day_bought_brl + d.intent.notional_brl <= limits.max_daily_buy_brl
        assert p.cash_brl >= d.intent.notional_brl * CASH_BUFFER
        equity = p.equity({"BTC": PRICE})
        assert (p.qty(base_asset(symbol)) * PRICE + d.intent.notional_brl) / equity * 100 <= limits.max_exposure_pct_per_asset
    else:
        assert d.quantity <= p.qty(base_asset(symbol))


RESULTADOS = {"aprovadas": 0, "rejeitadas": 0}


def test_propriedade_nao_passou_no_vazio(limits):
    RESULTADOS.update(aprovadas=0, rejeitadas=0)
    test_propriedade_aprovacoes_respeitam_limites(limits)
    total = RESULTADOS["aprovadas"] + RESULTADOS["rejeitadas"]
    assert RESULTADOS["aprovadas"] / total > 0.02, RESULTADOS
    assert RESULTADOS["rejeitadas"] / total > 0.05, RESULTADOS


def test_venda_de_toda_a_posicao_nao_deixa_residuo(limits):
    p = fresh_portfolio()
    p.apply_fill(buy_fill("0.00085714"))
    p.order_times.clear()
    limit = Decimal("349300.00")
    notional = (p.qty("BTC") * limit).quantize(Decimal("0.01"))  # arredondamento perde 1 satoshi
    d = ev(intent(side=Side.SELL, notional=str(notional), price=limit), p=p, limits=limits)
    assert isinstance(d, ApprovedOrder) and d.quantity == p.qty("BTC")
