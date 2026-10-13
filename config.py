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
_raw_secret_key=os.getenv('SECRET_KEY')
# Una clave vacía o de solo espacios cuenta como ausente. Un valor válido se conserva tal cual (sin recortarlo), para no
# cambiar la firma de las sesiones ya emitidas.
SECRET_KEY=_raw_secret_key if _raw_secret_key and _raw_secret_key.strip() else None
SECRET_KEY_IS_TEMPORARY=SECRET_KEY is None
SECRET_KEY_MIN_LENGTH=32
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
    missing_production_settings=[name for name in required_production_settings
                                 if (SECRET_KEY is None if name=='SECRET_KEY' else not os.getenv(name))]
    if missing_production_settings:
        raise RuntimeError('Faltan variables obligatorias de producción: '+', '.join(missing_production_settings))

def secret_key_warnings(secret_key, temporary):
    """Avisos sobre la clave de sesión. Nunca incluyen la clave ni su longitud exacta."""
    if temporary:
        return ['SECRET_KEY no está definida: se generó una clave temporal propia de este proceso. Con varios workers '
                '(por ejemplo, gunicorn --workers 2) cada uno tendría una clave distinta y las sesiones, el CSRF y la memoria '
                'de Nori no coincidirían entre ellos. Define una SECRET_KEY estable y compartida.']
    if len(str(secret_key))<SECRET_KEY_MIN_LENGTH:
        return [f'SECRET_KEY tiene menos de {SECRET_KEY_MIN_LENGTH} caracteres: se recomienda un valor aleatorio más largo.']
    return []

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
def _engine_options(database_uri):
    """Opciones del motor SQL. ``pool_recycle`` evita conexiones caducadas en MySQL/PostgreSQL; en SQLite no aplica y, con una
    base en memoria, descartaría la única conexión (y todas las tablas) a los 300 s, lo que rompía las suites largas."""
    options={'pool_pre_ping':True,'hide_parameters':True}
    if not str(database_uri).startswith('sqlite'):
        options['pool_recycle']=300
    return options


def _int_env(name, default, minimum=1):
    """Entero positivo desde una variable de entorno opcional; si falta o es inválido, el valor por defecto."""
    try:
        return max(minimum,int(os.getenv(name,'').strip() or default))
    except ValueError:
        return default


FRAUD_MODES=('off','shadow','enforce')

def _fraud_mode():
    """Modo del sistema de fraude. Cualquier valor desconocido se trata como «shadow» (observación): nunca activa medidas."""
    value=os.getenv('FRAUD_MODE','shadow').strip().lower()
    return value if value in FRAUD_MODES else 'shadow'


