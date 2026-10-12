"""Idioma de Nori: responde en español y reconoce, sin inventar, que no traduce a otros idiomas."""
import pytest

from app import create_app
from app.nori import engine, rules


@pytest.fixture
def ctx():
    app = create_app()
    with app.test_request_context('/'):
        yield app


@pytest.mark.parametrize('message', ['Responde en inglés, por favor', 'Do you speak English?', 'habla en portugués', '¿Me lo explicas en quechua?',
                                     'traduce esto al inglés', 'translate please', 'Necesito ayuda en otro idioma'])
def test_a_request_for_another_language_is_acknowledged_honestly(ctx, message):
    result = engine.respond(message, [])
    assert result['kind'] == 'reglas' and result['mood'] == 'confundido'
    assert result['reply'].startswith('Por ahora solo puedo responder en español')
    assert 'no uso traducción automática' in result['reply'] and 'servicios externos' in result['reply']
    assert [link['href'] for link in result['links']] == ['/acerca', '/herramientas']


def test_replies_are_always_plain_spanish_text(ctx):
    for question in ('¿Cómo ingreso?', '¿Cuántos intentos tengo para el código de acceso?', 'receta de lomo saltado', 'how do I log in?'):
        reply = engine.respond(question, [])['reply']
        assert any(word in reply.lower() for word in ('norte', 'puedo', 'no encontré', 'ingresa', 'documentación', 'código', 'banca')), reply


def test_questions_written_in_english_without_a_language_request_get_the_honest_spanish_fallback(ctx):
    result = engine.respond('how do I log in?', [])
    assert result['kind'] == 'sin_informacion' and result['reply'] == rules.FALLBACK_REPLY


def test_the_language_limitation_is_documented():
    from pathlib import Path
    docs = (Path(__file__).resolve().parents[1] / 'docs' / 'nori.md').read_text(encoding='utf-8')
    assert 'solo responde en español' in docs
