from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import deferred
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()
def now_utc(): return datetime.now(timezone.utc).replace(tzinfo=None)

class User(UserMixin, db.Model):
    __tablename__='users'
    __table_args__=(
        db.Index('uq_users_email','email',unique=True),
        db.Index('uq_users_dni','dni',unique=True),
    )
    id=db.Column(db.Integer,primary_key=True)
    username=db.Column(db.String(40),unique=True,nullable=False,index=True)
    full_name=db.Column(db.String(100),nullable=False)
    email=db.Column(db.String(254),nullable=True)
    dni=db.Column(db.String(8),nullable=True)
    phone=db.Column(db.String(9),nullable=True)
    is_seed=db.Column(db.Boolean,nullable=False,default=False)
    password_hash=db.Column(db.String(255),nullable=False)
    role=db.Column(db.String(20),nullable=False,default='CLIENTE')
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    password_changed_at=db.Column(db.DateTime,nullable=True)
    sessions_revoked_at=db.Column(db.DateTime,nullable=True)
    # Estado de la cuenta: ACTIVA (por defecto: todos los usuarios existentes), PENDIENTE, SUSPENDIDA o DESACTIVADA.
    # Una cuenta que no está ACTIVA no puede iniciar sesión ni conservar sesiones abiertas.
    account_status=db.Column(db.String(12),nullable=False,default='ACTIVA',server_default='ACTIVA')
    accounts=db.relationship('Account',backref='owner',lazy=True,cascade='all, delete-orphan')
    beneficiaries=db.relationship('Beneficiary',backref='owner',lazy=True,cascade='all, delete-orphan')
    cards=db.relationship('BankCard',backref='holder',lazy=True,cascade='all, delete-orphan')
    def set_password(self,password): self.password_hash=generate_password_hash(password)
    def check_password(self,password): return check_password_hash(self.password_hash,password)
    @property
    def is_active(self):
        """Flask-Login: una cuenta suspendida, desactivada o pendiente no puede iniciar sesión."""
        return self.account_status=='ACTIVA'
    def session_stamp(self):
        """Marca de las credenciales vigentes; si cambia, las sesiones anteriores dejan de ser válidas.

        Una revocación de sesiones por seguridad (``sessions_revoked_at``) añade un sufijo; mientras no exista, el sello es
        idéntico al anterior y las sesiones ya emitidas siguen siendo válidas."""
        stamp=self.password_changed_at.isoformat() if self.password_changed_at else '0'
        if self.sessions_revoked_at:
            stamp+='|r'+self.sessions_revoked_at.isoformat()
        return stamp

class LoginChallenge(db.Model):
    __tablename__='login_challenges'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,unique=True,index=True)
    code_hash=db.Column(db.String(64),nullable=False)
    expires_at=db.Column(db.DateTime,nullable=False,index=True)
    last_sent_at=db.Column(db.DateTime,nullable=False)
    attempts=db.Column(db.Integer,nullable=False,default=0)
    user=db.relationship('User',backref=db.backref('login_challenge',uselist=False,cascade='all, delete-orphan'))

class PasswordResetToken(db.Model):
    """Solicitud de recuperación de contraseña de un cliente. Solo se guarda el hash del token."""
    __tablename__='password_reset_tokens'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    token_hash=db.Column(db.String(64),nullable=False,unique=True)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    expires_at=db.Column(db.DateTime,nullable=False,index=True)
    used_at=db.Column(db.DateTime,nullable=True)
    user=db.relationship('User',backref=db.backref('password_reset_tokens',cascade='all, delete-orphan',lazy=True))

class LoginAttemptLimit(db.Model):
    __tablename__='login_attempt_limits'
    id=db.Column(db.Integer,primary_key=True)
    identifier_hash=db.Column(db.String(64),nullable=False,unique=True,index=True)
    failure_count=db.Column(db.Integer,nullable=False,default=1)
    window_started_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    locked_until=db.Column(db.DateTime,nullable=True)

