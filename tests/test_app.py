from app import create_app
from app.models import db, User, Account, LoginChallenge


def _csrf_token(response):
    import re
    return re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()


def _login(client, monkeypatch):
    import app.routes as routes_module

    sent_codes=[]
    monkeypatch.setattr(routes_module,'send_login_code',lambda email,code:sent_codes.append((email,code)))
    with client.application.app_context():
        user=User.query.filter_by(username='demo').first()
        user.email=user.email or 'demo@example.test'
        expected_email=user.email
        db.session.commit()

    login_page=client.get('/login')
    response=client.post('/login',data={
        'username': 'demo',
        'password': 'Demo123!',
        'csrf_token': _csrf_token(login_page),
    })
    assert response.status_code==302
    assert response.headers['Location'].endswith('/login/verify')
    assert sent_codes and sent_codes[0][0]==expected_email

    verify_page=client.get('/login/verify')
    response=client.post('/login/verify',data={
        'code':sent_codes[-1][1],
        'csrf_token':_csrf_token(verify_page),
    })
    assert response.status_code==302
    return sent_codes[-1][1]


def _registration_form(client):
    return client.get('/register')


def _register(client, full_name, dni, email, password='Norte9.Aa', confirmation=None):
    response=_registration_form(client)
    return client.post('/register',data={
        'csrf_token':_csrf_token(response),
        'full_name':full_name,
        'dni':dni,
        'email':email,
        'password':password,
        'password_confirmation':confirmation if confirmation is not None else password,
    })


def test_app_starts_and_seeded_users_exist():
    app=create_app(); app.config['TESTING']=True
    with app.app_context():
        assert User.query.filter_by(username='demo').first() is not None
        assert Account.query.count() >= 1


def test_public_homepage_links_to_existing_login_and_registration():
    app=create_app(); app.config['TESTING']=True
    response=app.test_client().get('/')

    assert response.status_code==200
    assert 'Tu día avanza.'.encode() in response.data
    assert b'href="/login"' in response.data
    assert b'href="/register"' in response.data
    assert 'Operaciones desde tu espacio digital'.encode() in response.data
    assert 'Norte es un proyecto académico de demostración, independiente y sin relación con el BCP'.encode() in response.data
    assert b'SBS' not in response.data
    assert b'home-parallax' in response.data
    assert response.data.count(b'aria-roledescription="diapositiva"') == 3
    assert b'data-hero-carousel' in response.data
    assert b'id="promociones"' in response.data
    assert response.data.count(b'data-parallax-card') == 5
    assert 'Tu recorrido por Norte es así de simple'.encode() in response.data
    assert b'data-open-cookies' in response.data
    assert b'data-open-help' in response.data
    for path in (b'/personas', b'/herramientas', b'/seguridad', b'/acerca'):
        assert b'href="' + path + b'"' in response.data


def test_movements_screen_filters_dates_and_paginates(monkeypatch):
    from datetime import datetime, timedelta
    from decimal import Decimal
    from app.models import Transaction

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        account=Account.query.filter_by(user_id=user.id).one()
        anchor=datetime(2026,1,1)
        for index in range(22):
            db.session.add(Transaction(
                account_id=account.id,operation_type='PAGO',description=f'Pago de prueba {index}',
                amount=Decimal('-1.00'),balance_after=Decimal('100.00'),status='COMPLETADA',
                created_at=anchor+timedelta(days=index),
            ))
        db.session.commit()

    first=client.get('/movements')
    assert first.status_code==200
    assert b'25 registros' in first.data
    assert 'Página 1 de 2'.encode() in first.data
    second=client.get('/movements?page=2&type=PAGO&from=2026-01-01&to=2026-01-22')
    assert second.status_code==200
    assert b'Pago de prueba 0' in second.data
    assert 'Página 2 de 2'.encode() in second.data
    assert client.get('/movements?type=DESCONOCIDO').status_code==400
    assert client.get('/movements?from=2026-02-30').status_code==400
    assert client.get('/movements?from=2026-02-20&to=2026-02-01').status_code==400


