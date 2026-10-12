"""Foto obligatoria al confirmar una operación: validación, guardado y purga por vencimiento.

La imagen llega como archivo JPEG desde el navegador (canvas → JPEG, máximo 640 px de ancho). El servidor no
confía en lo que declara el cliente: comprueba la firma del archivo, que termine correctamente y que su cabecera
declare dimensiones razonables, y rechaza lo que supere el tamaño permitido.

Nunca se escribe el contenido de la imagen en logs ni en mensajes de error.
"""
from datetime import timedelta

from ..models import OperationCapture, db, now_utc

CAPTURE_REQUIRED_MESSAGE = 'Activa la cámara para confirmar la operación: se toma una foto por seguridad.'
CAPTURE_INVALID_MESSAGE = 'No pudimos validar la foto de seguridad. Vuelve a activar la cámara e inténtalo otra vez.'
CAPTURE_TOO_LARGE_MESSAGE = 'La foto de seguridad es demasiado pesada. Vuelve a activar la cámara e inténtalo otra vez.'

MIN_SIDE = 32
MAX_SIDE = 1280
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def jpeg_dimensions(data):
    """Devuelve ``(ancho, alto)`` si ``data`` es un JPEG bien formado en su estructura de marcadores; si no, ``None``."""
    if len(data) < 12 or data[:3] != b'\xff\xd8\xff' or data[-2:] != b'\xff\xd9':
        return None
    index = 2
    size = len(data)
    while index + 4 <= size:
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        while marker == 0xFF and index + 2 < size:  # relleno 0xFF permitido entre marcadores
            index += 1
            marker = data[index + 1]
        index += 2
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9 or marker == 0xDA:  # fin o datos de imagen antes de la cabecera SOF
            return None
        if index + 2 > size:
            return None
        length = int.from_bytes(data[index:index + 2], 'big')
        if length < 2 or index + length > size:
            return None
        if marker in _SOF_MARKERS:
            if length < 8:
                return None
            height = int.from_bytes(data[index + 3:index + 5], 'big')
            width = int.from_bytes(data[index + 5:index + 7], 'big')
            return width, height
        index += length
    return None


def read_capture(file_storage, max_bytes):
    """Valida el archivo ``capture`` del formulario y devuelve sus datos listos para guardar.

    Lanza ``ValueError`` con un mensaje amable si falta, pesa demasiado o no es un JPEG válido.
    """
    if file_storage is None or not getattr(file_storage, 'filename', None):
        raise ValueError(CAPTURE_REQUIRED_MESSAGE)
    data = file_storage.stream.read(max_bytes + 1)
    if not data:
        raise ValueError(CAPTURE_REQUIRED_MESSAGE)
    if len(data) > max_bytes:
        raise ValueError(CAPTURE_TOO_LARGE_MESSAGE)
    dimensions = jpeg_dimensions(data)
    if dimensions is None:
        raise ValueError(CAPTURE_INVALID_MESSAGE)
    width, height = dimensions
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise ValueError(CAPTURE_INVALID_MESSAGE)
    return {'image': data, 'mime_type': 'image/jpeg', 'size_bytes': len(data), 'width': width, 'height': height}


def add_capture(user_id, transaction_id, capture, *, consent_version=None, ip_hash=None, user_agent=None):
    """Añade la captura a la sesión actual. No hace commit: se guarda en la misma transacción que la operación."""
    row = OperationCapture(
        user_id=user_id, transaction_id=transaction_id, image=capture['image'], mime_type=capture['mime_type'],
        size_bytes=capture['size_bytes'], width=capture['width'], height=capture['height'],
        consent_version=consent_version, ip_hash=ip_hash, user_agent=(user_agent or '')[:255] or None,
    )
    db.session.add(row)
    return row


def purge_expired_captures(retention_days, *, now=None):
    """Borra las capturas más antiguas que el plazo de conservación. Devuelve cuántas eliminó."""
    cutoff = (now or now_utc()) - timedelta(days=retention_days)
    deleted = OperationCapture.query.filter(OperationCapture.created_at < cutoff).delete(synchronize_session=False)
    db.session.commit()
    return deleted