class OperationChallenge(db.Model):
    __tablename__='operation_challenges'
    __table_args__=(db.UniqueConstraint('user_id','operation_type',name='uq_operation_challenge_user_type'),)
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    operation_type=db.Column(db.String(30),nullable=False)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id',ondelete='CASCADE'),nullable=False)
    beneficiary_id=db.Column(db.Integer,db.ForeignKey('beneficiaries.id',ondelete='CASCADE'),nullable=True)
    amount=db.Column(db.Numeric(12,2),nullable=False)
    idempotency_key=db.Column(db.String(64),nullable=False)
    details_hash=db.Column(db.String(64),nullable=True)
    code_hash=db.Column(db.String(64),nullable=False)
    expires_at=db.Column(db.DateTime,nullable=False,index=True)
    last_sent_at=db.Column(db.DateTime,nullable=False)
    attempts=db.Column(db.Integer,nullable=False,default=0)
    consumed_at=db.Column(db.DateTime,nullable=True)

class Account(db.Model):
    __tablename__='accounts'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    account_type=db.Column(db.String(40),nullable=False,default='Ahorros')
    number=db.Column(db.String(24),unique=True,nullable=False)
    currency=db.Column(db.String(3),nullable=False,default='PEN')
    balance=db.Column(db.Numeric(12,2),nullable=False,default=0)
    status=db.Column(db.String(20),nullable=False,default='ACTIVA')
    cci=db.Column(db.String(20),nullable=True,unique=True)
    is_seed=db.Column(db.Boolean,nullable=False,default=False)

class Beneficiary(db.Model):
    __tablename__='beneficiaries'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    name=db.Column(db.String(100),nullable=False)
    account_number=db.Column(db.String(24),nullable=False)
    bank_name=db.Column(db.String(80),nullable=False,default='Norte')
    is_seed=db.Column(db.Boolean,nullable=False,default=False)

class BankCard(db.Model):
    __tablename__='cards'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id',ondelete='CASCADE'),nullable=False)
    last4=db.Column(db.String(4),nullable=False)
    card_type=db.Column(db.String(30),nullable=False,default='Débito')
    status=db.Column(db.String(20),nullable=False,default='Activa')
    limit_amount=db.Column(db.Numeric(12,2),nullable=False,default=3000)
    is_seed=db.Column(db.Boolean,nullable=False,default=False)
    account=db.relationship('Account')

class InteractionSession(db.Model):
    __tablename__='sessions'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    operation_type=db.Column(db.String(30),nullable=False)
    started_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    ended_at=db.Column(db.DateTime)
    duration_seconds=db.Column(db.Integer,nullable=False,default=0)
    steps=db.Column(db.Integer,nullable=False,default=0)
    errors=db.Column(db.Integer,nullable=False,default=0)
    back_actions=db.Column(db.Integer,nullable=False,default=0)
    face_detected=db.Column(db.Boolean,nullable=False,default=False)
    consent_given=db.Column(db.Boolean,nullable=False,default=False)
    user=db.relationship('User')
    transaction_steps=db.relationship('TransactionStep',backref='session',lazy=True,cascade='all, delete-orphan')
    facial_analysis=db.relationship('FacialAnalysis',backref='session',uselist=False,cascade='all, delete-orphan')
    interaction_features=db.relationship('InteractionFeatures',backref='session',uselist=False,cascade='all, delete-orphan')
    ml_result=db.relationship('MLResult',backref='session',uselist=False,cascade='all, delete-orphan')
    risk_assessment=db.relationship('OperationRiskAssessment',backref='session',uselist=False,cascade='all, delete-orphan')

class Transaction(db.Model):
    __tablename__='transactions'
    __table_args__=(db.UniqueConstraint('account_id','idempotency_key',name='uq_transactions_account_idempotency'),)
    id=db.Column(db.Integer,primary_key=True)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id'),nullable=False,index=True)
    session_id=db.Column(db.Integer,db.ForeignKey('sessions.id'),nullable=True,index=True)
    operation_type=db.Column(db.String(30),nullable=False)
    description=db.Column(db.String(180),nullable=False)
    amount=db.Column(db.Numeric(12,2),nullable=False)
    balance_after=db.Column(db.Numeric(12,2),nullable=False)
    status=db.Column(db.String(20),nullable=False,default='COMPLETADA')
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    is_seed=db.Column(db.Boolean,nullable=False,default=False)
    idempotency_key=db.Column(db.String(64),nullable=True)
    account=db.relationship('Account',backref=db.backref('transactions',lazy=True))
    session=db.relationship('InteractionSession',backref=db.backref('transactions',lazy=True))

