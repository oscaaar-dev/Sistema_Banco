"""Identificadores ficticios de cuenta: CCI determinista derivado del número de cuenta.

El CCI de Norte es solo de demostración. Si la cuenta ya tiene uno guardado se respeta; si no, se calcula a partir
del número de cuenta, de modo que no hace falta ninguna columna nueva y el valor es siempre el mismo.
"""
import hashlib

CCI_ENTITY_CODE='999'    # código de entidad ficticio
CCI_BRANCH_CODE='000'    # código de oficina ficticio


def _check_digits(first_eighteen):
    """Dos dígitos de control (módulo 97) para detectar errores de digitación."""
    return f'{98-int(first_eighteen)%97:02d}'


def derive_cci(account_number):
    """CCI de 20 dígitos: entidad (3) + oficina (3) + cuenta de 12 dígitos + control (2).

    Las cuentas con otro formato reciben un cuerpo de 12 dígitos derivado de un hash estable de su número.
    """
    number=str(account_number or '').strip()
    if number.isascii() and number.isdigit() and len(number)==12:
        body=number
    else:
        digest=hashlib.sha256(('norte-cci:'+number).encode('utf-8')).hexdigest()
        body=f'{int(digest,16)%10**12:012d}'
    first_eighteen=CCI_ENTITY_CODE+CCI_BRANCH_CODE+body
    return first_eighteen+_check_digits(first_eighteen)


def cci_for(account):
    """CCI guardado de la cuenta o, si no existe, el derivado de su número."""
    return account.cci or derive_cci(account.number)


def account_number_from_cci(cci):
    """Número de cuenta embebido en un CCI derivado con control válido; ``None`` si no corresponde al formato."""
    value=str(cci or '')
    if len(value)!=20 or not value.isascii() or not value.isdigit():
        return None
    if not value.startswith(CCI_ENTITY_CODE+CCI_BRANCH_CODE) or _check_digits(value[:18])!=value[18:]:
        return None
    return value[6:18]
