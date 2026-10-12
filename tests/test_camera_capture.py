"""Cámara obligatoria: aviso, foto al confirmar, guardado atómico en la base, acceso restringido y purga."""
import re
from datetime import timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.models import Account, AuditLog, Beneficiary, OperationCapture, OperationChallenge, Transaction, User, db, now_utc
from app.services.capture_service import jpeg_dimensions, purge_expired_captures, read_capture
from camera_helpers import accept_camera_notice, capture_file, fake_jpeg

TECHNICAL = ('Traceback', 'sqlalchemy', 'psycopg', 'IntegrityError', 'decimal.')


def _csrf(page):
    return re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()


def _key(page):
    return re.search(rb'name="idempotency_key" value="([^"]+)"', page.data).group(1).decode()


@pytest.fixture
def ctx(monkeypatch):
    import app.routes as routes_module

    sent = []
    monkeypatch.setattr(routes_module, 'send_operation_code', lambda email, code, details: sent.append(code))
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username='demo').one()
        user.email = 'client@example.test'
        beneficiary = Beneficiary.query.filter_by(user_id=user.id).first()
        account = Account.query.filter_by(user_id=user.id).one()
        ids = {'user': user.id, 'beneficiary': beneficiary.id, 'account': account.id, 'balance': account.balance,
               'admin': User.query.filter_by(username='admin').one().id}
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(ids['user'])
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    page = client.get('/operation/transfer')
    return {'app': app, 'client': client, 'token': _csrf(page), 'key': _key(page), 'sent': sent, 'ids': ids, 'page': page}


def _request_code(c, amount='12.34'):
    c['client'].post('/operation/transfer/send-code', data={
        'csrf_token': c['token'], 'idempotency_key': c['key'], 'beneficiary_id': str(c['ids']['beneficiary']), 'amount': amount})
    assert c['sent'], 'no se envió el código'
    return c['sent'][-1]


def _confirm(c, code, *, capture='default', consent=True, accept=True, amount='12.34'):
    if accept:
        accept_camera_notice(c['client'], c['token'], 'transfer', c['key'])
    data = {'csrf_token': c['token'], 'idempotency_key': c['key'], 'beneficiary_id': str(c['ids']['beneficiary']),
            'amount': amount, 'otp_code': code, 'form_duration_seconds': '9', 'camera_consent': 'yes' if consent else 'no'}
    if capture == 'default':
        data['capture'] = capture_file()
    elif capture is not None:
        data['capture'] = capture
    return c['client'].post('/operation/transfer', data=data)


def _no_operation_happened(c):
    with c['app'].app_context():
        assert Transaction.query.filter_by(idempotency_key=c['key']).count() == 0
        assert OperationCapture.query.count() == 0
        assert Account.query.filter_by(id=c['ids']['account']).one().balance == c['ids']['balance']


# ───────── Aviso y página ─────────
def test_notice_has_a_single_primary_action_and_no_way_to_decline():
    app = create_app()
    app.config['TESTING'] = True
    client = app.test_client()
    with app.app_context():
        user_id = User.query.filter_by(username='demo').one().id
    with client.session_transaction() as browser_session:
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True
    for path in ('/operation/transfer', '/operation/payment', '/operation/recharge'):
        html = client.get(path).get_data(as_text=True)
        assert 'Entendido, activar cámara' in html
        assert 'Cancelar operación' in html
        assert 'No acepto' not in html and 'usar correo' not in html and 'declineCameraConsent' not in html
        assert 'enctype="multipart/form-data"' in html and 'name="capture"' in html
        # el aviso dice la verdad: foto guardada con usuario, operación y fecha; plazo; no identifica ni reemplaza el correo
        assert 'asociada a tu usuario, a la operación y a la fecha' in html
        assert '90 días' in html
        assert 'no identifica personas' in html
        assert 'href="/privacidad"' in html
        assert 'no se guardan datos faciales' not in html and 'no se envían ni se guardan' not in html


