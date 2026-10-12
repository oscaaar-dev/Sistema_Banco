"""Validación de datos de formularios que llegan como texto.

Las funciones lanzan ``ValueError`` con mensajes pensados para mostrarse tal cual al usuario.
Nunca dejan escapar ``decimal.InvalidOperation`` ni textos técnicos de Python.
"""
import re
from decimal import Decimal, InvalidOperation

MIN_AMOUNT = Decimal('0.01')
MAX_AMOUNT = Decimal('10000')
AMOUNT_RANGE_MESSAGE = 'El monto debe estar entre S/ 0.01 y S/ 10,000.00.'
INVALID_AMOUNT_MESSAGE = 'Ingresa un monto válido, solo con números (por ejemplo 25.50).'
EMPTY_AMOUNT_MESSAGE = 'Ingresa un monto válido.'

_AMOUNT_PATTERN = re.compile(r'\d{1,9}(?:\.\d{1,2})?')


def parse_amount(raw, *, minimum=MIN_AMOUNT, maximum=MAX_AMOUNT):
    """Convierte el texto de un monto a ``Decimal`` con dos decimales.

    Quita espacios, acepta la coma como separador decimal y solo admite dígitos con hasta dos decimales.
    Rechaza vacíos, texto, separadores de miles, signos y notación científica.
    """
    text = (raw if isinstance(raw, str) else '' if raw is None else str(raw)).strip()
    text = re.sub(r'\s+', '', text)
    if not text:
        raise ValueError(EMPTY_AMOUNT_MESSAGE)
    if text.count(',') + text.count('.') > 1:
        raise ValueError(INVALID_AMOUNT_MESSAGE)
    text = text.replace(',', '.')
    if not _AMOUNT_PATTERN.fullmatch(text):
        raise ValueError(INVALID_AMOUNT_MESSAGE)
    try:
        amount = Decimal(text).quantize(Decimal('0.01'))
    except InvalidOperation:  # pragma: no cover - el patrón ya garantiza un número válido
        raise ValueError(INVALID_AMOUNT_MESSAGE) from None
    if not minimum <= amount <= maximum:
        raise ValueError(AMOUNT_RANGE_MESSAGE)
    return amount


DURATION_MESSAGE = 'No se pudo validar la duración del formulario.'


def parse_duration_seconds(raw, default=0):
    """Segundos que tardó el usuario en el formulario (0 a 86 400). Texto ilegible o fuera de rango se rechaza."""
    if raw is None or str(raw).strip() == '':
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ValueError(DURATION_MESSAGE) from None
    if not 0 <= value <= 86400:
        raise ValueError(DURATION_MESSAGE)
    return value
