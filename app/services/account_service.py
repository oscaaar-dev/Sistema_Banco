"""Datos de la pantalla «Cuentas»: todo se calcula a partir de la base, sin cifras inventadas."""
import calendar
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from sqlalchemy import desc, func
from ..models import db, Account, BankCard, Transaction, User, now_utc
from .account_identifiers import cci_for

ZERO=Decimal('0.00')
MONTH_NAMES=('enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre')
CHART_WIDTH=310
CHART_HEIGHT=104
_MONTH_PATTERN=re.compile(r'(\d{4})-(\d{2})')


def _money(value):
    return Decimal(str(value if value is not None else 0)).quantize(Decimal('0.01'))


def parse_month(raw):
    """«2026-10» → primer día de ese mes; ``None`` si el texto no es un mes válido."""
    match=_MONTH_PATTERN.fullmatch((raw or '').strip())
    if not match:
        return None
    year,month=int(match.group(1)),int(match.group(2))
    if not 1<=month<=12 or not 1900<=year<=2200:
        return None
    return date(year,month,1)


def _add_months(first_day,offset):
    index=first_day.year*12+first_day.month-1+offset
    return date(index//12,index%12+1,1)


def opening_date(account):
    """Fecha de apertura según la base: la más antigua entre el alta del cliente y el primer movimiento de la cuenta.

    La tabla de cuentas no guarda su fecha de alta; una cuenta se abre al registrarse el cliente, y las cuentas de
    muestra con saldo de apertura registran ese movimiento antes de la fecha de carga.
    """
    first_movement=db.session.query(func.min(Transaction.created_at)).filter(Transaction.account_id==account.id).scalar()
    owner=db.session.get(User,account.user_id)
    candidates=[value for value in (first_movement,owner.created_at if owner else None) if value is not None]
    return min(candidates).date() if candidates else None


def month_summary(account,month_start):
    """Ingresos, gastos y serie diaria de un mes calendario."""
    month_end=_add_months(month_start,1)
    rows=(db.session.query(Transaction.amount,Transaction.created_at)
          .filter(Transaction.account_id==account.id,
                  Transaction.created_at>=datetime.combine(month_start,time.min),
                  Transaction.created_at<datetime.combine(month_end,time.min))
          .all())
    days=calendar.monthrange(month_start.year,month_start.month)[1]
    daily=[{'day':index+1,'income':ZERO,'expense':ZERO} for index in range(days)]
    income=expense=ZERO
    for amount,created_at in rows:
        value=_money(amount)
        entry=daily[created_at.day-1]
        if value>0:
            income+=value; entry['income']+=value
        elif value<0:
            expense+=-value; entry['expense']+=-value
    summary={'month':month_start,'label':month_label(month_start),'income':income,'expense':expense,
             'net':income-expense,'count':len(rows),'daily':daily}
    summary['bars']=_chart_bars(summary)
    summary['chart_width']=CHART_WIDTH
    summary['chart_height']=CHART_HEIGHT
    return summary


def month_label(month_start):
    return f'{MONTH_NAMES[month_start.month-1]} de {month_start.year}'


def _chart_bars(summary):
    """Barras diarias para un SVG sencillo: los ingresos suben desde la línea central y los gastos bajan."""
    daily=summary['daily']
    peak=max([entry['income'] for entry in daily]+[entry['expense'] for entry in daily]+[ZERO])
    middle=CHART_HEIGHT/2
    slot=CHART_WIDTH/len(daily)
    width=round(max(2.0,slot*0.6),2)
    bars=[]
    for entry in daily:
        x=round(entry['day']*slot-slot/2-width/2,2)
        up=round((float(entry['income'])/float(peak))*(middle-6),2) if peak else 0
        down=round((float(entry['expense'])/float(peak))*(middle-6),2) if peak else 0
        bars.append({'x':x,'width':width,'day':entry['day'],'income':entry['income'],'expense':entry['expense'],
                     'up':up,'up_y':round(middle-up,2),'down':down,'down_y':middle})
    return bars


def movement_months(account):
    """Primer y último mes con movimientos (como fechas del día 1); ``(None, None)`` si no hay ninguno."""
    first,last=(db.session.query(func.min(Transaction.created_at),func.max(Transaction.created_at))
                .filter(Transaction.account_id==account.id).one())
    if first is None:
        return None,None
    return first.date().replace(day=1),last.date().replace(day=1)


def recent_movements(account,limit=5):
    return (Transaction.query.filter_by(account_id=account.id)
            .order_by(desc(Transaction.created_at),desc(Transaction.id)).limit(limit).all())


def debit_cards(account):
    return BankCard.query.filter_by(account_id=account.id,user_id=account.user_id).order_by(BankCard.id).all()


def account_overview(account,month_start=None):
    """Todo lo que muestra /accounts para una cuenta. ``month_start`` es el mes del resumen (por defecto, el actual)."""
    today=now_utc().date()
    current_month=today.replace(day=1)
    month_start=month_start or current_month
    first_month,last_month=movement_months(account)
    summary=month_summary(account,month_start)
    previous_month=_add_months(month_start,-1)
    next_month=_add_months(month_start,1)
    owner=db.session.get(User,account.user_id)
    return {
        'account':account,
        'holder':owner.full_name if owner else '',
        'cci':cci_for(account),
        'opened':opening_date(account),
        'summary':summary,
        'is_current_month':month_start==current_month,
        'previous_month':previous_month if first_month and previous_month>=first_month else None,
        'next_month':next_month if next_month<=current_month else None,
        'latest_month_with_movements':last_month if last_month and last_month!=month_start else None,
        'recent':recent_movements(account),
        'cards':debit_cards(account),
        'has_movements':first_month is not None,
    }