def test_dashboard_and_movements_show_all_owned_active_accounts(monkeypatch):
    from decimal import Decimal
    from app.models import Transaction

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        account=Account(user_id=user.id,account_type='Cuenta secundaria',number='001234567811',
                        balance=Decimal('123.45'),currency='PEN')
        db.session.add(account); db.session.flush()
        db.session.add(Transaction(account_id=account.id,operation_type='INGRESO',description='Abono cuenta secundaria',
                                   amount=Decimal('123.45'),balance_after=Decimal('123.45'),status='COMPLETADA'))
        db.session.commit()
        account_id=account.id
    dashboard=client.get('/dashboard')
    assert dashboard.status_code==200
    assert b'123.45' in dashboard.data
    assert b'CUENTA SECUNDARIA' in dashboard.data
    default_history=client.get('/movements')
    selected_history=client.get(f'/movements?account_id={account_id}')
    assert b'Abono cuenta secundaria' not in default_history.data
    assert b'Abono cuenta secundaria' in selected_history.data
    assert selected_history.data.count(b'name="account_id"')==1
    assert client.get('/movements?account_id=999999').status_code==404


def test_card_locking_is_csrf_protected_audited_and_owner_scoped():
    from decimal import Decimal
    from app.models import AuditLog, BankCard

    app=create_app(); app.config['TESTING']=True
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        user_id=user.id
        card=BankCard.query.filter_by(user_id=user_id).first()
        card_id=card.id
        card.status='Activa'
        other=User(username='card-owner-other',full_name='Titular de prueba',role='CLIENTE')
        other.set_password('TestOnlyPass123!')
        db.session.add(other)
        db.session.flush()
        account=Account(user_id=other.id,account_type='Ahorros',number='900000000000000099',balance=Decimal('10.00'))
        db.session.add(account)
        db.session.flush()
        foreign_card=BankCard(user_id=other.id,account_id=account.id,last4='0000',status='Bloqueada')
        db.session.add(foreign_card)
        db.session.commit()
        foreign_card_id=foreign_card.id
    client=app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id']=str(user_id)
        browser_session['_fresh']=True
        browser_session['login_2fa_verified']=True
    page=client.get('/cards')
    response=client.post(f'/cards/{card_id}/status',data={
        'csrf_token':_csrf_token(page),'status':'Bloqueada',
    })
    assert response.status_code==302
    with app.app_context():
        assert BankCard.query.filter_by(id=card_id).one().status=='Bloqueada'
        assert AuditLog.query.filter_by(user_id=user_id,action='CARD_LOCKED').count()==1
    other_update=client.post(f'/cards/{foreign_card_id}/status',data={
        'csrf_token':_csrf_token(client.get('/cards')),'status':'Activa',
    })
    assert other_update.status_code==404


def test_account_statement_pdf_is_date_filtered_and_owner_scoped(monkeypatch):
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        account=Account.query.filter_by(user_id=user.id).one()
        account_id=account.id
        other=User(username='statement-owner-other',full_name='Titular de prueba',role='CLIENTE')
        other.set_password('TestOnlyPass123!')
        db.session.add(other)
        db.session.flush()
        other_account=Account(user_id=other.id,account_type='Ahorros',number='900000000000000098')
        db.session.add(other_account)
        db.session.commit()
        other_account_id=other_account.id

    response=client.get(f'/statements/{account_id}.pdf?from=2025-09-30&to=2025-09-30')
    assert response.status_code==200
    assert response.mimetype=='application/pdf'
    assert response.headers['Cache-Control']=='no-store'
    assert response.data.startswith(b'%PDF-1.4')
    assert response.data.endswith(b'%%EOF')
    assert b'NORTE.' in response.data
    assert b'ni mueve dinero real' in response.data
    assert b'SBS' not in response.data
    assert b'Saldo de apertura de muestra' in response.data
    assert b'Servicio del hogar' not in response.data
    assert client.get(f'/statements/{other_account_id}.pdf').status_code==404
    assert client.get(f'/statements/{account_id}.pdf?from=2026-02-30').status_code==400


def test_support_role_has_aggregated_view_and_no_client_or_admin_routes():
    app=create_app(); app.config['TESTING']=True
    with app.app_context():
        support_user=User(username='support-agent',full_name='Agente de soporte',email='support@example.test',role='SOPORTE')
        support_user.set_password('SupportOnly123!')
        db.session.add(support_user)
        db.session.commit()
        support_id=support_user.id
        client_user=User.query.filter_by(username='demo').one()
        client_id=client_user.id
    support_client=app.test_client()
    with support_client.session_transaction() as browser_session:
        browser_session['_user_id']=str(support_id)
        browser_session['_fresh']=True
        browser_session['login_2fa_verified']=True
    panel=support_client.get('/support')
    assert panel.status_code==200
    assert b'Centro de soporte' in panel.data
    assert b'support@example.test' not in panel.data
    assert support_client.get('/admin').status_code==403
    assert support_client.get('/dashboard').status_code==403
    assert support_client.get('/cards').status_code==403
    assert support_client.get('/operation/payment').status_code==403
    assert support_client.get('/api/v1/accounts').status_code==403
    assert support_client.get('/login').headers['Location'].endswith('/support')

    client=app.test_client()
    with client.session_transaction() as browser_session:
        browser_session['_user_id']=str(client_id)
        browser_session['_fresh']=True
        browser_session['login_2fa_verified']=True
    assert client.get('/support').status_code==403


