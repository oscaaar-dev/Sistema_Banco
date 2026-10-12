"""Memoria conversacional temporal de Nori.

La conversación vive solo en el ``sessionStorage`` del navegador. El servidor no guarda nada: en cada mensaje recibe los
últimos turnos, los **valida como no confiables** y los usa únicamente para entender preguntas de seguimiento
(«más información», «repítelo») durante esa solicitud.

Aislamiento: cada sesión de navegador tiene un *scope* derivado (HMAC) del token CSRF de su cookie de sesión. Si la sesión
cambia (otro usuario, inicio o cierre de sesión, cookie vencida) el scope cambia y el historial anterior se descarta.
"""
import hashlib
import hmac

from flask import current_app, session

from .safety import clean_message, contains_sensitive_data, normalize

ROLES = ('user', 'bot')


def session_scope():
    """Identificador opaco de la sesión actual; vacío si todavía no hay sesión."""
    raw = session.get('csrf_token')
    if not raw:
        return ''
    key = str(current_app.config['SECRET_KEY']).encode('utf-8')
    return hmac.new(key, ('nori-memory:' + str(raw)).encode('utf-8'), hashlib.sha256).hexdigest()[:24]


def max_messages():
    """Número máximo de mensajes de historial: dos por turno (pregunta y respuesta)."""
    return current_app.config['NORI_MEMORY_TURNS'] * 2


def clean_history(raw):
    """Historial recibido del navegador, depurado: solo roles permitidos, texto limpio, acotado y sin datos sensibles.

    Lo que no cumple se descarta en silencio; nunca se confía en el contenido, ni se acepta más de ``max_messages()``.
    """
    if not isinstance(raw, list):
        return []
    limit = current_app.config['NORI_MAX_MESSAGE_CHARS']
    cleaned = []
    for item in raw[-max_messages():]:
        if not isinstance(item, dict):
            continue
        role, text = item.get('role'), item.get('text')
        if role not in ROLES or not isinstance(text, str):
            continue
        text = clean_message(text, limit)
        if not text or contains_sensitive_data(text):
            continue
        cleaned.append({'role': role, 'text': text})
    return cleaned


def last_bot_intent(history, reply_index):
    """Tema de la última respuesta de Nori en el historial, solo si coincide con una respuesta que Nori realmente da.

    ``reply_index`` mapea el texto normalizado de cada respuesta conocida a su intención; así un historial inventado
    no puede introducir texto propio: lo único que puede lograr es elegir uno de los temas que Nori ya conoce.
    """
    for entry in reversed(history):
        if entry['role'] == 'bot':
            return reply_index.get(normalize(entry['text']))
    return None
