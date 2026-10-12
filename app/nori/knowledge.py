"""Fuentes documentales autorizadas de Nori (recuperación local).

Solo se leen dos orígenes, ambos propios de Norte y de lectura:

* los archivos ``*.md`` de ``app/nori/knowledge/`` con ``status: verificado`` (ruta fija; nunca una ruta recibida del usuario);
* el texto de las páginas públicas (Personas, Herramientas, Seguridad, Acerca de Norte, Términos y Privacidad), tomado de las
  mismas fuentes que las renderizan, de modo que Nori siempre responde con lo que la web dice hoy.

Todo documento se trata como **contenido no confiable**: se descartan los archivos con secretos, datos personales o
instrucciones dirigidas a un asistente, se limita su tamaño y se reduce a texto plano. Nada de ``data/``, ``docs/``,
``README``, ``.env`` ni de la base de datos se indexa.
"""
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from flask import current_app, render_template, url_for

KNOWLEDGE_DIR = Path(__file__).resolve().parent / 'knowledge'
VERIFIED = 'verificado'
MAX_FILE_BYTES = 20_000
MAX_CHUNK_CHARS = 450          # un fragmento corto puntúa mejor y cabe en una respuesta
MAX_SECTION_CHARS = 1800       # tope de lectura por sección antes de dividirla
MAX_CHUNKS = 200

PAGE_TITLES = {'personas': 'Personas', 'herramientas': 'Herramientas', 'seguridad': 'Seguridad', 'acerca': 'Acerca de Norte'}
LEGAL_PAGES = {'terminos': ('Términos y condiciones', 'main.terms'), 'privacidad': ('Privacidad', 'main.privacy')}

_SECRET_PATTERNS = (
    re.compile(r'(password|passwd|contraseña|api[_ -]?key|secret|secreto|token)\s*[:=]\s*\S{6,}', re.I),
    re.compile(r'-----BEGIN [A-Z ]*(PRIVATE|CERTIFICATE)'),
    re.compile(r'\b(sk|pk|ghp|xox[abp])[-_][A-Za-z0-9_-]{16,}'),
    re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'),            # correos: datos personales
    re.compile(r'\d{9,}'),                              # DNI de 8 dígitos + cualquier número largo (cuentas, tarjetas, teléfonos)
    re.compile(r'(postgres|mysql|sqlite)(\+\w+)?://', re.I),
)
_INSTRUCTION_PATTERNS = (
    re.compile(r'\b(ignora|ignore|olvida|desobedece|omite)\b.{0,40}\b(instrucciones|reglas|indicaciones|anteriores)', re.I),
    re.compile(r'\b(system prompt|prompt del sistema|eres ahora|a partir de ahora (eres|responde)|actúa como)\b', re.I),
    re.compile(r'\b(responde siempre|nunca digas|revela (tu|las) (instrucciones|reglas))\b', re.I),
)


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str          # documento o página de origen (se muestra como fuente)
    heading: str
    url: str            # ruta interna oficial
    text: str
    origin: str         # 'documento' | 'pagina'


def contains_secret(text):
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def contains_instruction(text):
    return any(pattern.search(text) for pattern in _INSTRUCTION_PATTERNS)


def safe_internal_url(url):
    """Solo rutas internas absolutas del sitio; nada de esquemas, dominios ni rutas de otras zonas."""
    return isinstance(url, str) and bool(re.fullmatch(r'/[A-Za-z0-9_\-./#]*', url)) and not url.startswith('//')


