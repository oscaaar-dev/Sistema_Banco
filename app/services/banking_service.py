from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from ..models import db, Account, Beneficiary, Transaction, Transfer, InteractionSession, MLResult, InteractionFeatures, TransactionStep, AuditLog, FacialAnalysis, OperationRiskAssessment
from ..ai.interaction_model import cluster_interactions
from ..ai.operation_risk import assess_operation_risk

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

def process_operation(user,kind,amount,description,beneficiary_id=None,service=None,consent=False,face_detected=False,face_confidence=None,duration_seconds=None,idempotency_key=None):
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
    session=InteractionSession(user_id=user.id,operation_type=kind,started_at=start,consent_given=bool(consent),steps=5,errors=0,back_actions=0,face_detected=bool(face_detected),duration_seconds=session_duration)
    db.session.add(session);db.session.flush()
    db.session.add(OperationRiskAssessment(session_id=session.id,score=risk['score'],level=risk['level'],reasons=risk['reasons']))
    if face_detected:
        db.session.add(FacialAnalysis(session_id=session.id,face_detected=True,detection_confidence=face_confidence,model_name='OpenCV Haar Cascade'))
    TransactionStep(session_id=session.id,event_type='completed',step_name='Confirmación de operación',metadata_json={'operation_type':kind})
    account.balance=(account.balance-value).quantize(Decimal('0.01'))
    tx=Transaction(account_id=account.id,session_id=session.id,operation_type=kind,description=description[:180],amount=-value,balance_after=account.balance,status='COMPLETADA',idempotency_key=idempotency_key)
    session.ended_at=datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.add(tx)
    db.session.flush()
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
    db.session.add(InteractionFeatures(session_id=session.id,duration_seconds=session.duration_seconds,steps=session.steps,errors=session.errors,back_actions=session.back_actions,face_detection_rate=1.0 if face_detected else 0.0,operation_type=kind))
    db.session.add(MLResult(session_id=session.id,cluster_id=None,label='Pendiente de datos suficientes',method='K-Means exploratorio'))
    log(user,kind,'OPERACIONES')
    _store_cluster_results()
    db.session.commit()
    return tx
