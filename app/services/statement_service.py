"""Datos y PDF del estado de cuenta: aquí se consulta la base; el dibujo del PDF vive en ``statement_pdf``."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from flask import current_app
from sqlalchemy import asc, desc
from ..models import Transaction, now_utc
from .statement_pdf import StatementData, StatementRow, render_statement

MOVEMENT_TYPES=('INGRESO','TRANSFERENCIA','PAGO','RECARGA')


def _money(value):
    return Decimal(str(value if value is not None else 0)).quantize(Decimal('0.01'))


def build_statement_data(account,owner,start=None,end=None,operation_type=None,issued_at=None):
    """Reúne lo que muestra el PDF respetando los filtros de fechas (desde/hasta, inclusivos) y de tipo.

    Los saldos inicial y final son los de la cuenta al empezar y terminar el periodo (todos los tipos); los totales de
    ingresos y egresos corresponden a los movimientos listados, es decir, a los filtros aplicados.
    """
    base=Transaction.query.filter_by(account_id=account.id)
    window=base
    if start:
        window=window.filter(Transaction.created_at>=datetime.combine(start,time.min))
    if end:
        window=window.filter(Transaction.created_at<datetime.combine(end+timedelta(days=1),time.min))
    ordering=(asc(Transaction.created_at),asc(Transaction.id))
    first_in_window=window.order_by(*ordering).first()
    last_in_window=window.order_by(desc(Transaction.created_at),desc(Transaction.id)).first()
    if first_in_window is not None:
        opening=_money(first_in_window.balance_after)-_money(first_in_window.amount)
    else:
        before=(base.filter(Transaction.created_at<datetime.combine(start,time.min)).order_by(desc(Transaction.created_at),desc(Transaction.id)).first()
                if start else None)
        earliest=None if before else base.order_by(*ordering).first()
        if before is not None:
            opening=_money(before.balance_after)
        elif earliest is not None:
            opening=_money(earliest.balance_after)-_money(earliest.amount)
        else:
            opening=_money(account.balance)
    closing=_money(last_in_window.balance_after) if last_in_window is not None else opening

    listed=window.filter_by(operation_type=operation_type) if operation_type else window
    rows=[StatementRow(item.created_at,item.operation_type,item.description,_money(item.amount),_money(item.balance_after))
          for item in listed.order_by(*ordering).all()]
    income=sum((row.amount for row in rows if row.amount>0),Decimal('0.00'))
    expense=sum((-row.amount for row in rows if row.amount<0),Decimal('0.00'))

    # Sin filtro de fechas, el periodo va de la primera a la última fecha registrada de la cuenta.
    period_start=start
    period_end=end
    if period_start is None or period_end is None:
        first_recorded=base.order_by(*ordering).first()
        last_recorded=base.order_by(desc(Transaction.created_at),desc(Transaction.id)).first()
        if period_start is None and first_recorded is not None:
            period_start=first_recorded.created_at.date()
        if period_end is None and last_recorded is not None:
            period_end=last_recorded.created_at.date()
    return StatementData(
        holder_name=owner.full_name,holder_dni=owner.dni or '',account_type=account.account_type,
        account_number=account.number,currency=account.currency,period_start=period_start,period_end=period_end,
        issued_at=issued_at or now_utc(),opening_balance=opening,closing_balance=closing,total_income=income,
        total_expense=expense,legal_notice=current_app.config['LEGAL_NOTICE'],type_filter=operation_type or '',rows=rows,
    )


def account_statement_pdf(account,owner,start=None,end=None,operation_type=None):
    return render_statement(build_statement_data(account,owner,start,end,operation_type))