def test_internal_transfer_uses_email_otp_and_updates_both_owned_accounts(monkeypatch):
    import re
    from decimal import Decimal
    from app.models import InteractionFeatures, Transaction

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        source=Account.query.filter_by(user_id=user.id).one()
        destination=Account(user_id=user.id,account_type='Cuenta de ahorros',number='001234567899',
                            balance=Decimal('50.00'),currency='PEN')
        db.session.add(destination); db.session.commit()
        source_id=source.id; destination_id=destination.id

    delivered=[]
    import app.routes as routes_module
    monkeypatch.setattr(routes_module,'send_operation_code',lambda email,code,summary:delivered.append((email,code,summary)))
    page=client.get('/internal-transfer')
    key=re.search(rb'name="idempotency_key" value="([^"]+)"',page.data).group(1).decode()
    base_data={
        'csrf_token':_csrf_token(page),'idempotency_key':key,
        'source_account_id':str(source_id),'destination_account_id':str(destination_id),
        'amount':'10.00','form_duration_seconds':'12',
    }
    requested=client.post('/internal-transfer',data={**base_data,'action':'send_code'})
    assert requested.status_code==200
    assert delivered and '10.00' in delivered[0][2]
    confirmed=client.post('/internal-transfer',data={**base_data,'action':'confirm','otp_code':delivered[0][1]})
    assert confirmed.status_code==302
    assert '/receipt/' in confirmed.headers['Location']
    with app.app_context():
        source=Account.query.filter_by(id=source_id).one()
        destination=Account.query.filter_by(id=destination_id).one()
        assert source.balance==Decimal('8530.50')
        assert destination.balance==Decimal('60.00')
        paired=Transaction.query.filter_by(idempotency_key=key).order_by(Transaction.account_id).all()
        assert len(paired)==2
        assert sorted(item.operation_type for item in paired)==['INGRESO','TRANSFERENCIA']
        assert InteractionFeatures.query.count()==1
    retry=client.post('/internal-transfer',data={**base_data,'action':'confirm','otp_code':delivered[0][1]})
    assert retry.status_code==302
    with app.app_context():
        assert Transaction.query.filter_by(idempotency_key=key).count()==2


def test_public_information_topics_have_separate_pages():
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    expected={
        '/personas': 'Administrador del sistema',
        '/herramientas': 'Transferencias internas',
        '/seguridad': 'Acceso verificado por correo',
        '/acerca': 'Por qué existe',
    }

    for path, heading in expected.items():
        response=client.get(path)
        assert response.status_code==200
        assert heading.encode() in response.data
        assert b'class="info-page"' in response.data
        assert b'data-reveal' in response.data
        assert b'class="info-card" data-reveal' in response.data
        assert b'class="info-card-action"' in response.data
        assert b'href="/personas"' in response.data
        assert b'href="/herramientas"' in response.data
        assert b'href="/seguridad"' in response.data
        assert b'href="/acerca"' in response.data

    assert b'Norte no procesa dinero real ni pagos externos' in client.get('/seguridad').data
    assert b'independiente y sin relaci' in client.get('/acerca').data


def test_public_cards_link_to_relevant_routes_and_personas_are_not_fake_testimonials():
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    personas=client.get('/personas').data
    tools=client.get('/herramientas').data
    security=client.get('/seguridad').data

    assert b'perfiles por funci' in personas
    assert b'Gerencia es una referencia informativa' in personas
    assert b'href="/register"' in personas
    assert b'href="/acerca"' in personas
    assert b'Banca por Internet' in personas
    assert b'Abrir cuenta' in personas
    assert b'href="/login"' in tools
    assert b'href="/terminos"' in security
    assert b'href="/privacidad"' in security