class Transfer(db.Model):
    """Internal record of a transfer flow; it never settles outside Norte."""
    __tablename__='transfers'
    id=db.Column(db.Integer,primary_key=True)
    transaction_id=db.Column(db.Integer,db.ForeignKey('transactions.id',ondelete='CASCADE'),nullable=False,unique=True)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id'),nullable=False,index=True)
    beneficiary_id=db.Column(db.Integer,db.ForeignKey('beneficiaries.id'),nullable=False,index=True)
    amount=db.Column(db.Numeric(12,2),nullable=False)
    currency=db.Column(db.String(3),nullable=False,default='PEN')
    status=db.Column(db.String(30),nullable=False,default='REGISTRADA')
    is_seed=db.Column(db.Boolean,nullable=False,default=False)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    transaction=db.relationship('Transaction',backref=db.backref('transfer',uselist=False))
    account=db.relationship('Account')
    beneficiary=db.relationship('Beneficiary')

class OperationCapture(db.Model):
    """Foto tomada al confirmar una operación (cámara obligatoria).

    Se guarda ligada al usuario y a la operación; la imagen es ``deferred`` para que listar capturas no cargue los bytes.
    Solo se lee desde un endpoint autenticado de administración y se borra al vencer el plazo de conservación.
    """
    __tablename__='operation_captures'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    transaction_id=db.Column(db.Integer,db.ForeignKey('transactions.id',ondelete='CASCADE'),nullable=False,index=True)
    image=deferred(db.Column(db.LargeBinary,nullable=False))
    mime_type=db.Column(db.String(30),nullable=False,default='image/jpeg')
    size_bytes=db.Column(db.Integer,nullable=False)
    width=db.Column(db.Integer,nullable=True)
    height=db.Column(db.Integer,nullable=True)
    consent_version=db.Column(db.String(20),nullable=True)
    ip_hash=db.Column(db.String(64),nullable=True)
    user_agent=db.Column(db.String(255),nullable=True)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    user=db.relationship('User',backref=db.backref('operation_captures',lazy=True,passive_deletes=True))
    transaction=db.relationship('Transaction',backref=db.backref('capture',uselist=False,lazy=True,passive_deletes=True))

class TransactionStep(db.Model):
    __tablename__='transaction_steps'
    id=db.Column(db.Integer,primary_key=True)
    session_id=db.Column(db.Integer,db.ForeignKey('sessions.id',ondelete='CASCADE'),nullable=False,index=True)
    event_type=db.Column(db.String(40),nullable=False)
    step_name=db.Column(db.String(80),nullable=False)
    occurred_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    metadata_json=db.Column(db.JSON,nullable=False,default=dict)

class FacialAnalysis(db.Model):
    __tablename__='facial_analysis'
    id=db.Column(db.Integer,primary_key=True)
    session_id=db.Column(db.Integer,db.ForeignKey('sessions.id',ondelete='CASCADE'),unique=True,nullable=False)
    face_detected=db.Column(db.Boolean,nullable=False,default=False)
    detection_confidence=db.Column(db.Float)
    model_name=db.Column(db.String(100),nullable=False,default='OpenCV Haar Cascade')
    processed_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    # No image bytes, face embeddings, identity, nor emotion diagnosis are persisted.

class InteractionFeatures(db.Model):
    __tablename__='interaction_features'
    id=db.Column(db.Integer,primary_key=True)
    session_id=db.Column(db.Integer,db.ForeignKey('sessions.id',ondelete='CASCADE'),unique=True,nullable=False)
    duration_seconds=db.Column(db.Float,nullable=False,default=0)
    steps=db.Column(db.Integer,nullable=False,default=0)
    errors=db.Column(db.Integer,nullable=False,default=0)
    back_actions=db.Column(db.Integer,nullable=False,default=0)
    face_detection_rate=db.Column(db.Float,nullable=False,default=0)
    dl_confidence=db.Column(db.Float)
    expression_variation=db.Column(db.Float)
    operation_type=db.Column(db.String(30),nullable=False)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)