def test_privacy_policy_discloses_the_stored_photo():
    app = create_app()
    html = app.test_client().get('/privacidad').get_data(as_text=True)
    assert 'Cámara y foto de seguridad' in html
    assert '90 días' in html
    assert 'no almacena fotogramas' not in html


# ───────── Cámara y foto obligatorias ─────────
def test_confirming_without_accepting_the_notice_is_blocked_and_keeps_the_code(ctx):
    code = _request_code(ctx)
    response = _confirm(ctx, code, accept=False, consent=True)
    html = response.get_data(as_text=True)
    assert 'debes aceptar el aviso y activar la cámara' in html
    _no_operation_happened(ctx)
    with ctx['app'].app_context():
        assert OperationChallenge.query.filter_by(user_id=ctx['ids']['user']).one().consumed_at is None


def test_confirming_without_a_photo_is_blocked_and_keeps_the_code(ctx):
    code = _request_code(ctx)
    response = _confirm(ctx, code, capture=None)
    assert 'Activa la cámara para confirmar la operación' in response.get_data(as_text=True)
    _no_operation_happened(ctx)
    retry = _confirm(ctx, code)  # el mismo código sigue valendo
    assert retry.status_code == 302 and '/receipt/' in retry.headers['Location']


BAD_PHOTOS = [
    pytest.param(b'esto no es una imagen', 'No pudimos validar la foto de seguridad', id='texto-plano'),
    pytest.param(b'<?php echo 1; ?>' + fake_jpeg(), 'No pudimos validar la foto de seguridad', id='codigo-antes-del-jpeg'),
    pytest.param(fake_jpeg()[:-2], 'No pudimos validar la foto de seguridad', id='sin-marcador-de-fin'),
    pytest.param(fake_jpeg(8, 8), 'No pudimos validar la foto de seguridad', id='demasiado-pequena'),
    pytest.param(fake_jpeg(4000, 3000), 'No pudimos validar la foto de seguridad', id='dimensiones-enormes'),
    pytest.param(fake_jpeg()[:-2] + b'\x00' * (300 * 1024) + b'\xff\xd9', 'demasiado pesada', id='mas-de-300-KB'),
]


@pytest.mark.parametrize('payload, message', BAD_PHOTOS)
def test_invalid_or_oversized_photos_are_rejected_with_a_friendly_message(ctx, payload, message):
    code = _request_code(ctx)
    response = _confirm(ctx, code, capture=capture_file(payload))
    html = response.get_data(as_text=True)
    assert message in html
    assert not any(fragment in html for fragment in TECHNICAL)
    _no_operation_happened(ctx)


def test_png_disguised_as_jpg_is_rejected(ctx):
    code = _request_code(ctx)
    png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
    response = _confirm(ctx, code, capture=capture_file(png, name='capture.jpg'))
    assert 'No pudimos validar la foto de seguridad' in response.get_data(as_text=True)
    _no_operation_happened(ctx)


def test_photo_is_saved_with_the_operation_and_its_metadata(ctx):
    code = _request_code(ctx)
    ctx['client'].environ_base['HTTP_USER_AGENT'] = 'PruebaNavegador/1.0'
    response = _confirm(ctx, code)
    assert response.status_code == 302
    with ctx['app'].app_context():
        tx = Transaction.query.filter_by(idempotency_key=ctx['key']).one()
        capture = OperationCapture.query.one()
        assert capture.transaction_id == tx.id and capture.user_id == ctx['ids']['user']
        assert capture.image == fake_jpeg()
        assert capture.mime_type == 'image/jpeg' and capture.size_bytes == len(fake_jpeg())
        assert (capture.width, capture.height) == (320, 240)
        assert capture.consent_version == '2026-10-v2'
        assert capture.user_agent == 'PruebaNavegador/1.0'
        assert capture.ip_hash and len(capture.ip_hash) == 64 and '127.0.0.1' not in capture.ip_hash
        assert capture.created_at is not None