def test_terms_and_privacy_pages_publish_legal_notice_and_links():
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()

    for path,heading in (
        ('/terminos','Términos y condiciones de uso'),
        ('/privacidad','Privacidad y tratamiento de datos personales'),
    ):
        response=client.get(path)
        assert response.status_code==200
        assert heading.encode() in response.data
        assert 'Norte es un proyecto académico de demostración, independiente y sin relación con el BCP'.encode() in response.data
        assert b'SBS' not in response.data
        assert b'href="/terminos"' in response.data
        assert b'href="/privacidad"' in response.data


def test_error_pages_keep_the_norte_brand():
    app=create_app(); app.config['TESTING']=True
    response=app.test_client().get('/ruta-inexistente')

    assert response.status_code==404
    assert b'Norte' in response.data
    assert b'No encontramos esta p' in response.data


def test_authenticated_user_can_still_open_public_homepage():
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    with app.app_context():
        user=User.query.filter_by(username='admin').first()
        user_id=user.id
    with client.session_transaction() as browser_session:
        browser_session['_user_id']=str(user_id)
        browser_session['_fresh']=True
        browser_session['login_2fa_verified']=True

    response=client.get('/')

    assert response.status_code==200
    assert 'Tu día avanza.'.encode() in response.data
    assert b'href="/login"' in response.data


def test_customer_can_register_and_login_with_email_otp(monkeypatch):
    import secrets
    import app.routes as routes_module

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    suffix=secrets.token_hex(4)
    dni=f'{10000000+secrets.randbelow(90000000)}'
    email=f'cliente{suffix}@example.test'
    response=_register(client,'Óscar Quiñones',dni,email)

    assert response.status_code==302
    assert response.headers['Location'].endswith('/login')
    with app.app_context():
        user=User.query.filter_by(dni=dni).first()
        assert user is not None
        assert user.username==dni
        assert user.email==email
        assert user.role=='CLIENTE'
        assert user.check_password('Norte9.Aa')
        account=Account.query.filter_by(user_id=user.id).first()
        assert account is not None
        assert account.balance==0

    sent_codes=[]
    monkeypatch.setattr(routes_module,'send_login_code',lambda recipient,code:sent_codes.append((recipient,code)))
    login_page=client.get('/login')
    response=client.post('/login',data={
        'username':email,
        'password':'Norte9.Aa',
        'csrf_token':_csrf_token(login_page),
    })
    assert response.status_code==302
    assert response.headers['Location'].endswith('/login/verify')
    assert sent_codes[0][0]==email
    verification_page=client.get('/login/verify')
    response=client.post('/login/verify',data={
        'code':sent_codes[0][1],
        'csrf_token':_csrf_token(verification_page),
    })
    assert response.status_code==302
    assert client.get('/dashboard').status_code==200
    with client.session_transaction() as browser_session:
        assert browser_session.permanent is True
        assert browser_session['login_2fa_verified'] is True
    assert app.permanent_session_lifetime.total_seconds()==1800


def test_login_otp_rate_limit_is_shared_per_ip_across_customer_accounts(monkeypatch):
    import secrets
    import app.routes as routes_module

    app=create_app(); app.config.update(TESTING=True,LOGIN_CODE_RATE_LIMIT_IP=1)
    client=app.test_client()
    users=[]
    with app.app_context():
        for index in range(2):
            username=f'ratelimit-{secrets.token_hex(4)}-{index}'
            user=User(username=username,full_name='Cliente Prueba',email=f'{username}@example.test',role='CLIENTE')
            user.set_password('Norte9.Aa')
            db.session.add(user)
            users.append((username,user.email))
        db.session.commit()

    delivered=[]
    monkeypatch.setattr(routes_module,'send_login_code',lambda recipient,code:delivered.append(recipient))
    first_page=client.get('/login')
    first=client.post('/login',data={
        'username':users[0][0],'password':'Norte9.Aa',
        'csrf_token':_csrf_token(first_page),
    })
    second_client=app.test_client()
    second_page=second_client.get('/login')
    second=second_client.post('/login',data={
        'username':users[1][0],'password':'Norte9.Aa',
        'csrf_token':_csrf_token(second_page),
    })

    assert first.status_code==302
    assert second.status_code==429
    assert delivered==[users[0][1]]


