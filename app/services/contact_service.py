"""Contactos de transferencia: búsqueda de clientes de Norte, nombre enmascarado y alta/baja segura.

Todas las consultas usan el ORM con parámetros enlazados. Un contacto siempre pertenece a un solo usuario y se
guarda con el nombre enmascarado de la persona (el mismo que el cliente vio al confirmar).
"""
import re
from ..models import db, Account, Beneficiary, OperationChallenge, Transfer, User
from .account_identifiers import account_number_from_cci

CONTACT_BANK='Norte'
# Un contacto con transferencias registradas no puede borrarse (clave foránea de ``transfers``): se archiva con esta
# marca en ``bank_name`` y deja de mostrarse, así no hace falta alterar el esquema de la base.
ARCHIVED_BANK='Norte · eliminado'

_IDENTIFIER_PATTERN=re.compile(r'\d{8}|\d{10,20}')


def mask_name(full_name):
    """«Ricardo Ortega Vega» → «Ricardo O*** V***»: nombre de pila completo e inicial del resto."""
    parts=str(full_name or '').split()
    if not parts:
        return 'Cliente Norte'
    return ' '.join([parts[0]]+[f'{part[0]}***' for part in parts[1:]])[:100]


def normalize_identifier(raw):
    """Devuelve solo los dígitos de un DNI (8) o de un número de cuenta/CCI (10 a 20); ``None`` si no es válido."""
    value=re.sub(r'[\s-]','',str(raw or ''))
    if not value.isascii() or not _IDENTIFIER_PATTERN.fullmatch(value):
        return None
    return value


def find_client_account(identifier):
    """Cuenta activa de un cliente a partir de un DNI, un número de cuenta o un CCI; ``None`` si no hay coincidencia."""
    base=Account.query.join(User,User.id==Account.user_id).filter(User.role=='CLIENTE',Account.status=='ACTIVA')
    if len(identifier)==8:
        return base.filter(User.dni==identifier).order_by(Account.id).first()
    account=base.filter((Account.number==identifier)|(Account.cci==identifier)).order_by(Account.id).first()
    if account is None:
        embedded=account_number_from_cci(identifier)
        if embedded:
            account=base.filter(Account.number==embedded).order_by(Account.id).first()
    return account


def active_beneficiaries(user_id):
    return (Beneficiary.query.filter(Beneficiary.user_id==user_id,Beneficiary.bank_name!=ARCHIVED_BANK)
            .order_by(Beneficiary.name,Beneficiary.id))


def get_active_beneficiary(user_id,beneficiary_id):
    if beneficiary_id is None:
        return None
    return active_beneficiaries(user_id).filter(Beneficiary.id==beneficiary_id).first()


def find_destination_account(account_number,sending_account_id):
    """Cuenta de Norte que recibe una transferencia hacia ese número (nunca la propia cuenta de origen)."""
    return (Account.query.filter(Account.number==account_number,Account.status=='ACTIVA',Account.id!=sending_account_id)
            .first())


def existing_contact(user_id,account_number):
    """Contacto del usuario para esa cuenta, activo o archivado."""
    return Beneficiary.query.filter_by(user_id=user_id,account_number=account_number).first()


def save_contact(user,account):
    """Guarda (o reactiva) el contacto. Lanza ``ValueError`` si es el propio usuario o ya estaba agregado."""
    if account.user_id==user.id:
        raise ValueError('Ese número corresponde a tus propias cuentas. Para moverte saldo entre ellas usa un traslado interno.')
    holder=db.session.get(User,account.user_id)
    name=mask_name(holder.full_name if holder else '')
    contact=existing_contact(user.id,account.number)
    if contact is not None and contact.bank_name!=ARCHIVED_BANK:
        raise ValueError('Ya tienes a este cliente en tus contactos.')
    if contact is None:
        contact=Beneficiary(user_id=user.id,account_number=account.number,bank_name=CONTACT_BANK,name=name)
        db.session.add(contact)
    else:
        contact.name=name
        contact.bank_name=CONTACT_BANK
    db.session.flush()
    return contact


def delete_contact(contact):
    """Elimina el contacto; si ya tiene transferencias registradas lo archiva para conservar el historial."""
    OperationChallenge.query.filter_by(beneficiary_id=contact.id).delete(synchronize_session=False)
    has_history=db.session.query(Transfer.id).filter(Transfer.beneficiary_id==contact.id).first() is not None
    if has_history:
        contact.bank_name=ARCHIVED_BANK
    else:
        db.session.delete(contact)
