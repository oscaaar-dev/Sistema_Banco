import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlparse
from datetime import timedelta
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent
load_dotenv(BASE_DIR/'.env')
APP_ENV=os.getenv('APP_ENV','development').strip().lower()
DATABASE_URL=os.getenv('DATABASE_URL') or os.getenv('MYSQL_URL')
SECRET_KEY=os.getenv('SECRET_KEY')
EMAIL_TRANSPORT=os.getenv('EMAIL_TRANSPORT','smtp').strip().lower()
BREVO_SANDBOX=os.getenv('BREVO_SANDBOX','false').strip().lower()=='true'
RAILWAY_ENVIRONMENT_NAME=os.getenv('RAILWAY_ENVIRONMENT_NAME','').strip().lower()
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

def _https_url(value):
    """Acepta solo enlaces https con dominio; evita esquemas como javascript: en enlaces públicos."""
    value=(value or '').strip()
    parsed=urlparse(value)
    return value if parsed.scheme=='https' and parsed.netloc else ''

def _contact_channels():
    """Canales públicos de contacto (cabecera y pie). Los que no estén configurados o sean inválidos no se muestran."""
    channels=[]
    instagram=_https_url(os.getenv('NORTE_INSTAGRAM_URL'))
    if instagram:
        channels.append({'key':'instagram','label':'Instagram','aria':'Instagram de Norte','url':instagram,
                         'text':os.getenv('NORTE_INSTAGRAM_HANDLE','').strip() or 'Instagram'})
    whatsapp=re.sub(r'\D','',os.getenv('NORTE_WHATSAPP_NUMBER',''))
    if 8<=len(whatsapp)<=15:
        channels.append({'key':'whatsapp','label':'WhatsApp','aria':'WhatsApp de Norte','url':f'https://wa.me/{whatsapp}',
                         'text':os.getenv('NORTE_WHATSAPP_DISPLAY','').strip() or '+'+whatsapp})
    facebook=_https_url(os.getenv('NORTE_FACEBOOK_URL'))
    if facebook:
        channels.append({'key':'facebook','label':'Facebook','aria':'Facebook de Norte','url':facebook,
                         'text':os.getenv('NORTE_FACEBOOK_NAME','').strip() or 'Facebook'})
    return channels

def _public_base_url():
    value=os.getenv('PUBLIC_BASE_URL','').strip().rstrip('/')
    parsed=urlparse(value)
    local=parsed.hostname in ('localhost','127.0.0.1','::1')
    if parsed.scheme=='https' and parsed.netloc or (parsed.scheme=='http' and local):
        return value
    return ''

def _contact_email():
    value=os.getenv('NORTE_CONTACT_EMAIL','').strip()
    return value if re.fullmatch(r'[^@\s<>"]+@[^@\s<>"]+\.[^@\s<>"]+',value) else ''
class Config:
    APP_ENV=APP_ENV
    SECRET_KEY=SECRET_KEY or secrets.token_hex(32)
    SQLALCHEMY_TRACK_MODIFICATIONS=False
    # Sin DATABASE_URL se usa SQLite local dentro de instance/; con URL se normaliza el driver.
    if not DATABASE_URL:
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{(BASE_DIR / 'instance' / 'norte_local.db').as_posix()}"
    elif DATABASE_URL.startswith('postgres://'):
        SQLALCHEMY_DATABASE_URI=DATABASE_URL.replace('postgres://','postgresql+psycopg://',1)
    elif DATABASE_URL.startswith('postgresql://'):
        SQLALCHEMY_DATABASE_URI=DATABASE_URL.replace('postgresql://','postgresql+psycopg://',1)
    elif DATABASE_URL.startswith('mysql://'):
        SQLALCHEMY_DATABASE_URI=DATABASE_URL.replace('mysql://','mysql+pymysql://',1)
    else:
        SQLALCHEMY_DATABASE_URI=DATABASE_URL
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
    BREVO_SANDBOX=BREVO_SANDBOX
    RAILWAY_ENVIRONMENT_NAME=RAILWAY_ENVIRONMENT_NAME
    LEGAL_NOTICE=LEGAL_NOTICE
    CONTACT_CHANNELS=_contact_channels()
    CONTACT_EMAIL=_contact_email()
    CONTACT_HOURS=os.getenv('NORTE_CONTACT_HOURS','').strip() or 'Lunes a viernes · 9:00 a 18:00 (horario de demostración)'
    BREVO_API_KEY=os.getenv('BREVO_API_KEY')
    LOGIN_CODE_TTL_SECONDS=300
    LOGIN_CODE_RESEND_SECONDS=60
    LOGIN_CODE_MAX_ATTEMPTS=5
    LOGIN_CODE_RATE_LIMIT_IP=5
    LOGIN_CODE_RATE_LIMIT_USER=3
    LOGIN_CODE_RATE_LIMIT_WINDOW_SECONDS=900
    PASSWORD_RESET_TTL_SECONDS=1800
    PASSWORD_RESET_RATE_LIMIT_IP=5
    PASSWORD_RESET_RATE_LIMIT_USER=3
    PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS=3600
    # Base pública (https://dominio) para construir el enlace del correo. Evita que un encabezado Host manipulado
    # envenene el enlace. En producción es obligatoria para enviar correos de recuperación.
    PUBLIC_BASE_URL=_public_base_url()
    REGISTRATION_RATE_LIMIT_IP=5
    REGISTRATION_RATE_LIMIT_WINDOW_SECONDS=3600
