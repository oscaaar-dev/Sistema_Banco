import os
import secrets
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent
load_dotenv(BASE_DIR/'.env')
APP_ENV=os.getenv('APP_ENV','development').strip().lower()
DATABASE_URL=os.getenv('DATABASE_URL') or os.getenv('MYSQL_URL')
SECRET_KEY=os.getenv('SECRET_KEY')
EMAIL_TRANSPORT=os.getenv('EMAIL_TRANSPORT','smtp').strip().lower()
LEGAL_NOTICE='Norte es un proyecto académico de demostración, independiente y sin relación con el BCP. Sus productos, saldos y operaciones son ficticios; no presta servicios financieros ni mueve dinero real.'
if APP_ENV=='production':
    required_production_settings=['SECRET_KEY','DATABASE_URL']
    if EMAIL_TRANSPORT=='brevo_api':
        required_production_settings.extend(('BREVO_API_KEY','SMTP_FROM'))
    else:
        required_production_settings.extend(('SMTP_HOST','SMTP_USERNAME','SMTP_PASSWORD','SMTP_FROM'))
    missing_production_settings=[name for name in required_production_settings if not os.getenv(name)]
    if missing_production_settings:
        raise RuntimeError('Faltan variables obligatorias de producción: '+', '.join(missing_production_settings))
class Config:
    APP_ENV=APP_ENV
    SECRET_KEY=SECRET_KEY or secrets.token_hex(32)
    SQLALCHEMY_DATABASE_URI=DATABASE_URL or 'postgresql+psycopg://banco_app:CAMBIA_ESTA_CLAVE@127.0.0.1:5432/banco_ia'
    SQLALCHEMY_TRACK_MODIFICATIONS=False
    if not DATABASE_URL:
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(BASE_DIR / 'instance' / 'norte_local.db').as_posix()}"
    elif SQLALCHEMY_DATABASE_URI.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI.replace('postgres://','postgresql+psycopg://',1)
    elif SQLALCHEMY_DATABASE_URI.startswith('postgresql://'):
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI.replace('postgresql://','postgresql+psycopg://',1)
    elif SQLALCHEMY_DATABASE_URI.startswith('mysql://'):
        SQLALCHEMY_DATABASE_URI=SQLALCHEMY_DATABASE_URI.replace('mysql://','mysql+pymysql://',1)
    SQLALCHEMY_ENGINE_OPTIONS={'pool_pre_ping':True,'pool_recycle':300}
    SESSION_COOKIE_HTTPONLY=True
    SESSION_COOKIE_SAMESITE='Lax'
    SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE',str(APP_ENV=='production').lower()).lower()=='true'
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30)
    SESSION_REFRESH_EACH_REQUEST=False
    WTF_CSRF_TIME_LIMIT=3600
    MAX_CONTENT_LENGTH=2*1024*1024
    SMTP_HOST=os.getenv('SMTP_HOST')
    SMTP_PORT=int(os.getenv('SMTP_PORT','587'))
    SMTP_USERNAME=os.getenv('SMTP_USERNAME')
    SMTP_PASSWORD=os.getenv('SMTP_PASSWORD')
    SMTP_FROM=os.getenv('SMTP_FROM')
    SMTP_USE_TLS=os.getenv('SMTP_USE_TLS','true').lower()=='true'
    SMTP_USE_SSL=os.getenv('SMTP_USE_SSL','false').lower()=='true'
    EMAIL_TRANSPORT=EMAIL_TRANSPORT
    LEGAL_NOTICE=LEGAL_NOTICE
    BREVO_API_KEY=os.getenv('BREVO_API_KEY')
    LOGIN_CODE_TTL_SECONDS=300
    LOGIN_CODE_RESEND_SECONDS=60
    LOGIN_CODE_MAX_ATTEMPTS=5