class MLResult(db.Model):
    __tablename__='ml_results'
    id=db.Column(db.Integer,primary_key=True)
    session_id=db.Column(db.Integer,db.ForeignKey('sessions.id',ondelete='CASCADE'),unique=True,nullable=False)
    cluster_id=db.Column(db.Integer)
    label=db.Column(db.String(100),nullable=False)
    method=db.Column(db.String(100),nullable=False)
    model_version=db.Column(db.String(40),nullable=False,default='1.0')
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)

class OperationRiskAssessment(db.Model):
    __tablename__='operation_risk_assessments'
    id=db.Column(db.Integer,primary_key=True)
    session_id=db.Column(db.Integer,db.ForeignKey('sessions.id',ondelete='CASCADE'),unique=True,nullable=False)
    score=db.Column(db.Integer)
    level=db.Column(db.String(40),nullable=False)
    reasons=db.Column(db.JSON,nullable=False,default=list)
    method=db.Column(db.String(80),nullable=False,default='Reglas transparentes (no probabilidad)')
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)

class RiskEvaluation(db.Model):
    """Resultado de evaluar una operación o un evento de acceso. Es una **evaluación**, no un fraude ni una sanción.

    ``level`` toma NORMAL, INTERMEDIO o SOSPECHOSO (o NO_EVALUADO si la evaluación falló). El motor nunca asigna FRAUDE: ese
    nivel solo existe en un incidente confirmado por un administrador.
    """
    __tablename__='risk_evaluations'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True,index=True)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id',ondelete='SET NULL'),nullable=True)
    transaction_id=db.Column(db.Integer,db.ForeignKey('transactions.id',ondelete='SET NULL'),nullable=True)
    context=db.Column(db.String(20),nullable=False)           # TRANSFERENCIA, PAGO, RECARGA, TRASLADO_PROPIO o ACCESO
    event=db.Column(db.String(30),nullable=False)             # OPERACION, LOGIN_FAILURE, LOGIN_OTP_FAILURE, LOGIN_SUCCESS
    level=db.Column(db.String(14),nullable=False,index=True)
    score=db.Column(db.Integer,nullable=False,default=0)
    confidence=db.Column(db.String(12),nullable=False,default='SUFICIENTE')
    signals=db.Column(db.JSON,nullable=False,default=list)
    rules_version=db.Column(db.String(30),nullable=False)
    model_version=db.Column(db.String(60),nullable=True)
    model_score=db.Column(db.Float,nullable=True)
    mode=db.Column(db.String(10),nullable=False)
    recommended_action=db.Column(db.String(40),nullable=False)
    applied_action=db.Column(db.String(40),nullable=False)
    status=db.Column(db.String(10),nullable=False,default='OK')
    error_code=db.Column(db.String(60),nullable=True)
    duration_ms=db.Column(db.Float,nullable=True)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    user=db.relationship('User')

class FraudIncident(db.Model):
    """Incidente investigable. ``status`` distingue abierto, en investigación, descartado y fraude confirmado."""
    __tablename__='fraud_incidents'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True,index=True)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id',ondelete='SET NULL'),nullable=True)
    evaluation_id=db.Column(db.Integer,db.ForeignKey('risk_evaluations.id',ondelete='SET NULL'),nullable=True)
    origin=db.Column(db.String(12),nullable=False,default='AUTOMATICO')    # AUTOMATICO o MANUAL
    category=db.Column(db.String(14),nullable=False)                       # TRANSACCIONAL o ACCESO
    status=db.Column(db.String(16),nullable=False,default='ABIERTO',index=True)
    level=db.Column(db.String(14),nullable=False,default='SOSPECHOSO')     # SOSPECHOSO o FRAUDE (solo si fue confirmado)
    title=db.Column(db.String(200),nullable=False)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    updated_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    confirmed_at=db.Column(db.DateTime,nullable=True)
    confirmed_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    confirmation_reason=db.Column(db.String(500),nullable=True)
    closed_at=db.Column(db.DateTime,nullable=True)
    closed_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    closure_reason=db.Column(db.String(500),nullable=True)
    user=db.relationship('User',foreign_keys=[user_id])
    evaluation=db.relationship('RiskEvaluation')
    events=db.relationship('IncidentEvent',backref='incident',lazy=True,cascade='all, delete-orphan',order_by='IncidentEvent.id')

