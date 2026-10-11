"""Cabecera, navegación, pie y datos de contacto compartidos por todas las páginas."""
import re

import pytest

import config
from app import create_app

PUBLIC_PATHS = ['/', '/personas', '/herramientas', '/seguridad', '/acerca', '/terminos', '/privacidad',
                '/login', '/register', '/ruta-que-no-existe']

CHANNELS = [
    {'key': 'instagram', 'label': 'Instagram', 'aria': 'Instagram de Norte', 'url': 'https://instagram.com/norte_demo', 'text': '@norte_demo'},
    {'key': 'whatsapp', 'label': 'WhatsApp', 'aria': 'WhatsApp de Norte', 'url': 'https://wa.me/51900000000', 'text': '+51 900 000 000'},
    {'key': 'facebook', 'label': 'Facebook', 'aria': 'Facebook de Norte', 'url': 'https://facebook.com/norte_demo', 'text': 'Norte'},
]


def _client(**overrides):
    app = create_app(overrides or None)
    app.config['TESTING'] = True
    return app.test_client()


def test_contact_config_accepts_only_safe_values(monkeypatch):
    monkeypatch.setenv('NORTE_INSTAGRAM_URL', 'https://instagram.com/norte_demo')
    monkeypatch.setenv('NORTE_INSTAGRAM_HANDLE', '@norte_demo')
    monkeypatch.setenv('NORTE_WHATSAPP_NUMBER', '+51 900-000-000')
    monkeypatch.setenv('NORTE_FACEBOOK_URL', 'javascript:alert(1)')
    monkeypatch.setenv('NORTE_CONTACT_EMAIL', 'no es un correo')
    channels = {item['key']: item for item in config._contact_channels()}
    assert set(channels) == {'instagram', 'whatsapp'}, 'Facebook con esquema javascript: debe descartarse'
    assert channels['whatsapp']['url'] == 'https://wa.me/51900000000'
    assert channels['instagram']['text'] == '@norte_demo'
    assert config._contact_email() == ''


def test_contact_config_without_values_shows_nothing(monkeypatch):
    for name in ('NORTE_INSTAGRAM_URL', 'NORTE_WHATSAPP_NUMBER', 'NORTE_FACEBOOK_URL', 'NORTE_CONTACT_EMAIL'):
        monkeypatch.delenv(name, raising=False)
    assert config._contact_channels() == []
    assert config._contact_email() == ''


@pytest.mark.parametrize('path', PUBLIC_PATHS)
def test_every_public_page_has_shared_header_and_footer(path):
    response = _client().get(path)
    html = response.get_data(as_text=True)
    assert response.status_code in (200, 404)
    assert 'id="siteHeader"' in html and 'id="siteFooter"' in html
    assert 'Norte es un proyecto académico de demostración, independiente y sin relación con el BCP' in html
    assert 'SBS' not in html


def test_authenticated_pages_also_show_the_footer():
    client = _client()
    with client.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True
        session['login_2fa_verified'] = True
    html = client.get('/dashboard').get_data(as_text=True)
    assert 'id="siteFooter"' in html
    assert 'Norte es un proyecto académico de demostración' in html


def test_social_links_open_in_new_tab_safely():
    html = _client(CONTACT_CHANNELS=CHANNELS, CONTACT_EMAIL='contacto@example.com').get('/').get_data(as_text=True)
    for channel in CHANNELS:
        assert f'href="{channel["url"]}"' in html
        assert channel['aria'] in html
    external = re.findall(r'<a [^>]*href="https://(?:instagram|wa\.me|facebook)[^>]*>', html)
    assert len(external) >= 3, 'el pie enlaza las tres redes'
    assert all('target="_blank"' in tag and 'rel="noopener noreferrer"' in tag for tag in external)
    assert 'href="mailto:contacto@example.com"' in html


def test_footer_hides_unconfigured_channels_but_keeps_hours_and_notice():
    html = _client(CONTACT_CHANNELS=[], CONTACT_EMAIL='').get('/').get_data(as_text=True)
    assert 'wa.me' not in html and 'instagram.com' not in html
    assert 'Horario de atención' in html
    assert 'Banca digital 100% online' in html


def test_header_and_footer_internal_links_resolve():
    client = _client(CONTACT_CHANNELS=CHANNELS)
    html = client.get('/').get_data(as_text=True)
    chrome = re.search(r'<header class="sh".*?</header>', html, re.S).group(0) + \
        re.search(r'<footer class="sf".*?</footer>', html, re.S).group(0)
    internal = sorted({href.split('#')[0] for href in re.findall(r'href="(/[^"]*)"', chrome)} - {''})
    assert '/personas' in internal and '/seguridad' in internal and '/acerca' in internal
    for href in internal:
        assert client.get(href).status_code == 200, f'enlace roto en cabecera o pie: {href}'


def test_navigation_is_minimal_accessible_and_has_no_empty_sections():
    client = _client()
    html = client.get('/').get_data(as_text=True)
    header = re.search(r'<header class="sh".*?</header>', html, re.S).group(0)
    nav = re.search(r'<nav class="sh__nav".*?</nav>', header, re.S).group(0)
    links = re.findall(r'<a class="sh__link" href="([^"]+)"', nav)
    assert links == ['/personas', '/herramientas', '/seguridad', '/acerca'], 'cuatro enlaces simples, todos a páginas reales'
    assert 'aria-controls="siteNav"' in header and 'aria-expanded="false"' in header
    assert 'mm-trigger' not in html and 'mm-panel' not in html, 'ya no hay menús desplegables'
    for removed in ('PyMES', 'Empresas', 'Ahorro', 'Préstamos', 'Educación financiera', 'Próximamente', 'Concepto'):
        assert removed not in html, f'sección retirada que sigue visible: {removed}'
    current = client.get('/seguridad').get_data(as_text=True)
    assert re.search(r'<a class="sh__link" href="/seguridad" aria-current="page">', current)
    assert current.count('aria-current="page"') == 1


def test_footer_keeps_only_essential_navigation_and_legal_links():
    html = _client().get('/').get_data(as_text=True)
    footer = re.search(r'<footer class="sf".*?</footer>', html, re.S).group(0)
    for href in ('/personas', '/herramientas', '/seguridad', '/acerca', '/privacidad', '/terminos'):
        assert f'href="{href}"' in footer
    assert 'Ayuda y educación' not in footer and 'Próximamente' not in footer
