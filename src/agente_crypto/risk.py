"""Motor de risco: regras rígidas, sem rede, sem IA.

`evaluate` é uma função pura: recebe a intenção, o estado e o mercado,
e devolve ApprovedOrder ou Rejection com todos os motivos.
Só este módulo consegue criar ApprovedOrder.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from .config import RiskLimits
from .domain import (
    _RISK_TOKEN,
    ApprovedOrder,
    MarketSnapshot,
    OrderIntent,
    Rejection,
    Side,
    base_asset,
)
from .portfolio import Portfolio

QTY_STEP = Decimal("0.00000001")
CASH_BUFFER = Decimal("1.02")  # reserva para taxa + slippage nas compras
FULL_EXIT_RATIO = Decimal("0.999")


def evaluate(
    intent: OrderIntent,
    portfolio: Portfolio,
    snapshot: MarketSnapshot,
    prices: dict[str, Decimal],
    now: datetime,
    limits: RiskLimits,
    kill_switch_active: bool,
) -> ApprovedOrder | Rejection:
    reasons: list[str] = []
    asset = base_asset(intent.symbol)

    # --- bloqueios gerais ---
    if kill_switch_active:
        reasons.append("kill switch ativo")
    if intent.symbol not in limits.allowed_symbols:
        reasons.append(f"par {intent.symbol} fora da lista permitida")
    if snapshot.symbol != intent.symbol:
        reasons.append("dado de mercado é de outro par")
    if now - snapshot.ts > timedelta(seconds=limits.max_data_age_seconds):
        reasons.append("dado de mercado velho")
    if snapshot.ts > now + timedelta(seconds=5):
        reasons.append("dado de mercado no futuro")
    if snapshot.bid <= 0 or snapshot.ask < snapshot.bid or snapshot.last <= 0:
        reasons.append("cotação inválida")
    elif snapshot.spread_pct > limits.max_spread_pct:
        reasons.append(f"spread {snapshot.spread_pct:.2f}% acima do limite")

    # --- forma da ordem ---
    if intent.notional_brl < limits.min_order_brl:
        reasons.append("ordem abaixo do mínimo")
    if intent.notional_brl > limits.max_order_brl:
        reasons.append(f"ordem de R${intent.notional_brl} acima do máximo R${limits.max_order_brl}")
    if intent.limit_price <= 0:
        reasons.append("preço limite inválido")
    elif snapshot.last > 0:
        dev = abs(intent.limit_price - snapshot.last) / snapshot.last * 100
        if dev > limits.max_price_deviation_pct:
            reasons.append(f"preço limite desvia {dev:.2f}% do mercado")

    cutoff = now - timedelta(hours=1)
    if sum(1 for t in portfolio.order_times if t > cutoff) >= limits.max_orders_per_hour:
        reasons.append("limite de ordens por hora atingido")

    quantity = Decimal("0")
    if intent.limit_price > 0 and not reasons:
        quantity = (intent.notional_brl / intent.limit_price).quantize(QTY_STEP, rounding=ROUND_DOWN)

    if intent.side is Side.BUY:
        if portfolio.day_bought_brl + intent.notional_brl > limits.max_daily_buy_brl:
            reasons.append("limite diário de compras atingido")
        if portfolio.cash_brl < intent.notional_brl * CASH_BUFFER:
            reasons.append("saldo em BRL insuficiente")
        if asset not in portfolio.open_assets() and len(portfolio.open_assets()) >= limits.max_open_positions:
            reasons.append("número máximo de posições atingido")
        try:
            equity = portfolio.equity(prices)
        except KeyError:
            equity = None
            reasons.append("sem preço para avaliar a carteira")
        if equity is not None and equity > 0:
            exposure = (portfolio.qty(asset) * snapshot.last + intent.notional_brl) / equity * 100
            if exposure > limits.max_exposure_pct_per_asset:
                reasons.append(f"exposição em {asset} iria a {exposure:.1f}%")
            if portfolio.day_start_equity > 0:
                loss = (portfolio.day_start_equity - equity) / portfolio.day_start_equity * 100
                if loss >= limits.max_daily_loss_pct:
                    reasons.append(f"perda diária de {loss:.2f}% atingiu o limite; só vendas")
    else:
        held = portfolio.qty(asset)
        if held <= 0:
            reasons.append(f"sem posição em {asset} para vender (venda a descoberto proibida)")
        # Venda nunca maior que a posição: reduz para o que existe.
        # Se a intenção cobre ~toda a posição, vende tudo para não deixar resíduo.
        if quantity >= held * FULL_EXIT_RATIO:
            quantity = held
        quantity = min(quantity, held)

    if not reasons and quantity <= 0:
        reasons.append("quantidade resultante é zero")

    if reasons:
        return Rejection(intent=intent, reasons=tuple(reasons))
    return ApprovedOrder(
        intent=intent,
        quantity=quantity,
        approved_at=now,
        limits_version=limits.version,
        _token=_RISK_TOKEN,
    )