class IncidentEvent(db.Model):
    """Historial inmutable de un incidente (quién hizo qué y cuándo). ``actor_id`` vacío significa «el sistema»."""
    __tablename__='incident_events'
    id=db.Column(db.Integer,primary_key=True)
    incident_id=db.Column(db.Integer,db.ForeignKey('fraud_incidents.id',ondelete='CASCADE'),nullable=False,index=True)
    actor_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    event_type=db.Column(db.String(40),nullable=False)
    detail=db.Column(db.String(500),nullable=False,default='')
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    actor=db.relationship('User',foreign_keys=[actor_id])

class SecurityRestriction(db.Model):
    """Restricción con alcance y duración definidos. Nunca es permanente: ``expires_at`` es obligatorio."""
    __tablename__='security_restrictions'
    __table_args__=(db.Index('ix_security_restrictions_active','user_id','status','expires_at'),)
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False)
    incident_id=db.Column(db.Integer,db.ForeignKey('fraud_incidents.id',ondelete='SET NULL'),nullable=True)
    scope=db.Column(db.String(14),nullable=False)                          # OPERACIONES o ACCESO
    origin=db.Column(db.String(12),nullable=False)                         # PREVENTIVA (automática) o SANCION (administrativa)
    status=db.Column(db.String(10),nullable=False,default='ACTIVA')       # ACTIVA, LEVANTADA o EXPIRADA
    reason=db.Column(db.String(300),nullable=False)
    starts_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    expires_at=db.Column(db.DateTime,nullable=False)
    created_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    lifted_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    lifted_at=db.Column(db.DateTime,nullable=True)
    lift_reason=db.Column(db.String(300),nullable=True)
    rules_version=db.Column(db.String(30),nullable=True)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    user=db.relationship('User',foreign_keys=[user_id])
    created_by=db.relationship('User',foreign_keys=[created_by_id])
    lifted_by=db.relationship('User',foreign_keys=[lifted_by_id])

class HeldOperation(db.Model):
    """Operación retenida **antes** de ejecutarse: no modifica saldos ni crea movimientos.

    Si un administrador la autoriza, el cliente puede volver a confirmarla (con todas sus validaciones, OTP y cámara) y la
    autorización se consume una sola vez.
    """
    __tablename__='held_operations'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    account_id=db.Column(db.Integer,db.ForeignKey('accounts.id',ondelete='CASCADE'),nullable=False)
    evaluation_id=db.Column(db.Integer,db.ForeignKey('risk_evaluations.id',ondelete='SET NULL'),nullable=True)
    incident_id=db.Column(db.Integer,db.ForeignKey('fraud_incidents.id',ondelete='SET NULL'),nullable=True)
    kind=db.Column(db.String(20),nullable=False)
    amount=db.Column(db.Numeric(12,2),nullable=False)
    fingerprint=db.Column(db.String(64),nullable=False,index=True)         # HMAC de los datos de la operación (no la identifica fuera del sistema)
    reason=db.Column(db.String(300),nullable=False)
    status=db.Column(db.String(12),nullable=False,default='RETENIDA',index=True)   # RETENIDA, AUTORIZADA, RECHAZADA, EJECUTADA o EXPIRADA
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    expires_at=db.Column(db.DateTime,nullable=False)
    reviewed_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    reviewed_at=db.Column(db.DateTime,nullable=True)
    review_reason=db.Column(db.String(300),nullable=True)
    transaction_id=db.Column(db.Integer,db.ForeignKey('transactions.id',ondelete='SET NULL'),nullable=True)
    user=db.relationship('User',foreign_keys=[user_id])
    reviewed_by=db.relationship('User',foreign_keys=[reviewed_by_id])

