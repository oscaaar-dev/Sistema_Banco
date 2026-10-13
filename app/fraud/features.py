"""Extracción de características de riesgo.

Solo se usan datos que el sistema ya recopila legítimamente y que se calculan con consultas **acotadas** (ventanas de tiempo y
límites de filas, con índices). Nunca se leen contraseñas, códigos OTP, fotografías ni contenido sensible, y no se guardan
direcciones IP: solo se compara el hash que el sistema ya calcula y se conserva un booleano («contexto nuevo»).

La duración declarada por el formulario (``form_duration_seconds``) NO se usa: la envía el navegador del cliente y no es fiable.
"""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from statistics import median

from ..models import AuditLog, OperationCapture, Transaction, Transfer, db, now_utc

HISTORY_DAYS = 90
HISTORY_LIMIT = 100
RECENT_LIMIT = 50

LOGIN_FAILURE_ACTIONS = ('LOGIN_FAILURE',)
OTP_FAILURE_ACTIONS = ('LOGIN_OTP_FAILURE', 'OPERATION_OTP_FAILURE', 'INTERNAL_TRANSFER_OTP_FAILURE')
OTP_SENT_ACTIONS = ('LOGIN_OTP_SENT',)


@dataclass
class OperationFeatures:
    kind: str
    amount: Decimal
    balance: Decimal
    n_history: int = 0
    median_amount: Decimal | None = None
    ops_2m: int = 0
    ops_10m: int = 0
    ops_1h: int = 0
    first_time_beneficiary: bool | None = None
    distinct_beneficiaries_24h: int = 0
    same_amount_repeats_1h: int = 0
    password_changed_recently: bool = False
    failed_logins_30m: int = 0
    otp_failures_30m: int = 0
    new_context: bool | None = None

    @property
    def balance_share(self):
        return float(self.amount / self.balance) if self.balance and self.balance > 0 else None


@dataclass
class AccessFeatures:
    failed_logins_30m: int = 0
    otp_failures_30m: int = 0
    otp_sent_60m: int = 0
    new_context: bool | None = None
    n_prior_access_events: int = 0


def _count_recent(rows, now, minutes):
    limit = now - timedelta(minutes=minutes)
    return sum(1 for created_at in rows if created_at >= limit)


def _audit_counts(user_id, now):
    """Eventos de seguridad recientes del usuario, en una sola consulta acotada (ventana de 60 min)."""
    since = now - timedelta(minutes=60)
    rows = (db.session.query(AuditLog.action, AuditLog.created_at)
            .filter(AuditLog.user_id == user_id, AuditLog.created_at >= since,
                    AuditLog.action.in_(LOGIN_FAILURE_ACTIONS + OTP_FAILURE_ACTIONS + OTP_SENT_ACTIONS))
            .order_by(AuditLog.created_at.desc()).limit(200).all())
    window = now - timedelta(minutes=30)
    login_failures = sum(1 for action, when in rows if action in LOGIN_FAILURE_ACTIONS and when >= window)
    otp_failures = sum(1 for action, when in rows if action in OTP_FAILURE_ACTIONS and when >= window)
    otp_sent = sum(1 for action, when in rows if action in OTP_SENT_ACTIONS)
    return login_failures, otp_failures, otp_sent


def operation_features(user, account, kind, amount, *, beneficiary=None, ip_hash=None, now=None):
    """Características de una operación de salida de dinero, calculadas ANTES de modificar saldos."""
    now = now or now_utc()
    value = Decimal(str(amount))
    features = OperationFeatures(kind=kind, amount=value, balance=Decimal(str(account.balance)))

    history = (db.session.query(Transaction.amount)
               .filter(Transaction.account_id == account.id, Transaction.operation_type == kind, Transaction.amount < 0,
                       Transaction.created_at >= now - timedelta(days=HISTORY_DAYS))
               .order_by(Transaction.created_at.desc()).limit(HISTORY_LIMIT).all())
    amounts = [abs(Decimal(str(row[0]))) for row in history]
    features.n_history = len(amounts)
    features.median_amount = Decimal(str(median(amounts))) if amounts else None

    recent = [row[0] for row in (db.session.query(Transaction.created_at)
                                 .filter(Transaction.account_id == account.id, Transaction.amount < 0,
                                         Transaction.created_at >= now - timedelta(hours=1))
                                 .order_by(Transaction.created_at.desc()).limit(RECENT_LIMIT).all())]
    features.ops_1h = len(recent)
    features.ops_10m = _count_recent(recent, now, 10)
    features.ops_2m = _count_recent(recent, now, 2)

    if beneficiary is not None:
        features.first_time_beneficiary = db.session.query(Transfer.id).filter(
            Transfer.account_id == account.id, Transfer.beneficiary_id == beneficiary.id).first() is None
        day = (db.session.query(Transfer.beneficiary_id, Transfer.amount, Transfer.created_at)
               .filter(Transfer.account_id == account.id, Transfer.created_at >= now - timedelta(hours=24))
               .order_by(Transfer.created_at.desc()).limit(200).all())
        distinct = {row[0] for row in day} | {beneficiary.id}
        features.distinct_beneficiaries_24h = len(distinct)
        hour = now - timedelta(hours=1)
        features.same_amount_repeats_1h = sum(1 for b_id, amt, when in day
                                              if b_id == beneficiary.id and when >= hour and Decimal(str(amt)) == value)

    features.password_changed_recently = bool(user.password_changed_at and user.password_changed_at >= now - timedelta(hours=24))
    features.failed_logins_30m, features.otp_failures_30m, _ = _audit_counts(user.id, now)

    if ip_hash:
        prior = {row[0] for row in (db.session.query(OperationCapture.ip_hash)
                                    .filter(OperationCapture.user_id == user.id, OperationCapture.ip_hash.isnot(None),
                                            OperationCapture.created_at >= now - timedelta(days=HISTORY_DAYS))
                                    .order_by(OperationCapture.created_at.desc()).limit(50).all())}
        features.new_context = (ip_hash not in prior) if len(prior) >= 2 else None   # sin historial suficiente, no se infiere nada
    return features


def access_features(user, *, ip_hash=None, now=None):
    """Características de un evento de acceso (fallo de clave, fallo de OTP o acceso correcto)."""
    now = now or now_utc()
    features = AccessFeatures()
    features.failed_logins_30m, features.otp_failures_30m, features.otp_sent_60m = _audit_counts(user.id, now)
    prior_rows = (db.session.query(AuditLog.ip_local)
                  .filter(AuditLog.user_id == user.id, AuditLog.action == 'LOGIN_OTP_SENT', AuditLog.ip_local.isnot(None),
                          AuditLog.created_at >= now - timedelta(days=HISTORY_DAYS), AuditLog.created_at < now - timedelta(minutes=60))
                  .order_by(AuditLog.created_at.desc()).limit(50).all())
    prior = {row[0] for row in prior_rows}
    features.n_prior_access_events = len(prior_rows)
    if ip_hash and len(prior_rows) >= 3:
        features.new_context = ip_hash not in prior
    return features
