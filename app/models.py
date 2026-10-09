from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
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
    accounts=db.relationship('Account',backref='owner',lazy=True,cascade='all, delete-orphan')
    beneficiaries=db.relationship('Beneficiary',backref='owner',lazy=True,cascade='all, delete-orphan')
    cards=db.relationship('BankCard',backref='holder',lazy=True,cascade='all, delete-orphan')
    def set_password(self,password): self.password_hash=generate_password_hash(password)
    def check_password(self,password): return check_password_hash(self.password_hash,password)

class LoginChallenge(db.Model):
    __tablename__='login_challenges'
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey('users.id',ondelete='CASCADE'),nullable=False,unique=True,index=True)
    code_hash=db.Column(db.String(64),nullable=False)
    expires_at=db.Column(db.DateTime,nullable=False,index=True)
    last_sent_at=db.Column(db.DateTime,nullable=False)
    attempts=db.Column(db.Integer,nullable=False,default=0)
    user=db.relationship('User',backref=db.backref('login_challenge',uselist=False,cascade='all, delete-orphan'))

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
