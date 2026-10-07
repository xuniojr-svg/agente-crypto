"""Caixa-preta auditável e livro fiscal, em SQLite.

- `eventos`: append-only, cada linha guarda o hash da anterior (cadeia).
  Qualquer edição ou remoção quebra `verify_chain()`.
- `livro_fiscal`: uma linha por execução, com custo médio e resultado
  realizado em BRL, no formato que a Fase 2 usará com dinheiro real.
Triggers do SQLite impedem UPDATE e DELETE nas duas tabelas.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .domain import Fill

GENESIS = "0" * 64
BRT = timezone(timedelta(hours=-3))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tipo TEXT NOT NULL,
    dados TEXT NOT NULL,
    hash_anterior TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS livro_fiscal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    data_hora_utc TEXT NOT NULL,
    data_hora_brt TEXT NOT NULL,
    exchange TEXT NOT NULL,
    par TEXT NOT NULL,
    operacao TEXT NOT NULL,
    quantidade TEXT NOT NULL,
    preco_brl TEXT NOT NULL,
    valor_brl TEXT NOT NULL,
    taxa_brl TEXT NOT NULL,
    custo_medio_apos_brl TEXT NOT NULL,
    resultado_realizado_brl TEXT NOT NULL,
    ordem_id TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS eventos_sem_update BEFORE UPDATE ON eventos
BEGIN SELECT RAISE(ABORT, 'eventos é append-only'); END;
CREATE TRIGGER IF NOT EXISTS eventos_sem_delete BEFORE DELETE ON eventos
BEGIN SELECT RAISE(ABORT, 'eventos é append-only'); END;
CREATE TRIGGER IF NOT EXISTS fiscal_sem_update BEFORE UPDATE ON livro_fiscal
BEGIN SELECT RAISE(ABORT, 'livro_fiscal é append-only'); END;
CREATE TRIGGER IF NOT EXISTS fiscal_sem_delete BEFORE DELETE ON livro_fiscal
BEGIN SELECT RAISE(ABORT, 'livro_fiscal é append-only'); END;
"""


def _json_default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "value"):
        return o.value
    raise TypeError(type(o))


def _hash(prev: str, ts: str, tipo: str, dados: str) -> str:
    return hashlib.sha256(f"{prev}|{ts}|{tipo}|{dados}".encode()).hexdigest()


class Ledger:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(_SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _last_hash(self) -> str:
        row = self.db.execute("SELECT hash FROM eventos ORDER BY seq DESC LIMIT 1").fetchone()
        return row[0] if row else GENESIS

    def record(self, tipo: str, ts: datetime, **dados) -> str:
        payload = json.dumps(dados, default=_json_default, sort_keys=True, ensure_ascii=False)
        ts_s = ts.isoformat()
        prev = self._last_hash()
        h = _hash(prev, ts_s, tipo, payload)
        with self.db:
            self.db.execute(
                "INSERT INTO eventos (ts, tipo, dados, hash_anterior, hash) VALUES (?,?,?,?,?)",
                (ts_s, tipo, payload, prev, h),
            )
        return h

    def record_fiscal(self, fill: Fill, avg_cost_after: Decimal, realized: Decimal) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO livro_fiscal (data_hora_utc, data_hora_brt, exchange, par, operacao,"
                " quantidade, preco_brl, valor_brl, taxa_brl, custo_medio_apos_brl,"
                " resultado_realizado_brl, ordem_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fill.ts.astimezone(timezone.utc).isoformat(),
                    fill.ts.astimezone(BRT).isoformat(),
                    fill.exchange,
                    fill.symbol,
                    "compra" if fill.side.value == "buy" else "venda",
                    str(fill.quantity),
                    str(fill.price.quantize(Decimal("0.01"))),
                    str(fill.gross_brl.quantize(Decimal("0.01"))),
                    str(fill.fee_brl),
                    str(avg_cost_after.quantize(Decimal("0.01"))),
                    str(realized.quantize(Decimal("0.01"))),
                    fill.order_id,
                ),
            )

    def verify_chain(self) -> tuple[bool, int | None]:
        """Devolve (ok, seq do primeiro evento adulterado)."""
        prev = GENESIS
        for seq, ts, tipo, dados, hprev, h in self.db.execute(
            "SELECT seq, ts, tipo, dados, hash_anterior, hash FROM eventos ORDER BY seq"
        ):
            if hprev != prev or _hash(prev, ts, tipo, dados) != h:
                return False, seq
            prev = h
        return True, None

    def events(self, tipo: str | None = None) -> list[dict]:
        q = "SELECT seq, ts, tipo, dados FROM eventos"
        args: tuple = ()
        if tipo:
            q += " WHERE tipo = ?"
            args = (tipo,)
        return [
            {"seq": s, "ts": t, "tipo": k, **json.loads(d)}
            for s, t, k, d in self.db.execute(q + " ORDER BY seq", args)
        ]

    def fiscal_rows(self) -> list[dict]:
        cur = self.db.execute("SELECT * FROM livro_fiscal ORDER BY seq")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur]

    def export_fiscal_csv(self, path: str | Path) -> int:
        rows = self.fiscal_rows()
        with open(path, "w", newline="", encoding="utf-8") as f:
            if rows:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
                w.writeheader()
                w.writerows(rows)
        return len(rows)