def test_if_saving_the_photo_fails_nothing_is_completed(ctx, monkeypatch):
    import app.services.banking_service as banking

    def boom(*args, **kwargs):
        raise RuntimeError('fallo simulado al guardar la foto')

    monkeypatch.setattr(banking, 'add_capture', boom)
    code = _request_code(ctx)
    response = _confirm(ctx, code)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'No se pudo completar la operación ni guardar la foto de seguridad' in html
    assert 'fallo simulado' not in html and not any(fragment in html for fragment in TECHNICAL)
    _no_operation_happened(ctx)


def test_payment_and_recharge_also_require_the_photo(ctx):
    for kind, extra in (('payment', {'service': 'Agua'}), ('recharge', {'phone': '987654321'})):
        page = ctx['client'].get(f'/operation/{kind}')
        token, key = _csrf(page), _key(page)
        ctx['client'].post(f'/operation/{kind}/send-code', data={'csrf_token': token, 'idempotency_key': key, 'amount': '5', **extra})
        code = ctx['sent'][-1]
        accept_camera_notice(ctx['client'], token, kind, key)
        blocked = ctx['client'].post(f'/operation/{kind}', data={
            'csrf_token': token, 'idempotency_key': key, 'amount': '5', 'otp_code': code,
            'form_duration_seconds': '3', 'camera_consent': 'yes', **extra})
        assert 'Activa la cámara para confirmar la operación' in blocked.get_data(as_text=True)
        ok = ctx['client'].post(f'/operation/{kind}', data={
            'csrf_token': token, 'idempotency_key': key, 'amount': '5', 'otp_code': code,
            'form_duration_seconds': '3', 'camera_consent': 'yes', 'capture': capture_file(), **extra})
        assert ok.status_code == 302
    with ctx['app'].app_context():
        assert OperationCapture.query.count() == 2


def test_internal_transfer_requires_the_photo_too(ctx):
    delivered = ctx['sent']
    with ctx['app'].app_context():
        second = Account(user_id=ctx['ids']['user'], account_type='Corriente', number='900000000000000055', balance=Decimal('20.00'))
        db.session.add(second)
        db.session.commit()
        destination_id = second.id
    page = ctx['client'].get('/internal-transfer')
    token, key = _csrf(page), _key(page)
    assert 'operationConsentDialog' not in page.get_data(as_text=True)  # la cámara solo aparece al confirmar, con el código ya enviado
    base = {'csrf_token': token, 'idempotency_key': key, 'source_account_id': str(ctx['ids']['account']),
            'destination_account_id': str(destination_id), 'amount': '10', 'form_duration_seconds': '4'}
    step_two = ctx['client'].post('/internal-transfer', data={**base, 'action': 'send_code'})
    step_two_html = step_two.get_data(as_text=True)
    assert 'operationConsentDialog' in step_two_html and 'Entendido, activar cámara' in step_two_html
    code = delivered[-1]
    blocked = ctx['client'].post('/internal-transfer', data={**base, 'action': 'confirm', 'otp_code': code})
    assert 'debes aceptar el aviso y activar la cámara' in blocked.get_data(as_text=True)
    accept_camera_notice(ctx['client'], token, 'internal', key)
    no_photo = ctx['client'].post('/internal-transfer', data={**base, 'action': 'confirm', 'otp_code': code})
    assert 'Activa la cámara para confirmar la operación' in no_photo.get_data(as_text=True)
    done = ctx['client'].post('/internal-transfer', data={**base, 'action': 'confirm', 'otp_code': code, 'capture': capture_file()})
    assert done.status_code == 302
    with ctx['app'].app_context():
        capture = OperationCapture.query.one()
        assert Transaction.query.filter_by(id=capture.transaction_id, operation_type='TRANSFERENCIA').count() == 1


