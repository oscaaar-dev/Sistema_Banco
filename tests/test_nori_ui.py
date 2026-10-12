"""Nori · interfaz: contraste, ARIA, estados de error y seguridad del cliente (comprobaciones estáticas del CSS y el JS)."""
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'nori'
CSS = (STATIC / 'nori.css').read_text(encoding='utf-8')
JS = (STATIC / 'nori.js').read_text(encoding='utf-8')


def _colour(name):
    return re.search(rf'--{name}:\s*#([0-9a-fA-F]{{6}})', CSS).group(1)


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground, background):
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize('foreground,background', [
    ('n-text', 'n-bg'), ('n-text', 'n-card'),          # texto principal en el panel y en las burbujas de Nori
    ('n-mute', 'n-bg'), ('n-mute', 'n-card'),          # texto secundario, etiqueta de origen y aviso legal
    ('n-pur-hi', 'n-bg'), ('n-pur-hi', 'n-card'),      # enlaces a las fuentes
    ('n-text', 'n-pur'),                               # burbuja del usuario y botón Enviar
])
def test_text_colours_meet_wcag_aa_contrast(foreground, background):
    assert contrast(_colour(foreground), _colour(background)) >= 4.5


def test_the_palette_is_the_norte_palette():
    assert _colour('n-pur') == '6d3bd2' and _colour('n-bg') == '0c0818'


def test_chat_semantics_and_accessible_names():
    for fragment in ("panel.setAttribute('role', 'dialog')", "panel.setAttribute('aria-label', 'Chat con Nori')", "log.setAttribute('role', 'log')",
                     "log.setAttribute('aria-live', 'polite')", "input.setAttribute('aria-label', 'Tu mensaje para Nori')",
                     "input.setAttribute('aria-describedby', 'nori-legal')", "log.setAttribute('aria-busy', 'true')", "log.removeAttribute('aria-busy')",
                     "mascot.setAttribute('aria-expanded'", "mascot.setAttribute('aria-controls', 'nori-panel')", "setAttribute('role', 'status')"):
        assert fragment in JS, fragment
    assert "e.key === 'Escape'" in JS and 'mascot.focus()' in JS and 'input.focus()' in JS      # foco al abrir y al cerrar


def test_the_answer_origin_is_labelled_and_kinds_are_validated_before_use():
    assert "reglas: 'Respuesta predefinida'" in JS and "documentacion: 'Según la documentación de Norte'" in JS
    assert 'KIND_LABELS[t.kind] ? t.kind' in JS and 'KIND_LABELS[data.kind] ? data.kind' in JS
    assert '.nori-kind' in CSS


def test_errors_can_be_retried_without_duplicating_the_user_message_or_remembering_the_failure():
    assert "'Reintentar'" in JS and 'retry: canRetry ? text' in JS and 'options.retry ? (lastUserBubble' in JS
    assert re.search(r"data\.error !== 'rate_limit'", JS)                               # un límite de uso no se reintenta
    assert '!data.redact && !data.error && !data.offline' in JS                         # los errores no entran en la memoria


def test_duplicate_sends_are_blocked_and_input_is_limited():
    assert 'if (busy) return;' in JS and 'send.disabled = true' in JS and 'input.maxLength = MAX_CHARS' in JS
    assert "slice(0, MAX_CHARS)" in JS


def test_the_client_never_renders_server_text_as_html_nor_follows_external_links():
    assert 'innerHTML' not in JS and 'insertAdjacentHTML' not in JS and 'document.write' not in JS and 'eval(' not in JS
    assert re.search(r"/\^\\/\(\?!\[\\/\\\\\]\)/\.test\(href\)", JS)                  # solo rutas internas
    assert 'console.' not in JS                                                          # sin registros de conversaciones en la consola


def test_long_text_wraps_and_the_reading_area_does_not_overflow():
    assert re.search(r'\.nori-msg \{[^}]*overflow-wrap: anywhere', CSS)
    assert 'overflow-y: auto' in CSS and 'overscroll-behavior: contain' in CSS


def test_typing_animation_is_short_and_motion_can_be_reduced():
    assert 'text.length / 70' in JS and 'if (reduce())' in JS
    assert '@media (prefers-reduced-motion: reduce)' in CSS