def test_registration_rate_limit_is_audited_without_plaintext_ip():
    import secrets
    import app.routes as routes_module
    from app.models import AuditLog

    app=create_app(); app.config.update(TESTING=True,REGISTRATION_RATE_LIMIT_IP=1)
    remote_ip='198.51.100.24'
    with app.test_request_context('/register',environ_base={'REMOTE_ADDR':remote_ip}):
        ip_hash=routes_module._request_ip_hash('registration-ip')
    with app.app_context():
        existing_user=User.query.filter_by(username='demo').one()
        db.session.add(AuditLog(
            user_id=existing_user.id,action='CUSTOMER_REGISTERED',module='ACCESO',
            result='COMPLETADO',ip_local=ip_hash,
        ))
        db.session.commit()

    client=app.test_client()
    form=client.get('/register',environ_overrides={'REMOTE_ADDR':remote_ip})
    response=client.post('/register',data={
        'csrf_token':_csrf_token(form),'full_name':'Cliente Prueba','dni':'12345678',
        'email':f'{secrets.token_hex(4)}@example.test','password':'Norte9.Aa',
        'password_confirmation':'Norte9.Aa',
    },environ_overrides={'REMOTE_ADDR':remote_ip})

    assert response.status_code==429
    assert len(ip_hash)==64
    assert remote_ip not in ip_hash


def test_http_responses_include_baseline_security_headers():
    app=create_app(); app.config['TESTING']=True
    response=app.test_client().get('/')

    assert response.headers['X-Content-Type-Options']=='nosniff'
    assert response.headers['X-Frame-Options']=='DENY'
    assert response.headers['Referrer-Policy']=='strict-origin-when-cross-origin'
    assert response.headers['Permissions-Policy']=='camera=(self), geolocation=(), microphone=()'


def test_customer_can_start_login_using_dni(monkeypatch):
    import secrets
    import app.routes as routes_module

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    suffix=secrets.token_hex(4)
    dni=f'{10000000+secrets.randbelow(90000000)}'
    email=f'dni{suffix}@example.test'
    assert _register(client,'Cliente Prueba',dni,email).status_code==302

    sent_codes=[]
    monkeypatch.setattr(routes_module,'send_login_code',lambda recipient,code:sent_codes.append((recipient,code)))
    login_page=client.get('/login')
    response=client.post('/login',data={
        'username':dni,
        'password':'Norte9.Aa',
        'csrf_token':_csrf_token(login_page),
    })

    assert response.status_code==302
    assert response.headers['Location'].endswith('/login/verify')
    assert sent_codes[0][0]==email


def test_password_login_is_temporarily_blocked_after_five_failures():
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    login_page=client.get('/login')
    token=_csrf_token(login_page)

    for _ in range(5):
        response=client.post('/login',data={
            'username':'demo',
            'password':'WrongPassword9!',
            'csrf_token':token,
        })
        assert response.status_code==200
    blocked=client.post('/login',data={
        'username':'demo',
        'password':'Demo123!',
        'csrf_token':token,
    })

    assert blocked.status_code==429
    assert 'Usuario o clave incorrectos'.encode() in blocked.data
    from app.models import LoginAttemptLimit
    with app.app_context():
        attempt=LoginAttemptLimit.query.one()
        assert len(attempt.identifier_hash)==64
        assert attempt.failure_count==5
        assert attempt.locked_until is not None
        assert 'demo' not in attempt.identifier_hash


def test_registration_rejects_passwords_outside_policy():
    import secrets

    for password in ('Norte_S9A','NorteSeguro123','Short1A','NoSpaces 9A.'):
        app=create_app(); app.config['TESTING']=True
        client=app.test_client()
        suffix=secrets.token_hex(4)
        dni=f'{10000000+secrets.randbelow(90000000)}'
        response=_register(client,'Cliente Prueba',dni,f'policy{suffix}@example.test',password)

        assert response.status_code==400
        assert 'La contraseña debe tener entre 8 y 10 caracteres'.encode() in response.data
        with app.app_context():
            assert User.query.filter_by(dni=dni).first() is None


def test_registration_accepts_spanish_letters_in_password():
    import secrets

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    password='Ñandú9.aB'
    response=_register(client,'Cliente Prueba','87654321',f'accent{secrets.token_hex(4)}@example.test',password)
    assert response.status_code==302


def test_registration_rejects_invalid_dni_email_and_mismatched_passwords():
    import secrets

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    suffix=secrets.token_hex(4)
    response=_register(
        client,'Cliente Prueba','1234',f'invalid{suffix}@',
        password='Norte9.Aa',confirmation='Norte8.Aa',
    )

    assert response.status_code==400
    assert 'DNI debe contener exactamente 8 dígitos'.encode() in response.data
    assert 'correo electrónico válida'.encode() in response.data
    assert 'contraseñas no coinciden'.encode() in response.data


