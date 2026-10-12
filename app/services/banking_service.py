from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from ..models import db, Account, Beneficiary, Transaction, Transfer, InteractionSession, MLResult, InteractionFeatures, TransactionStep, AuditLog, OperationRiskAssessment
from ..ai.interaction_model import cluster_interactions
from ..ai.operation_risk import assess_operation_risk
from .capture_service import add_capture

def account_for(user):
    return Account.query.filter_by(user_id=user.id,status='ACTIVA').first()

def log(user,action,module,result='COMPLETADA',ip_local=None):
    db.session.add(AuditLog(user_id=user.id if user and user.is_authenticated else None,action=action,module=module,result=result,ip_local=ip_local))

def operation_risk_for(user,kind,amount,duration_seconds=None):
    account=account_for(user)
    if not account:raise ValueError('No se encontró una cuenta activa.')
    value=Decimal(str(amount)).quantize(Decimal('0.01'))
    now=datetime.now(timezone.utc).replace(tzinfo=None)
    history_start=now-timedelta(days=90)
    recent_start=now-timedelta(hours=1)
    comparable_transactions=Transaction.query.filter_by(account_id=account.id,operation_type=kind).filter(Transaction.created_at>=history_start).order_by(Transaction.created_at.desc()).limit(100).all()
    recent_operation_count=Transaction.query.filter_by(account_id=account.id).filter(Transaction.created_at>=recent_start).count()
    return assess_operation_risk(value,account.balance,[abs(tx.amount) for tx in comparable_transactions],recent_operation_count,duration_seconds)

def _store_cluster_results():
    sessions=InteractionSession.query.order_by(InteractionSession.started_at.asc()).all()
    outcomes=cluster_interactions(sessions)
    if not outcomes:return
    for session,outcome in zip(sessions,outcomes):
        result=MLResult.query.filter_by(session_id=session.id).first()
        if result:
            result.cluster_id=outcome['cluster_id']; result.label=outcome['label']; result.method='K-Means (sesiones de interacción)'
        else:
            db.session.add(MLResult(session_id=session.id,cluster_id=outcome['cluster_id'],label=outcome['label'],method='K-Means (sesiones de interacción)'))

def process_operation(user,kind,amount,description,beneficiary_id=None,service=None,consent=False,duration_seconds=None,idempotency_key=None,capture=None,capture_meta=None):
    account=(Account.query.filter_by(user_id=user.id,status='ACTIVA')
             .with_for_update().first())
    if not account:raise ValueError('No se encontró una cuenta activa.')
    value=Decimal(str(amount)).quantize(Decimal('0.01'))
    if idempotency_key is not None:
        if not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',idempotency_key):
            raise ValueError('La clave de operación no es válida. Vuelve a cargar el formulario.')
        previous=Transaction.query.filter_by(account_id=account.id,idempotency_key=idempotency_key).first()
        if previous:
            if previous.operation_type==kind and previous.amount==-value and previous.description==description[:180]:
                return previous
            raise ValueError('Esta clave ya se utilizó para otra operación. Vuelve a cargar el formulario.')
    if not (Decimal('0.01')<=value<=Decimal('10000')):raise ValueError('El monto debe estar entre S/ 0.01 y S/ 10,000.00.')
    if kind in ('TRANSFERENCIA','PAGO','RECARGA') and account.balance<value:raise ValueError('Saldo insuficiente para esta operación.')
    beneficiary=None
    if kind=='TRANSFERENCIA':
        beneficiary=Beneficiary.query.filter_by(id=beneficiary_id,user_id=user.id).first()
        if beneficiary is None:raise ValueError('Selecciona un destinatario válido.')
    start=datetime.now(timezone.utc).replace(tzinfo=None)
    risk=operation_risk_for(user,kind,value,duration_seconds)
    session_duration=max(1,int(duration_seconds)) if duration_seconds is not None else 1
    session=InteractionSession(user_id=user.id,operation_type=kind,started_at=start,consent_given=bool(consent),steps=5,errors=0,back_actions=0,face_detected=False,duration_seconds=session_duration)
    db.session.add(session);db.session.flush()
    db.session.add(OperationRiskAssessment(session_id=session.id,score=risk['score'],level=risk['level'],reasons=risk['reasons']))
    TransactionStep(session_id=session.id,event_type='completed',step_name='Confirmación de operación',metadata_json={'operation_type':kind})
    account.balance=(account.balance-value).quantize(Decimal('0.01'))
    tx=Transaction(account_id=account.id,session_id=session.id,operation_type=kind,description=description[:180],amount=-value,balance_after=account.balance,status='COMPLETADA',idempotency_key=idempotency_key)
    session.ended_at=datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.add(tx)
    db.session.flush()
    if capture:
        # La foto va en la misma transacción que la operación: si falla, no se completa nada.
        add_capture(user.id,tx.id,capture,**(capture_meta or {}))
    if kind=='TRANSFERENCIA':
        db.session.add(Transfer(
            transaction_id=tx.id,
            account_id=account.id,
            beneficiary_id=beneficiary.id,
            amount=value,
            currency=account.currency,
            status='REGISTRADA',
            is_seed=tx.is_seed,
        ))
    db.session.add(InteractionFeatures(session_id=session.id,duration_seconds=session.duration_seconds,steps=session.steps,errors=session.errors,back_actions=session.back_actions,face_detection_rate=0.0,operation_type=kind))
    db.session.add(MLResult(session_id=session.id,cluster_id=None,label='Pendiente de datos suficientes',method='K-Means exploratorio'))
    log(user,kind,'OPERACIONES')
    _store_cluster_results()
    db.session.commit()
    return tx


