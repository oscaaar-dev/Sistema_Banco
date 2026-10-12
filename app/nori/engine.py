"""Cómo decide Nori qué responder: reglas predefinidas, recuperación documental local o reconocer que no hay información.

Orden de decisión (nada de esto usa un modelo generativo ni servicios externos):

1. Regla **fuerte** (frase exacta o varias palabras clave): respuesta predefinida (``kind='reglas'``), salvo que un
   fragmento documental muy específico (3 o más términos en común y alta relevancia) responda mejor.
2. Si no, recuperación en la documentación autorizada: si hay fragmentos suficientemente relevantes, se **citan** con sus
   fuentes (``kind='documentacion'``). Una regla débil (una sola palabra clave) cede ante un fragmento específico de la
   pregunta (varios términos en común) o muy relevante.
3. Si no hay documentación suficiente pero sí una regla débil, se usa la regla.
4. Si no hay nada: Nori lo reconoce y orienta a las páginas oficiales (``kind='sin_informacion'``).

Las preguntas de seguimiento usan solo el historial ya validado por ``memory``: el tema de la respuesta anterior o la última
pregunta del usuario para volver a consultar los documentos.
"""
from flask import current_app

from . import rag, rules
from .memory import last_bot_intent, last_user_message

RULES_STRONG = 6          # puntaje de regla a partir del cual la respuesta predefinida se considera segura


def _docs_reply(hits, question, skip=None):
    config = current_app.config
    top = hits[0]
    prefix = f'Según la documentación de Norte («{top.chunk.title}»): '
    room = max(120, min(config['NORI_RAG_EXCERPT_CHARS'], config['NORI_MAX_MESSAGE_CHARS'] - len(prefix) - 4))
    body = rag.excerpt(top.chunk, question, room, skip=skip)
    seen, links = set(), []
    for hit in hits[:config['NORI_RAG_MAX_SOURCES']]:
        if hit.score < top.score * 0.5 or (hit.chunk.title, hit.chunk.url) in seen:
            continue
        seen.add((hit.chunk.title, hit.chunk.url))
        links.append({'label': hit.chunk.title, 'href': hit.chunk.url})
    return {'reply': prefix + body, 'mood': 'reposo', 'links': links, 'suggestions': [], 'kind': 'documentacion', 'body': body}


def _beats_rule(hit):
    """Un fragmento específico (varios términos de la pregunta) gana a una regla débil; con un solo término hace falta más relevancia."""
    config = current_app.config
    return hit.score >= (config['NORI_RAG_STRONG_SCORE'] if hit.matched >= 2 else config['NORI_RAG_SINGLE_TERM_SCORE'])


def _safe_search(question):
    try:
        return rag.search(question)
    except Exception:
        current_app.logger.warning('Nori: falló la recuperación documental; se responde con reglas.')
        return []


def _docs_follow_up(question, kind):
    """«Más información» o «repítelo» sobre una respuesta documentada: se vuelve a consultar con la pregunta anterior."""
    hits = _safe_search(question)
    if not hits:
        return None
    first = _docs_reply(hits, question)
    if kind == 'repeat':
        return {key: value for key, value in first.items() if key != 'body'}
    following = rag.next_in_document(hits[0].chunk)          # primero, la siguiente sección del mismo documento
    if following is not None:
        more = _docs_reply([rag.Hit(following, hits[0].score, hits[0].matched)], question)
    elif len(hits) > 1:
        more = _docs_reply(hits[1:], question)
    else:
        return None
    return {key: value for key, value in more.items() if key != 'body'}


def _tag(result, kind):
    result = dict(result)
    result.pop('intent', None)
    result['kind'] = kind
    return result


def respond(message, history):
    """Respuesta de Nori (``reply``, ``mood``, ``links``, ``suggestions``, ``kind``) para un mensaje ya validado."""
    intent, score = rules.best_match(message)
    last = last_bot_intent(history, rules.reply_index())
    if intent is not None and rules.is_menu_suggestion(message):
        return _tag(rules.answer(message, last), 'reglas')              # entradas del menú: respuesta curada
    if intent is None:
        follow_up = rules.follow_up_kind(message)
        if follow_up:
            question = last_user_message(history)
            if last is None and question:
                docs = _docs_follow_up(question, follow_up)
                if docs:
                    return docs
            return _tag(rules.answer(message, last), 'reglas')
        hits = _safe_search(message)
        if hits:
            return {key: value for key, value in _docs_reply(hits, message).items() if key != 'body'}
        return _tag(rules.answer(message), 'sin_informacion')
    hits = _safe_search(message)
    if score >= RULES_STRONG:
        # una regla fuerte pero genérica cede solo ante un fragmento muy específico de la pregunta
        if hits and hits[0].matched >= 3 and hits[0].score >= current_app.config['NORI_RAG_OVERRIDE_SCORE']:
            return {key: value for key, value in _docs_reply(hits, message).items() if key != 'body'}
        return _tag(rules.answer(message, last), 'reglas')
    if hits and _beats_rule(hits[0]):
        return {key: value for key, value in _docs_reply(hits, message).items() if key != 'body'}
    return _tag(rules.answer(message, last), 'reglas')
