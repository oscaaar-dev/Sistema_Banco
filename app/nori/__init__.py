"""Nori, asistente de la web pública de Norte.

Responde con reglas (y, en etapas siguientes, con documentación autorizada) sin servicios externos ni costo. Conserva la
protección CSRF de la aplicación: el navegador pide un token con ``GET /api/nori/csrf`` y lo envía en ``X-CSRFToken``.
No guarda conversaciones: lo escrito solo se procesa para responder. La memoria de la conversación vive en el
``sessionStorage`` del navegador; el servidor recibe los últimos turnos, los valida y no los conserva (ver ``memory``).
"""
import hmac

from flask import Blueprint, current_app, jsonify, request
from flask_wtf.csrf import CSRFError, generate_csrf

from ..routes import _request_ip_hash
from .memory import clean_history, last_bot_intent, session_scope
from .rules import answer, reply_index
from .safety import SENSITIVE_REPLY, clean_message, contains_sensitive_data, register_request_and_check_limit

nori = Blueprint('nori', __name__, url_prefix='/api/nori')


def _json(payload, status=200):
    payload.setdefault('links', [])
    payload.setdefault('suggestions', [])
    payload['csrf'] = generate_csrf()      # token vigente para el siguiente mensaje (caduca con el tiempo)
    payload['scope'] = session_scope()     # identifica esta sesión de navegador: si cambia, la memoria local se descarta
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


@nori.errorhandler(CSRFError)
def csrf_failed(error):
    return _json({'error': 'csrf', 'mood': 'confundido',
                  'reply': 'La sesión del chat venció. Vuelve a enviar tu mensaje.'}, 400)


@nori.get('/csrf')
def csrf_token():
    response = jsonify(csrf=generate_csrf(), scope=session_scope())
    response.headers['Cache-Control'] = 'no-store'
    return response


@nori.post('/chat')
def chat():
    if register_request_and_check_limit(_request_ip_hash('nori-chat-ip')):
        return _json({'error': 'rate_limit', 'mood': 'confundido',
                      'reply': 'Estás escribiendo muy rápido. Esperemos un momento y seguimos.'}, 429)
    if (request.content_length or 0) > current_app.config['NORI_MAX_REQUEST_BYTES']:
        return _json({'error': 'too_large', 'mood': 'confundido',
                      'reply': 'Tu mensaje o el historial es demasiado grande. Inicia una nueva conversación.'}, 413)
    data = request.get_json(silent=True)
    raw = data.get('message') if isinstance(data, dict) else None
    if not isinstance(raw, str):
        return _json({'error': 'invalid', 'mood': 'confundido', 'reply': 'Escríbeme tu consulta y te ayudo.'}, 400)
    limit = current_app.config['NORI_MAX_MESSAGE_CHARS']
    if len(raw) > limit * 4:
        return _json({'error': 'too_long', 'mood': 'confundido',
                      'reply': f'Tu mensaje es muy largo. Intenta con menos de {limit} caracteres.'}, 400)
    message = clean_message(raw, limit)
    if not message:
        return _json({'error': 'empty', 'mood': 'confundido', 'reply': 'Escríbeme tu consulta y te ayudo.'}, 400)
    if contains_sensitive_data(message):
        return _json({'reply': SENSITIVE_REPLY, 'mood': 'seguridad', 'redact': True})
    # Memoria temporal: el historial llega del navegador y se trata como no confiable. Solo se acepta si pertenece a esta
    # misma sesión (scope); si no, se descarta y se avisa para que el navegador borre su copia.
    history, reset = [], False
    if data.get('history'):
        sent_scope, scope = data.get('scope'), session_scope()
        if scope and isinstance(sent_scope, str) and hmac.compare_digest(sent_scope.encode('utf-8'), scope.encode('utf-8')):
            history = clean_history(data.get('history'))
        else:
            reset = True
    result = answer(message, last_bot_intent(history, reply_index()))
    result.pop('intent', None)
    if reset:
        result['reset'] = True
    return _json(result)
