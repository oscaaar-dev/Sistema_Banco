"""Política única de contraseñas de Norte (registro, recuperación y cualquier cambio futuro).

Reglas, todas obligatorias:
  - entre 8 y 10 caracteres (ambos límites incluidos);
  - al menos una mayúscula A-Z, una minúscula a-z, un número 0-9 y un punto (.).

No se exigen otros símbolos. La contraseña nunca se recorta ni se transforma: se valida tal como llega.
El servidor es quien autoriza; las expresiones de RULES también se publican en la interfaz (data-pattern)
para que la validación visual use exactamente las mismas reglas.
"""
import re
import unicodedata

MIN_LENGTH = 8
MAX_LENGTH = 10

# key, texto visible, expresión (solo para reglas de contenido). Mantener el orden: es el de la interfaz.
RULES = (
    {'key': 'length', 'label': f'Entre {MIN_LENGTH} y {MAX_LENGTH} caracteres', 'pattern': None},
    {'key': 'upper', 'label': 'Al menos una mayúscula', 'pattern': r'[A-Z]'},
    {'key': 'lower', 'label': 'Al menos una minúscula', 'pattern': r'[a-z]'},
    {'key': 'digit', 'label': 'Al menos un número', 'pattern': r'[0-9]'},
    {'key': 'dot', 'label': 'Al menos un punto (.)', 'pattern': r'\.'},
)

POLICY_MESSAGE = ('La contraseña debe tener entre 8 y 10 caracteres, con al menos una mayúscula, '
                  'una minúscula, un número y un punto (.).')
CONTROL_CHARACTERS_MESSAGE = 'La contraseña no puede incluir caracteres de control.'
MISMATCH_MESSAGE = 'Las contraseñas no coinciden.'


def failed_rules(password):
    """Devuelve las claves de las reglas que NO cumple la contraseña (lista vacía si es válida)."""
    if not isinstance(password, str):
        return [rule['key'] for rule in RULES]
    failed = []
    for rule in RULES:
        if rule['key'] == 'length':
            ok = MIN_LENGTH <= len(password) <= MAX_LENGTH
        else:
            ok = re.search(rule['pattern'], password) is not None
        if not ok:
            failed.append(rule['key'])
    return failed


def has_control_characters(password):
    return isinstance(password, str) and any(unicodedata.category(char).startswith('C') for char in password)


def validate_new_password(password, confirmation=None):
    """Valida una contraseña nueva. Devuelve (errores, claves_incumplidas).

    ``confirmation=None`` omite la comprobación de coincidencia. No se usa strip(): los espacios cuentan.
    """
    errors = []
    failed = failed_rules(password)
    if failed:
        errors.append(POLICY_MESSAGE)
    if has_control_characters(password):
        errors.append(CONTROL_CHARACTERS_MESSAGE)
    if confirmation is not None and password != confirmation:
        errors.append(MISMATCH_MESSAGE)
    return errors, failed