class FraudModelVersion(db.Model):
    """Registro de cada versión del detector de anomalías: origen, datos usados, métricas y estado.

    Un entrenamiento siempre crea un ``CANDIDATO``. Pasa a ``ACTIVO`` solo con una activación explícita y validada; el CSV de
    entrenamiento no se guarda aquí (solo su huella SHA-256 y los conteos), y nunca sustituye al modelo activo por sí mismo.
    """
    __tablename__='fraud_model_versions'
    id=db.Column(db.Integer,primary_key=True)
    version=db.Column(db.String(60),nullable=False,unique=True)
    algorithm=db.Column(db.String(40),nullable=False,default='IsolationForest')
    source=db.Column(db.String(20),nullable=False)                       # CSV o BASE_DE_DATOS
    status=db.Column(db.String(12),nullable=False,default='CANDIDATO',index=True)   # CANDIDATO, ACTIVO, ARCHIVADO o RECHAZADO
    trained_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    n_train=db.Column(db.Integer,nullable=False,default=0)
    n_eval=db.Column(db.Integer,nullable=False,default=0)
    features=db.Column(db.JSON,nullable=False,default=list)
    metrics=db.Column(db.JSON,nullable=False,default=dict)
    validation=db.Column(db.JSON,nullable=False,default=dict)             # informe de validación de los datos de entrada
    data_sha256=db.Column(db.String(64),nullable=True)
    sklearn_version=db.Column(db.String(20),nullable=False)
    python_version=db.Column(db.String(10),nullable=False)
    created_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    activated_at=db.Column(db.DateTime,nullable=True)
    activated_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    notes=db.Column(db.String(300),nullable=True)

class Report(db.Model):
    __tablename__='reports'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'))
    report_type=db.Column(db.String(40),nullable=False)
    title=db.Column(db.String(140),nullable=False)
    data_json=db.Column(db.JSON,nullable=False,default=dict)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    user=db.relationship('User')

class AuditLog(db.Model):
    __tablename__='audit_logs'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True,index=True)
    action=db.Column(db.String(80),nullable=False)
    module=db.Column(db.String(50),nullable=False)
    result=db.Column(db.String(30),nullable=False)
    ip_local=db.Column(db.String(64))
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    user=db.relationship('User')

class AdminPermission(db.Model):
    """Permiso administrativo concedido a una cuenta ADMINISTRADOR. Denegar por defecto: sin fila no hay permiso.

    ``ADMIN_GENERAL`` no necesita filas: tiene todos los permisos por definición del rol."""
    __tablename__='admin_permissions'
    __table_args__=(db.UniqueConstraint('user_id','permission',name='uq_admin_permissions_user_permission'),)
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    permission=db.Column(db.String(40),nullable=False)
    granted_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    granted_at=db.Column(db.DateTime,nullable=False,default=now_utc)

class AdminChangeLog(db.Model):
    """Historial de cambios administrativos (altas, permisos, estados, sesiones). Solo se inserta: ninguna ruta lo modifica ni lo borra.

    ``detail`` nunca contiene contraseñas, tokens ni códigos."""
    __tablename__='admin_change_log'
    id=db.Column(db.Integer,primary_key=True)
    actor_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True,index=True)
    target_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True,index=True)
    action=db.Column(db.String(40),nullable=False)
    detail=db.Column(db.JSON,nullable=False,default=dict)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc,index=True)
    actor=db.relationship('User',foreign_keys=[actor_id])
    target=db.relationship('User',foreign_keys=[target_id])

class AdminActivationToken(db.Model):
    """Enlace de un solo uso para que una cuenta administrativa nueva defina su propia contraseña. Solo se guarda el HMAC del token."""
    __tablename__='admin_activation_tokens'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,index=True)
    token_hash=db.Column(db.String(64),nullable=False,unique=True)
    created_by_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='SET NULL'),nullable=True)
    auto_activate=db.Column(db.Boolean,nullable=False,default=False)
    created_at=db.Column(db.DateTime,nullable=False,default=now_utc)
    expires_at=db.Column(db.DateTime,nullable=False,index=True)
    used_at=db.Column(db.DateTime,nullable=True)