def process_internal_transfer(user,source_account_id,destination_account_id,amount,duration_seconds=None,idempotency_key=None,capture=None,capture_meta=None):
    if source_account_id==destination_account_id:
        raise ValueError('Elige dos cuentas distintas para el traslado.')
    if idempotency_key is not None and not re.fullmatch(r'[A-Za-z0-9_-]{22,64}',idempotency_key):
        raise ValueError('La clave de operación no es válida. Vuelve a cargar el formulario.')
    accounts=(Account.query.filter(
        Account.user_id==user.id,Account.status=='ACTIVA',Account.id.in_((source_account_id,destination_account_id))
    ).order_by(Account.id).with_for_update().all())
    if len(accounts)!=2:
        raise ValueError('No se encontraron ambas cuentas activas a tu nombre.')
    by_id={account.id:account for account in accounts}
    source=by_id[source_account_id]; destination=by_id[destination_account_id]
    if source.currency!=destination.currency:
        raise ValueError('Las cuentas deben tener la misma moneda para un traslado interno.')
    value=Decimal(str(amount)).quantize(Decimal('0.01'))
    if not Decimal('0.01')<=value<=Decimal('10000'):
        raise ValueError('El monto debe estar entre S/ 0.01 y S/ 10,000.00.')
    if source.balance<value:
        raise ValueError('Saldo insuficiente en la cuenta de origen.')
    if idempotency_key:
        previous_source=Transaction.query.filter_by(account_id=source.id,idempotency_key=idempotency_key).first()
        previous_destination=Transaction.query.filter_by(account_id=destination.id,idempotency_key=idempotency_key).first()
        if previous_source and previous_destination:
            if (previous_source.operation_type=='TRANSFERENCIA' and previous_source.amount==-value
                    and previous_destination.operation_type=='INGRESO' and previous_destination.amount==value):
                return previous_source
            raise ValueError('Esta clave ya se usó para otra operación.')
        if previous_source or previous_destination:
            raise ValueError('La operación quedó en un estado incompleto. Contacta a soporte antes de reintentar.')

    risk=operation_risk_for(user,'TRANSFERENCIA',value,duration_seconds)
    duration=max(1,int(duration_seconds)) if duration_seconds is not None else 1
    interaction=InteractionSession(user_id=user.id,operation_type='TRANSFERENCIA',started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                                   duration_seconds=duration,steps=5,errors=0,back_actions=0,face_detected=False,consent_given=False)
    db.session.add(interaction); db.session.flush()
    db.session.add(OperationRiskAssessment(session_id=interaction.id,score=risk['score'],level=risk['level'],reasons=risk['reasons']))
    source.balance=(source.balance-value).quantize(Decimal('0.01'))
    destination.balance=(destination.balance+value).quantize(Decimal('0.01'))
    suffix_source=source.number[-4:]; suffix_destination=destination.number[-4:]
    source_tx=Transaction(account_id=source.id,session_id=interaction.id,operation_type='TRANSFERENCIA',
                          description=f'Traslado a cuenta propia terminada en {suffix_destination}',amount=-value,
                          balance_after=source.balance,status='COMPLETADA',idempotency_key=idempotency_key)
    destination_tx=Transaction(account_id=destination.id,session_id=interaction.id,operation_type='INGRESO',
                               description=f'Traslado desde cuenta propia terminada en {suffix_source}',amount=value,
                               balance_after=destination.balance,status='COMPLETADA',idempotency_key=idempotency_key)
    interaction.ended_at=datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.add_all((source_tx,destination_tx)); db.session.flush()
    if capture:
        add_capture(user.id,source_tx.id,capture,**(capture_meta or {}))
    db.session.add(TransactionStep(session_id=interaction.id,event_type='completed',step_name='Traslado entre cuentas propias',
                                   metadata_json={'source_account_id':source.id,'destination_account_id':destination.id}))
    db.session.add(InteractionFeatures(session_id=interaction.id,duration_seconds=duration,steps=5,errors=0,back_actions=0,
                                       face_detection_rate=0.0,operation_type='TRANSFERENCIA'))
    db.session.add(MLResult(session_id=interaction.id,cluster_id=None,label='Pendiente de datos suficientes',method='K-Means exploratorio'))
    log(user,'INTERNAL_TRANSFER','OPERACIONES')
    _store_cluster_results()
    db.session.commit()
    return source_tx
