"""Lectura mínima de los PDF de Norte en las pruebas (contenido sin comprimir, fuentes estándar WinAnsi)."""
import re

_STRING = re.compile(rb'\(((?:\\.|[^\\)])*)\)\s*Tj', re.S)
_ESCAPE = re.compile(rb'\\([0-7]{1,3}|.)', re.S)
_SIMPLE = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f'}


def _unescape(raw):
    def replace(match):
        token = match.group(1)
        if token[:1].isdigit():
            return bytes([int(token, 8)])
        return _SIMPLE.get(token, token)
    return _ESCAPE.sub(replace, raw).decode('cp1252')


def pdf_lines(pdf):
    """Cada cadena de texto dibujada en el PDF, en orden de aparición."""
    return [_unescape(raw) for raw in _STRING.findall(pdf)]


def pdf_text(pdf):
    """Todo el texto del PDF unido con espacios (los saltos de línea de un párrafo quedan como espacios)."""
    return ' '.join(pdf_lines(pdf))


def page_count(pdf):
    return len(re.findall(rb'/Type /Page\b(?!s)', pdf))
