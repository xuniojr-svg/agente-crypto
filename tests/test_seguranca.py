"""Garantias estruturais: sem saque, ordem só via motor de risco."""

import inspect
import re
from datetime import datetime
from decimal import Decimal

import pytest

from agente_crypto.domain import ApprovedOrder
from agente_crypto.execution import base, paper
from agente_crypto.execution.paper import PaperExchange

from conftest import intent

PROIBIDO = re.compile(r"withdraw|transfer|saque|sacar|transferir|api_key|deposit_address", re.I)


def test_nenhum_adaptador_tem_metodo_de_saque():
    for mod in (base, paper):
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            for name, _ in inspect.getmembers(cls, callable):
                assert not PROIBIDO.search(name), f"{cls.__name__}.{name} parece saque/transferência"


def test_nao_da_para_forjar_ordem_aprovada():
    with pytest.raises(PermissionError):
        ApprovedOrder(intent=intent(), quantity=Decimal("1"), approved_at=datetime.now(), limits_version="x")
    with pytest.raises(PermissionError):
        ApprovedOrder(intent=intent(), quantity=Decimal("1"), approved_at=datetime.now(),
                      limits_version="x", _token=object())


def test_executor_recusa_intencao_crua(cfg):
    ex = PaperExchange(cfg.fees, {"BRL": Decimal("1000")})
    with pytest.raises(PermissionError):
        ex.place_order(intent())