def test_registration_rejects_duplicate_dni_or_email():
    import secrets

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    suffix=secrets.token_hex(4)
    dni=f'{10000000+secrets.randbelow(90000000)}'
    email=f'duplicate{suffix}@example.test'
    assert _register(client,'Cliente Prueba',dni,email).status_code==302

    response=_register(client,'Otra Persona',dni,email)

    assert response.status_code==409
    assert 'Ya existe una cuenta con ese DNI o correo'.encode() in response.data
    with app.app_context():
        assert User.query.filter_by(dni=dni).count()==1


def test_login_page_and_dashboard(monkeypatch):
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    response=client.get('/login')
    assert response.status_code==200
    assert b'Demo123!' not in response.data
    assert b'Admin123!' not in response.data
    assert b'Banca demo' not in response.data
    _login(client,monkeypatch)
    response=client.get('/dashboard')
    assert response.status_code==200
    assert b'Hola' in response.data


def test_login_requires_email_code_and_rejects_wrong_code(monkeypatch):
    import app.routes as routes_module

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    sent_codes=[]
    monkeypatch.setattr(routes_module,'send_login_code',lambda email,code:sent_codes.append(code))
    with app.app_context():
        user=User.query.filter_by(username='demo').first()
        user.email='demo@example.test'
        db.session.commit()

    login_page=client.get('/login')
    response=client.post('/login',data={
        'username':'demo',
        'password':'Demo123!',
        'csrf_token':_csrf_token(login_page),
    })
    assert response.status_code==302
    assert response.headers['Location'].endswith('/login/verify')
    assert client.get('/dashboard').status_code==302

    verify_page=client.get('/login/verify')
    response=client.post('/login/verify',data={'code':'000000','csrf_token':_csrf_token(verify_page)})
    assert response.status_code==200
    assert 'Código incorrecto'.encode() in response.data
    assert client.get('/dashboard').status_code==302

    response=client.post('/login/verify',data={
        'code':sent_codes[0],
        'csrf_token':_csrf_token(client.get('/login/verify')),
    })
    assert response.status_code==302
    assert client.get('/dashboard').status_code==200
    with app.app_context():
        assert LoginChallenge.query.count()==0


def test_login_stays_blocked_when_smtp_delivery_fails(monkeypatch):
    import app.routes as routes_module
    from app.services.email_service import EmailDeliveryError

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    def fail_delivery(email,code):
        raise EmailDeliveryError('SMTP unavailable')
    monkeypatch.setattr(routes_module,'send_login_code',fail_delivery)
    with app.app_context():
        user=User.query.filter_by(username='demo').first()
        user.email='demo@example.test'
        db.session.commit()
    login_page=client.get('/login')
    response=client.post('/login',data={
        'username':'demo',
        'password':'Demo123!',
        'csrf_token':_csrf_token(login_page),
    },follow_redirects=True)

    assert response.status_code==200
    assert b'No se pudo enviar el c\xc3\xb3digo de seguridad' in response.data
    assert client.get('/dashboard').status_code==302
    with app.app_context():
        assert LoginChallenge.query.count()==0


def test_legacy_authenticated_session_must_complete_email_code():
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    with app.app_context():
        user=User.query.filter_by(username='demo').first()
        user_id=user.id
    with client.session_transaction() as browser_session:
        browser_session['_user_id']=str(user_id)
        browser_session['_fresh']=True

    response=client.get('/dashboard')

    assert response.status_code==302
    assert response.headers['Location'].endswith('/login')
    with client.session_transaction() as browser_session:
        assert '_user_id' not in browser_session


def test_login_code_is_limited_to_five_attempts(monkeypatch):
    import app.routes as routes_module

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    sent_codes=[]
    monkeypatch.setattr(routes_module,'send_login_code',lambda email,code:sent_codes.append(code))
    with app.app_context():
        user=User.query.filter_by(username='demo').first()
        user.email='demo@example.test'
        db.session.commit()
    login_page=client.get('/login')
    client.post('/login',data={
        'username':'demo',
        'password':'Demo123!',
        'csrf_token':_csrf_token(login_page),
    })
    verify_page=client.get('/login/verify')
    wrong_code='000000' if sent_codes[0]!='000000' else '000001'

    for attempt in range(5):
        response=client.post('/login/verify',data={
            'code':wrong_code,
            'csrf_token':_csrf_token(verify_page),
        })
    assert response.status_code==302
    assert response.headers['Location'].endswith('/login')
    assert client.get('/dashboard').status_code==302
    with app.app_context():
        assert LoginChallenge.query.count()==0


