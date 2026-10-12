"""Seguridad de Nori: normalización de la entrada, detección de datos sensibles y límite de mensajes.

Nada de lo que escribe la persona se guarda en el servidor: el límite de mensajes solo anota una fila de auditoría
(acción, IP con hash y hora), sin el texto de la conversación.

Retención de esas filas: se consideran vencidas a las 24 horas, pero **no hay una tarea programada**: se borran cuando Nori
recibe una solicitud (mensaje o apertura del chat) o al ejecutar ``flask --app run purge-nori-audit``. Por eso el plazo real
es «24 horas, o hasta la siguiente solicitud a Nori si nadie la usa».
"""
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta

from flask import current_app

from ..models import AuditLog, db, now_utc

AUDIT_ACTION='NORI_CHAT'
AUDIT_RETENTION=timedelta(days=1)
_CONTROL_CHARACTERS=re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_DIGIT_SEPARATORS=re.compile(r'(?<=\d)[ .-](?=\d)')
_LONG_NUMBER=re.compile(r'\d{6,}')
_SECRET_DECLARATION=re.compile(r'\b(contrasena|clave|pin|cvv|cvc|otp|codigo|token)\s+(es|era|sera|son)\b')
_SECRET_ASSIGNMENT=re.compile(r'\b(contraseña|contrasena|clave|pin|cvv|cvc|otp|código|codigo|token)\s*[:=]', re.I)

SENSITIVE_REPLY=('Por tu seguridad no escribas aquí DNI, números de tarjeta, contraseñas ni códigos. '
                 'No los necesito y no debes compartirlos con nadie. Oculté tu mensaje.')

# Respaldo en memoria (por proceso): sigue limitando aunque la base de datos no responda.
_memory_hits=defaultdict(deque)
_last_purge=0.0          # instante (monotónico) de la última purga de este proceso; solo se usa para no purgar en cada solicitud


def purge_expired_audit(force=False):
    """Borra las filas de auditoría de Nori con más de 24 horas y devuelve cuántas eliminó.

    Se llama desde el chat y desde la entrega del token. Para no escribir en cada solicitud, un mismo proceso purga como
    mucho una vez cada ``NORI_PURGE_INTERVAL_SECONDS`` (``force=True`` lo ignora). No toca filas de otras acciones.
    """
    global _last_purge
    now_tick=time.monotonic()
    interval=current_app.config['NORI_PURGE_INTERVAL_SECONDS']
    if not force and _last_purge and now_tick-_last_purge<interval:
        return 0
    try:
        removed=(AuditLog.query.filter(AuditLog.action==AUDIT_ACTION,AuditLog.created_at<now_utc()-AUDIT_RETENTION)
                 .delete(synchronize_session=False))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Nori: no se pudo purgar la auditoría vencida.')
        return 0
    _last_purge=now_tick
    return removed


def clean_message(raw,limit):
    """Texto de una línea, sin caracteres de control ni espacios repetidos y recortado a ``limit``."""
    text=_CONTROL_CHARACTERS.sub(' ',str(raw))
    return ' '.join(text.split())[:limit]


def normalize(text):
    """Minúsculas, sin tildes ni signos: sirve para comparar palabras clave."""
    decomposed=unicodedata.normalize('NFD',str(text).lower())
    stripped=''.join(character for character in decomposed if unicodedata.category(character)!='Mn')
    return ' '.join(re.sub(r'[^a-z0-9 ]+',' ',stripped).split())


def contains_sensitive_data(text):
    """DNI, teléfonos, tarjetas y códigos (6 o más dígitos seguidos) o frases que declaran una clave o un código."""
    joined=_DIGIT_SEPARATORS.sub('',str(text))
    if _LONG_NUMBER.search(joined):
        return True
    return bool(_SECRET_ASSIGNMENT.search(text) or _SECRET_DECLARATION.search(normalize(text)))


_MEMORY_KEYS_LIMIT=2048  # tope de conexiones distintas que recuerda el respaldo; al superarlo se descartan las inactivas


def _memory_limited(key,limit,window):
    now=time.monotonic()
    if len(_memory_hits)>_MEMORY_KEYS_LIMIT:
        for stale in [k for k,v in _memory_hits.items() if not v or now-v[-1]>window]:
            del _memory_hits[stale]
    hits=_memory_hits[key]
    while hits and now-hits[0]>window:
        hits.popleft()
    if len(hits)>=limit:
        return True
    hits.append(now)
    return False


def register_request_and_check_limit(ip_hash):
    """Anota la solicitud y devuelve ``True`` si esta conexión superó el límite por minuto o diario."""
    config=current_app.config
    window=config['NORI_RATE_LIMIT_WINDOW_SECONDS']
    per_window=config['NORI_RATE_LIMIT_MESSAGES']
    per_day=config['NORI_DAILY_LIMIT']
    if _memory_limited(ip_hash,per_window,window):
        return True
    purge_expired_audit()
    try:
        now=now_utc()
        base=AuditLog.query.filter_by(action=AUDIT_ACTION,ip_local=ip_hash)
        if base.filter(AuditLog.created_at>=now-timedelta(seconds=window)).count()>=per_window:
            return True
        if base.filter(AuditLog.created_at>=now-AUDIT_RETENTION).count()>=per_day:
            return True
        db.session.add(AuditLog(user_id=None,action=AUDIT_ACTION,module='NORI',result='ATENDIDA',ip_local=ip_hash,created_at=now))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Nori: no se pudo registrar el control de solicitudes; se usa el límite en memoria.')
    return False
