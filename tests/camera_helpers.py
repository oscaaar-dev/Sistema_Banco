"""Ayudantes de prueba para la cámara obligatoria: un JPEG mínimo válido y la aceptación del aviso."""
import io


def fake_jpeg(width=320, height=240):
    """JPEG estructuralmente válido (SOI, APP0, SOF0, EOI). No es una foto: basta para las validaciones del servidor."""
    app0 = b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    sof0 = b'\xff\xc0\x00\x0b\x08' + height.to_bytes(2, 'big') + width.to_bytes(2, 'big') + b'\x01\x01\x11\x00'
    return b'\xff\xd8' + app0 + sof0 + b'\xff\xd9'


def capture_file(data=None, name='capture.jpg'):
    """Valor para ``data={'capture': ...}`` del cliente de pruebas (archivo en el formulario multipart)."""
    return (io.BytesIO(data if data is not None else fake_jpeg()), name)


def accept_camera_notice(client, token, kind, key, version='2026-10-v2'):
    response = client.post('/camera/consent', json={'version': version, 'kind': kind, 'idempotency_key': key},
                           headers={'X-CSRFToken': token})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response