class Config:
    APP_ENV=APP_ENV
    SECRET_KEY=SECRET_KEY or secrets.token_hex(32)
    SECRET_KEY_IS_TEMPORARY=SECRET_KEY_IS_TEMPORARY   # solo para avisar en el arranque; la clave no se registra
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
    # hide_parameters evita que un error de SQL imprima parámetros (por ejemplo, los bytes de una foto) en los logs.
    SQLALCHEMY_ENGINE_OPTIONS=_engine_options(SQLALCHEMY_DATABASE_URI)
    SESSION_COOKIE_HTTPONLY=True
    SESSION_COOKIE_SAMESITE='Lax'
    SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE',str(APP_ENV=='production').lower()).lower()=='true'
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30)
    SESSION_REFRESH_EACH_REQUEST=False
    WTF_CSRF_TIME_LIMIT=3600
    MAX_CONTENT_LENGTH=2*1024*1024
    # Cámara obligatoria: una foto JPEG por operación, plazo de conservación y tamaño máximo.
    CAPTURE_RETENTION_DAYS=90
    CAPTURE_MAX_BYTES=300*1024
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
    # Búsqueda de clientes para agregar contactos: límite por usuario y por conexión para frenar la enumeración de DNI/cuentas.
    CONTACT_LOOKUP_RATE_LIMIT_USER=8
    CONTACT_LOOKUP_RATE_LIMIT_IP=20
    CONTACT_LOOKUP_RATE_LIMIT_WINDOW_SECONDS=600
    # Detección de fraude (ver docs/fraude.md). «shadow» solo registra; «enforce» aplica medidas automáticas y hay que
    # activarlo expresamente. Los umbrales son iniciales y NO están calibrados con datos reales etiquetados.
    FRAUD_MODE=_fraud_mode()
    FRAUD_RULES_VERSION='reglas-1.0'
    FRAUD_LEVEL_INTERMEDIO=25              # puntuación a partir de la cual el nivel es INTERMEDIO
    FRAUD_LEVEL_SOSPECHOSO=55              # ... y SOSPECHOSO (además exige al menos dos grupos de señales independientes)
    FRAUD_LEVEL_SOSPECHOSO_LOW_CONFIDENCE=65   # con historial insuficiente se exige más para llegar a SOSPECHOSO
    FRAUD_MIN_HISTORY=3                    # operaciones comparables mínimas para usar reglas basadas en el historial
    FRAUD_FAILSAFE_MAX_AMOUNT=500          # si la evaluación falla en modo enforce, solo continúan operaciones hasta este monto (S/)
    FRAUD_PREVENTIVE_ACCESS_MINUTES=30     # duración del bloqueo preventivo de acceso (siempre temporal)
    FRAUD_HOLD_TTL_HOURS=72                # vigencia de una retención y de su autorización
    FRAUD_MAX_SANCTION_DAYS=365            # una sanción administrativa nunca es permanente
    FRAUD_SANCTION_DAYS_BY_RECIDIVISM=(7,30,90)   # sugerencia según incidentes CONFIRMADOS previos (las alertas no cuentan)
    FRAUD_EVALUATION_RETENTION_DAYS=180
    FRAUD_MODEL_ENABLED=True               # inferencia del detector de anomalías (solo observación; sin artefacto no hace nada)
    FRAUD_MODEL_DIR=str(BASE_DIR/'instance'/'fraud_models')
    FRAUD_MODEL_MIN_SAMPLES=200
    INTERACTION_CLUSTER_MAX_SESSIONS=300   # K-Means exploratorio: solo las sesiones más recientes (antes se reprocesaban todas)
    # Nori (asistente de la web pública): sin servicios externos; límites por conexión (IP con hash).
    NORI_MAX_MESSAGE_CHARS=500
    NORI_RATE_LIMIT_MESSAGES=_int_env('NORI_RATE_LIMIT_MESSAGES',20)
    NORI_RATE_LIMIT_WINDOW_SECONDS=60
    NORI_DAILY_LIMIT=_int_env('NORI_DAILY_LIMIT',300)
    # Recuperación documental local (TF-IDF): umbral de relevancia, fuentes citadas y tamaños máximos.
    NORI_RAG_ENABLED=True
    NORI_RAG_MIN_SCORE=0.12
    NORI_RAG_STRONG_SCORE=0.20          # fragmento específico (2 o más términos) que gana a una regla débil
    NORI_RAG_OVERRIDE_SCORE=0.30        # con 3 o más términos en común, gana incluso a una regla fuerte
    NORI_RAG_SINGLE_TERM_SCORE=0.45     # con un solo término en común hace falta mucha más relevancia
    NORI_RAG_MAX_SOURCES=2
    NORI_RAG_EXCERPT_CHARS=380
    NORI_RAG_QUERY_CHARS=200
    NORI_PURGE_INTERVAL_SECONDS=60          # como mucho una purga de auditoría por minuto y proceso
    # Memoria temporal de la conversación (vive en el sessionStorage del navegador; el servidor no guarda nada).
    NORI_MEMORY_TURNS=8
    NORI_MEMORY_TTL_SECONDS=1800
    NORI_MAX_REQUEST_BYTES=32768
    PASSWORD_RESET_TTL_SECONDS=1800
    PASSWORD_RESET_RATE_LIMIT_IP=5
    PASSWORD_RESET_RATE_LIMIT_USER=3
    PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS=3600
    # Base pública (https://dominio) para construir el enlace del correo. Evita que un encabezado Host manipulado
    # envenene el enlace. En producción es obligatoria para enviar correos de recuperación.
    PUBLIC_BASE_URL=_public_base_url()
    REGISTRATION_RATE_LIMIT_IP=5
    REGISTRATION_RATE_LIMIT_WINDOW_SECONDS=3600