def test_the_old_notice_version_is_not_accepted(ctx):
    response = ctx['client'].post('/camera/consent', json={'version': '2026-10-v1', 'kind': 'transfer', 'idempotency_key': ctx['key']},
                                  headers={'X-CSRFToken': ctx['token']})
    assert response.status_code == 400


# ───────── Acceso restringido a las imágenes ─────────
def _make_capture(ctx):
    code = _request_code(ctx)
    assert _confirm(ctx, code).status_code == 302
    with ctx['app'].app_context():
        return OperationCapture.query.one().id


def _login_as(client, user_id):
    with client.session_transaction() as browser_session:
        browser_session.clear()
        browser_session['_user_id'] = str(user_id)
        browser_session['_fresh'] = True
        browser_session['login_2fa_verified'] = True


def test_only_administrators_can_see_captures_and_each_view_is_audited(ctx):
    capture_id = _make_capture(ctx)
    image_url, list_url = f'/admin/capturas/{capture_id}/imagen', '/admin/capturas'

    assert ctx['client'].get(image_url).status_code == 403          # el propio cliente tampoco
    assert ctx['client'].get(list_url).status_code == 403

    anonymous = ctx['app'].test_client()
    for url in (image_url, list_url):
        response = anonymous.get(url)
        assert response.status_code == 302 and '/login' in response.headers['Location']

    _login_as(ctx['client'], ctx['ids']['admin'])
    listing = ctx['client'].get(list_url)
    assert listing.status_code == 200 and listing.headers['Cache-Control'] == 'no-store'
    assert f'/admin/capturas/{capture_id}/imagen' in listing.get_data(as_text=True)
    image = ctx['client'].get(image_url)
    assert image.status_code == 200 and image.data == fake_jpeg()
    assert image.mimetype == 'image/jpeg'
    assert 'no-store' in image.headers['Cache-Control'] and image.headers['X-Content-Type-Options'] == 'nosniff'
    assert ctx['client'].get('/admin/capturas/99999/imagen').status_code == 404
    with ctx['app'].app_context():
        actions = {row.action for row in AuditLog.query.filter_by(module='PRIVACIDAD').all()}
        assert {'CAPTURE_LIST_VIEWED', 'CAPTURE_IMAGE_VIEWED'} <= actions


def test_no_public_url_serves_the_image(ctx):
    _make_capture(ctx)
    anonymous = ctx['app'].test_client()
    for url in ('/static/captures/1.jpg', '/static/images/captures/1.jpg', '/captures/1', '/capturas/1/imagen'):
        assert anonymous.get(url).status_code in (302, 404)


def test_sql_errors_do_not_print_bound_parameters_such_as_photo_bytes():
    assert create_app().config['SQLALCHEMY_ENGINE_OPTIONS']['hide_parameters'] is True


# ───────── Conservación ─────────
def test_purge_deletes_only_captures_older_than_the_retention_period(ctx):
    code = _request_code(ctx)
    assert _confirm(ctx, code).status_code == 302
    with ctx['app'].app_context():
        user = User.query.filter_by(username='demo').one()
        tx = Transaction.query.filter_by(idempotency_key=ctx['key']).one()
        recent = OperationCapture.query.one()
        recent.created_at = now_utc() - timedelta(days=89)
        db.session.add(OperationCapture(user_id=user.id, transaction_id=tx.id, image=fake_jpeg(), mime_type='image/jpeg',
                                        size_bytes=10, created_at=now_utc() - timedelta(days=91)))
        db.session.commit()
        assert purge_expired_captures(90) == 1
        remaining = OperationCapture.query.all()
        assert len(remaining) == 1 and remaining[0].id == recent.id
        assert Transaction.query.filter_by(id=tx.id).count() == 1     # la operación no se toca