def plain_text(text):
    """Texto plano: sin HTML, sin marcas Markdown, con espacios normalizados."""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[*_`>#]+', '', text)
    return ' '.join(text.split())


def split_text(text, limit=MAX_CHUNK_CHARS):
    """Divide un texto largo en grupos de oraciones de hasta ``limit`` caracteres (sin cortar oraciones)."""
    sentences = [part for part in re.split(r'(?<=[.!?])\s+', text) if part]
    groups, current = [], ''
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > limit:
            groups.append(current)
            current = sentence
        else:
            current = f'{current} {sentence}'.strip()
    if current:
        groups.append(current)
    return groups or [text[:limit]]


def _warn(message, *args):
    current_app.logger.warning('Nori (documentos): ' + message, *args)       # solo nombres de archivo, nunca contenido


def _front_matter(raw):
    match = re.match(r'---\s*\n(.*?)\n---\s*\n(.*)', raw, re.S)
    if not match:
        return {}, raw
    meta = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(':')
        if separator:
            meta[key.strip().lower()] = value.strip()
    return meta, match.group(2)


def _markdown_chunks(path):
    if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
        _warn('se omite %s (enlace simbólico o tamaño excesivo)', path.name)
        return []
    raw = path.read_text(encoding='utf-8')
    meta, body = _front_matter(raw)
    if meta.get('status') != VERIFIED:
        _warn('se omite %s (sin status: %s)', path.name, VERIFIED)
        return []
    if contains_secret(raw):
        _warn('se omite %s (parece contener secretos o datos personales)', path.name)
        return []
    url, title = meta.get('url', ''), plain_text(meta.get('title', path.stem))
    if not safe_internal_url(url):
        _warn('se omite %s (url no válida)', path.name)
        return []
    chunks = []
    for index, section in enumerate(re.split(r'(?m)^##\s+', body)[1:], start=1):
        heading, _, content = section.partition('\n')
        lines = [line for line in (plain_text(line) for line in content.splitlines()) if line and not contains_instruction(line)]
        text = ' '.join(lines)[:MAX_SECTION_CHARS]
        for part, piece in enumerate(split_text(text) if text else [], start=1):
            chunks.append(Chunk(f'{path.stem}#{index}.{part}', title, plain_text(heading), url, piece, 'documento'))
    return chunks


def load_documents():
    chunks = []
    root = KNOWLEDGE_DIR.resolve()
    for path in sorted(KNOWLEDGE_DIR.glob('*.md')):
        if path.name.lower() == 'readme.md' or root not in path.resolve().parents:
            continue
        chunks.extend(_markdown_chunks(path))
    return chunks


def _page_chunks():
    """Texto de las páginas informativas públicas (mismo diccionario que las renderiza)."""
    from ..routes import PUBLIC_INFO_PAGES
    chunks = []
    for key, page in PUBLIC_INFO_PAGES.items():
        title, url = PAGE_TITLES[key], url_for('main.' + key)
        pieces = [('Presentación', page['intro'])]
        pieces += [(card['title'], f"{card['title']}. {card['description']}") for card in page['highlights']]
        if page.get('note'):
            pieces.append(('Nota', page['note']))
        pieces += [(step, f'{step}. {text}') for step, text in page.get('timeline', [])]
        for index, (heading, text) in enumerate(pieces, start=1):
            text = plain_text(text)
            if text and not contains_instruction(text):
                for part, piece in enumerate(split_text(text[:MAX_SECTION_CHARS]), start=1):
                    chunks.append(Chunk(f'pagina-{key}#{index}.{part}', title, plain_text(heading), url, piece, 'pagina'))
    return chunks


class _ArticleParser(HTMLParser):
    """Extrae (id, título, texto) de cada ``<article>`` de una página legal."""

    def __init__(self):
        super().__init__()
        self.articles, self._current, self._in_heading = [], None, False

    def handle_starttag(self, tag, attrs):
        if tag == 'article':
            self._current = {'id': dict(attrs).get('id', ''), 'heading': '', 'text': []}
        elif tag == 'h2' and self._current is not None:
            self._in_heading = True

    def handle_endtag(self, tag):
        if tag == 'h2':
            self._in_heading = False
        elif tag == 'article' and self._current is not None:
            self.articles.append(self._current)
            self._current = None

    def handle_data(self, data):
        if self._current is None:
            return
        if self._in_heading:
            self._current['heading'] += data
        else:
            self._current['text'].append(data)


def _legal_chunks():
    chunks = []
    for key, (title, endpoint) in LEGAL_PAGES.items():
        parser = _ArticleParser()
        parser.feed(render_template('legal.html', page_key=key))
        base_url = url_for(endpoint)
        for index, article in enumerate(parser.articles, start=1):
            text = plain_text(' '.join(article['text']))
            if text and not contains_instruction(text):
                url = base_url + ('#' + article['id'] if article['id'] else '')
                for part, piece in enumerate(split_text(text[:MAX_SECTION_CHARS]), start=1):
                    chunks.append(Chunk(f'{key}#{index}.{part}', title, plain_text(article['heading']), url, piece, 'pagina'))
    return chunks


def build_chunks():
    """Todos los fragmentos autorizados (documentos + páginas públicas). Requiere el contexto de la aplicación."""
    with current_app.test_request_context('/'):
        chunks = load_documents() + _page_chunks() + _legal_chunks()
    clean = [chunk for chunk in chunks if safe_internal_url(chunk.url) and not contains_secret(chunk.text)]
    return clean[:MAX_CHUNKS]