def test_operation_risk_preview_returns_score_with_limited_history(monkeypatch):
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)

    response=client.get('/operation-risk?kind=transfer&amount=5000')

    assert response.status_code==200
    result=response.get_json()
    assert result['score'] == 35
    assert result['level'] == 'Moderado'
    assert result['enough_history'] is False
    assert any('Historial limitado' in reason for reason in result['reasons'])

def test_operation_requires_email_otp_even_after_camera_consent(monkeypatch):
    import re

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)

    operation_page=client.get('/operation/payment')
    operation_token=_csrf_token(operation_page)
    idempotency_key=re.search(rb'name="idempotency_key" value="([^"]+)"',operation_page.data).group(1).decode()
    consent=client.post('/camera/consent',json={
        'version':'2026-10-v1','kind':'payment','idempotency_key':idempotency_key,
    },headers={'X-CSRFToken':operation_token})
    assert consent.status_code==200
    response=client.post('/operation/payment',data={
        'csrf_token':operation_token,
        'idempotency_key':idempotency_key,
        'camera_consent':'yes',
        'form_duration_seconds':'30',
        'service':'Agua',
        'amount':'100',
    })

    assert response.status_code==200
    assert b'Solicita un nuevo c' in response.data
    from app.models import Transaction
    with app.app_context():
        assert Transaction.query.filter_by(operation_type='PAGO',is_seed=False).count()==0

def test_camera_frame_endpoint_is_disabled_and_does_not_store_state(monkeypatch):
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    operation_page=client.get('/operation/payment')
    response=client.post('/camera/analyze',data={
        'csrf_token':_csrf_token(operation_page),
        'camera_consent':'yes',
        'frame':'data:image/jpeg;base64,AA==',
    })
    assert response.status_code==410
    assert 'deshabilitado' in response.get_json()['error']
    with client.session_transaction() as browser_session:
        assert 'camera_state' not in browser_session


def test_interaction_clustering_does_not_read_legacy_facial_metrics():
    from app.ai.interaction_model import FEATURES, cluster_interactions

    class LegacySession:
        def __init__(self, index):
            self.duration_seconds=10+index
            self.errors=index % 3
            self.steps=3+index
            self.back_actions=index % 2

        @property
        def facial_analysis(self):
            raise AssertionError('El agrupador no debe leer análisis faciales históricos')

    sessions=[LegacySession(index) for index in range(5)]
    assert 'face_detection_rate' not in FEATURES
    assert len(cluster_interactions(sessions))==len(sessions)


def test_receipt_does_not_expose_legacy_facial_analysis_or_cluster(monkeypatch):
    from decimal import Decimal
    from app.models import Account, FacialAnalysis, InteractionSession, MLResult, Transaction, User

    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        account=Account.query.filter_by(user_id=user.id).first()
        interaction=InteractionSession(user_id=user.id,operation_type='PAGO',duration_seconds=18,
                                      steps=5,errors=0,back_actions=0,face_detected=True)
        db.session.add(interaction); db.session.flush()
        db.session.add(FacialAnalysis(session_id=interaction.id,face_detected=True,detection_confidence=99.9))
        db.session.add(MLResult(session_id=interaction.id,label='LEGACY FACE CLUSTER',method='legacy'))
        tx=Transaction(account_id=account.id,session_id=interaction.id,operation_type='PAGO',
                       description='Pago histórico de prueba',amount=Decimal('-1.00'),
                       balance_after=account.balance,status='COMPLETADA')
        db.session.add(tx); db.session.commit()
        transaction_id=tx.id

    response=client.get(f'/receipt/{transaction_id}')
    assert response.status_code==200
    assert b'Pago hist' in response.data
    assert b'Verificaci' not in response.data
    assert b'99.9' not in response.data
    assert b'LEGACY FACE CLUSTER' not in response.data


