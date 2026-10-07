import sqlite3
from decimal import Decimal

import pytest

from agente_crypto.domain import Fill, Side
from agente_crypto.ledger import Ledger
from agente_crypto.portfolio import Portfolio

from conftest import NOW


def test_cadeia_detecta_adulteracao(tmp_path):
    db = tmp_path / "l.sqlite"
    led = Ledger(db)
    for i in range(5):
        led.record("teste", NOW, n=i, valor=Decimal("1.5"))
    assert led.verify_chain() == (True, None)

    with pytest.raises(sqlite3.IntegrityError):
        led.db.execute("UPDATE eventos SET dados='{}' WHERE seq=3")
    with pytest.raises(sqlite3.IntegrityError):
        led.db.execute("DELETE FROM eventos WHERE seq=3")

    # mesmo removendo o trigger (acesso direto ao arquivo), a cadeia acusa
    led.db.execute("DROP TRIGGER eventos_sem_update")
    led.db.execute("UPDATE eventos SET dados='{\"n\": 99}' WHERE seq=3")
    led.db.commit()
    assert led.verify_chain() == (False, 3)


def test_custo_medio_e_resultado_realizado(tmp_path):
    p = Portfolio(cash_brl=Decimal("10000"))
    led = Ledger(tmp_path / "f.sqlite")

    def fill(side, qty, price, fee):
        f = Fill("o", "BTC/BRL", side, Decimal(qty), Decimal(price), Decimal(fee), NOW, "paper")
        r = p.apply_fill(f)
        led.record_fiscal(f, p.positions["BTC"].avg_cost_brl, r)
        return r

    fill(Side.BUY, "0.01", "300000", "21")   # custo 3021
    fill(Side.BUY, "0.01", "320000", "22.4")  # custo 3222.4 -> médio 312170 por BTC
    assert p.positions["BTC"].avg_cost_brl == Decimal("312170")
    r = fill(Side.SELL, "0.01", "330000", "23.1")  # recebe 3276.9, custo 3121.70
    assert r == Decimal("155.20")
    assert p.positions["BTC"].avg_cost_brl == Decimal("312170")  # venda não muda o médio
    fill(Side.SELL, "0.01", "310000", "21.7")
    assert p.qty("BTC") == 0 and p.positions["BTC"].avg_cost_brl == 0

    rows = led.fiscal_rows()
    assert [r["operacao"] for r in rows] == ["compra", "compra", "venda", "venda"]
    assert rows[2]["resultado_realizado_brl"] == "155.20"
    assert rows[0]["data_hora_brt"].endswith("-03:00")
    n = led.export_fiscal_csv(tmp_path / "f.csv")
    assert n == 4 and "custo_medio_apos_brl" in (tmp_path / "f.csv").read_text()


def test_venda_a_descoberto_impossivel_na_carteira():
    p = Portfolio(cash_brl=Decimal("1000"))
    with pytest.raises(ValueError):
        p.apply_fill(Fill("o", "BTC/BRL", Side.SELL, Decimal("0.1"), Decimal("1"), Decimal(0), NOW, "paper"))
