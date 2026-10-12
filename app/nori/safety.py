"""Seguridad de Nori: normalización de la entrada, detección de datos sensibles y límite de mensajes.

Nada de lo que escribe la persona se guarda en el servidor: el límite de mensajes solo anota una fila de auditoría
(acción, IP con hash y hora), sin el texto de la conversación.
"""
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta

from flask import current_app

from ..models import AuditLog, db, now_utc

AUDIT_ACTION='NORI_CHAT'
_CONTROL_CHARACTERS=re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_DIGIT_SEPARATORS=re.compile(r'(?<=\d)[ .-](?=\d)')
_LONG_NUMBER=re.compile(r'\d{6,}')
_SECRET_DECLARATION=re.compile(r'\b(contrasena|clave|pin|cvv|cvc|otp|codigo|token)\s+(es|era|sera|son)\b')
_SECRET_ASSIGNMENT=re.compile(r'\b(contraseña|contrasena|clave|pin|cvv|cvc|otp|código|codigo|token)\s*[:=]', re.I)

SENSITIVE_REPLY=('Por tu seguridad no escribas aquí DNI, números de tarjeta, contraseñas ni códigos. '
                 'No los necesito y no debes compartirlos con nadie. Oculté tu mensaje.')

# Respaldo en memoria (por proceso): sigue limitando aunque la base de datos no responda.
_memory_hits=defaultdict(deque)


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


def _memory_limited(key,limit,window):
    now=time.monotonic()
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
    try:
        now=now_utc()
        base=AuditLog.query.filter_by(action=AUDIT_ACTION,ip_local=ip_hash)
        if base.filter(AuditLog.created_at>=now-timedelta(seconds=window)).count()>=per_window:
            return True
        if base.filter(AuditLog.created_at>=now-timedelta(days=1)).count()>=per_day:
            return True
        db.session.add(AuditLog(user_id=None,action=AUDIT_ACTION,module='NORI',result='ATENDIDA',ip_local=ip_hash,created_at=now))
        # La auditoría de Nori solo sirve para contar: se purga lo que ya no cuenta (más de un día).
        AuditLog.query.filter(AuditLog.action==AUDIT_ACTION,AuditLog.created_at<now-timedelta(days=1)).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning('Nori: no se pudo registrar el control de solicitudes; se usa el límite en memoria.')
    return False