def test_camera_consent_records_only_user_version_and_timestamp(monkeypatch):
    import re
    from app.models import AuditLog, User
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    page=client.get('/operation/payment')
    token=_csrf_token(page)
    key=re.search(rb'data-operation-key="([^"]+)"',page.data).group(1).decode()
    response=client.post('/camera/consent',json={
        'version':'2026-10-v1','kind':'payment','idempotency_key':key,
    },headers={'X-CSRFToken':token})
    assert response.status_code==200
    assert response.get_json()=={'recorded':True,'version':'2026-10-v1'}
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        consent=AuditLog.query.filter_by(user_id=user.id,module='PRIVACIDAD').one()
        assert consent.action=='CAMERA_CONSENT_ACCEPTED_2026_10_V1_PAYMENT'
        assert consent.created_at is not None
        assert consent.ip_local is None
        assert AuditLog.query.filter_by(module='PRIVACIDAD').count()==1
    with client.session_transaction() as browser_session:
        assert browser_session['camera_consent_receipt']=={
            'version':'2026-10-v1','kind':'payment','idempotency_key':key,
        }


def test_invalid_camera_consent_version_is_not_recorded(monkeypatch):
    import re
    from app.models import AuditLog
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    page=client.get('/operation/payment')
    token=_csrf_token(page)
    key=re.search(rb'data-operation-key="([^"]+)"',page.data).group(1).decode()
    response=client.post('/camera/consent',json={
        'version':'old','kind':'payment','idempotency_key':key,
    },headers={'X-CSRFToken':token})
    assert response.status_code==400
    with app.app_context():
        assert AuditLog.query.filter_by(module='PRIVACIDAD').count()==0

def test_payment_can_be_confirmed_by_email_otp_without_camera(monkeypatch):
    import re
    from decimal import Decimal
    import app.routes as routes_module
    from app.models import Account, FacialAnalysis, InteractionSession, OperationChallenge, Transaction, User

    sent=[]
    monkeypatch.setattr(routes_module,'send_operation_code',lambda email,code,details:sent.append((email,code,details)))
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    page=client.get('/operation/payment')
    token=_csrf_token(page)
    key=re.search(rb'name="idempotency_key" value="([^"]+)"',page.data).group(1).decode()
    request_code=client.post('/operation/payment/send-code',data={
        'csrf_token':token,'idempotency_key':key,'amount':'25.50','service':'Agua',
    })
    assert request_code.status_code==302
    assert sent and 'Agua' in sent[0][2]
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        challenge=OperationChallenge.query.filter_by(user_id=user.id,operation_type='PAGO').one()
        assert challenge.beneficiary_id is None
        assert challenge.details_hash
        assert sent[0][1] not in challenge.code_hash

    response=client.post('/operation/payment',data={
        'csrf_token':token,'idempotency_key':key,'amount':'25.50','service':'Agua',
        'otp_code':sent[0][1],'form_duration_seconds':'12',
    })
    assert response.status_code==302
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        challenge=OperationChallenge.query.filter_by(user_id=user.id,operation_type='PAGO').one()
        tx=Transaction.query.filter_by(idempotency_key=key).one()
        session_record=InteractionSession.query.filter_by(id=tx.session_id).one()
        assert challenge.consumed_at is not None
        assert session_record.consent_given is False
        assert session_record.face_detected is False
        assert FacialAnalysis.query.count()==0
        assert tx.amount==Decimal('-25.50')

def test_operation_otp_is_bound_to_service(monkeypatch):
    import re
    import app.routes as routes_module
    from app.models import OperationChallenge, Transaction, User

    sent=[]
    monkeypatch.setattr(routes_module,'send_operation_code',lambda email,code,details:sent.append(code))
    app=create_app(); app.config['TESTING']=True
    client=app.test_client()
    _login(client,monkeypatch)
    page=client.get('/operation/payment')
    token=_csrf_token(page)
    key=re.search(rb'name="idempotency_key" value="([^"]+)"',page.data).group(1).decode()
    client.post('/operation/payment/send-code',data={
        'csrf_token':token,'idempotency_key':key,'amount':'25.50','service':'Agua',
    })
    response=client.post('/operation/payment',data={
        'csrf_token':token,'idempotency_key':key,'amount':'25.50','service':'Internet',
        'otp_code':sent[0],'form_duration_seconds':'12',
    })
    assert response.status_code==200
    assert 'Los datos cambiaron'.encode() in response.data
    with app.app_context():
        user=User.query.filter_by(username='demo').one()
        assert OperationChallenge.query.filter_by(user_id=user.id,operation_type='PAGO').one().consumed_at is not None
        assert Transaction.query.filter_by(idempotency_key=key).count()==0