def test_purge_command_supports_dry_run_and_days(ctx):
    code = _request_code(ctx)
    assert _confirm(ctx, code).status_code == 302
    with ctx['app'].app_context():
        OperationCapture.query.update({'created_at': now_utc() - timedelta(days=120)})
        db.session.commit()
    runner = ctx['app'].test_cli_runner()
    dry = runner.invoke(args=['purge-captures', '--dry-run'])
    assert dry.exit_code == 0 and '1 captura(s) superan los 90 días' in dry.output
    with ctx['app'].app_context():
        assert OperationCapture.query.count() == 1
    real = runner.invoke(args=['purge-captures'])
    assert real.exit_code == 0 and '1 captura(s) eliminadas' in real.output
    with ctx['app'].app_context():
        assert OperationCapture.query.count() == 0


# ───────── Validador de JPEG ─────────
def test_jpeg_dimensions_reads_the_declared_size():
    assert jpeg_dimensions(fake_jpeg(640, 480)) == (640, 480)
    assert jpeg_dimensions(b'') is None
    assert jpeg_dimensions(b'\xff\xd8\xff\xd9') is None
    assert jpeg_dimensions(fake_jpeg()[:-1]) is None


def test_read_capture_requires_a_named_file():
    class Empty:
        filename = ''
        stream = None

    with pytest.raises(ValueError, match='Activa la cámara'):
        read_capture(None, 300 * 1024)
    with pytest.raises(ValueError, match='Activa la cámara'):
        read_capture(Empty(), 300 * 1024)


# ───────── "Enviar código" no debe perder la operación (error real hallado en el navegador) ─────────
def test_after_send_code_the_page_keeps_the_operation_key_and_the_entered_data(ctx):
    _request_code(ctx)
    page = ctx['client'].get('/operation/transfer')
    html = page.get_data(as_text=True)
    assert _key(page) == ctx['key'], 'la clave debe sobrevivir al redirect: el código está ligado a ella'
    assert f'<option value="{ctx["ids"]["beneficiary"]}" selected>' in html
    assert 'value="12.34"' in html
    assert 'data-acknowledged' in html
    assert ctx['key'] not in ctx['page'].request.url      # nada sensible viaja en la URL


def test_the_operation_can_be_confirmed_with_the_key_from_the_page_loaded_after_send_code(ctx):
    code = _request_code(ctx)
    page = ctx['client'].get('/operation/transfer')           # lo que ve el usuario tras pulsar "Enviar código"
    key, token = _key(page), _csrf(page)
    accept_camera_notice(ctx['client'], token, 'transfer', key)
    response = ctx['client'].post('/operation/transfer', data={
        'csrf_token': token, 'idempotency_key': key, 'beneficiary_id': str(ctx['ids']['beneficiary']), 'amount': '12.34',
        'otp_code': code, 'form_duration_seconds': '9', 'camera_consent': 'yes', 'capture': capture_file()})
    assert response.status_code == 302 and '/receipt/' in response.headers['Location']
    with ctx['client'].session_transaction() as browser_session:
        assert 'pending_operation' not in browser_session   # se limpia al completarse


def test_a_fresh_visit_without_a_pending_code_gets_a_new_key_and_an_empty_form(ctx):
    page = ctx['client'].get('/operation/payment')
    assert _key(page) != ctx['key']
    assert 'value="12.34"' not in page.get_data(as_text=True)


def test_pending_payment_data_is_restored_from_the_session_not_the_url(ctx):
    page = ctx['client'].get('/operation/payment')
    key, token = _key(page), _csrf(page)
    ctx['client'].post('/operation/payment/send-code', data={'csrf_token': token, 'idempotency_key': key, 'amount': '7.5', 'service': 'Internet'})
    after = ctx['client'].get('/operation/payment')
    html = after.get_data(as_text=True)
    assert _key(after) == key
    assert '<option selected>Internet</option>' in html and 'value="7.50"' in html
    assert 'Internet' not in after.request.url
