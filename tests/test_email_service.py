import pytest

from app import create_app
from app.services import email_service
from app.services.email_service import EmailDeliveryError


def test_login_code_email_uses_starttls_and_contains_code(monkeypatch):
    app=create_app()
    app.config.update(
        SMTP_HOST='smtp.example.test',
        SMTP_PORT=587,
        SMTP_USERNAME='sender@example.test',
        SMTP_PASSWORD='app-password',
        SMTP_FROM='sender@example.test',
        SMTP_USE_TLS=True,
        SMTP_USE_SSL=False,
    )
    sent={}

    class FakeSMTP:
        def __init__(self,host,port,timeout):
            sent['connection']=(host,port,timeout)

        def __enter__(self):
            return self

        def __exit__(self,*args):
            return False

        def ehlo(self):
            sent['ehlo']=True

        def starttls(self,context):
            sent['tls']=context

        def login(self,username,password):
            sent['credentials']=(username,password)

        def send_message(self,message):
            sent['message']=message

    monkeypatch.setattr(email_service.smtplib,'SMTP',FakeSMTP)
    with app.app_context():
        email_service.send_login_code('user@example.test','123456')

    assert sent['connection']==('smtp.example.test',587,10)
    assert sent['tls'] is not None
    assert sent['credentials']==('sender@example.test','app-password')
    assert sent['message']['To']=='user@example.test'
    assert '123456' in sent['message'].get_content()


def test_login_code_email_rejects_unconfigured_smtp():
    app=create_app()
    app.config.update(
        SMTP_HOST=None,
        SMTP_PORT=587,
        SMTP_USERNAME=None,
        SMTP_PASSWORD=None,
        SMTP_FROM=None,
        SMTP_USE_TLS=True,
        SMTP_USE_SSL=False,
    )
    with app.app_context(),pytest.raises(EmailDeliveryError):
        email_service.send_login_code('user@example.test','123456')


def test_transfer_confirmation_uses_brevo_adapter_and_includes_details(monkeypatch):
    app=create_app()
    app.config.update(EMAIL_TRANSPORT='brevo_api',BREVO_API_KEY='test-api-key',SMTP_FROM='sender@example.test')
    sent={}
    monkeypatch.setattr(email_service,'_send_with_brevo_api',
                        lambda recipient,subject,body:sent.update(recipient=recipient,subject=subject,body=body))

    with app.app_context():
        email_service.send_operation_code('user@example.test','123456','S/ 12.34 a Cliente ···· 7890')

    assert sent['recipient']=='user@example.test'
    assert sent['subject']=='Confirma tu operación · Norte'
    assert '123456' in sent['body']
    assert 'S/ 12.34 a Cliente' in sent['body']
    assert 'Vence en 5 minutos' in sent['body']


def test_brevo_sandbox_marks_request_to_drop_email_without_delivery(monkeypatch):
    app=create_app()
    app.config.update(
        EMAIL_TRANSPORT='brevo_api',BREVO_API_KEY='test-api-key',
        BREVO_SANDBOX=True,SMTP_FROM='sender@example.test',
    )
    sent={}

    class FakeResponse:
        status=201

        def __enter__(self):
            return self

        def __exit__(self,*args):
            return False

    def fake_urlopen(request,timeout):
        import json
        sent['payload']=json.loads(request.data)
        sent['timeout']=timeout
        return FakeResponse()

    monkeypatch.setattr(email_service,'urlopen',fake_urlopen)
    with app.app_context():
        email_service.send_login_code('user@example.test','123456')

    assert sent['payload']['headers']=={'X-Sib-Sandbox':'drop'}
    assert sent['timeout']==15


def test_live_staging_email_is_limited_to_approved_test_recipient(monkeypatch):
    app=create_app()
    app.config.update(
        EMAIL_TRANSPORT='brevo_api',BREVO_API_KEY='test-api-key',
        BREVO_SANDBOX=False,BREVO_TEST_RECIPIENT='oscarquinonesvallejos@gmail.com',
        RAILWAY_ENVIRONMENT_NAME='staging',SMTP_FROM='sender@example.test',
    )
    sent=[]

    class FakeResponse:
        status=201

        def __enter__(self):
            return self

        def __exit__(self,*args):
            return False

    def fake_urlopen(request,timeout):
        import json
        sent.append(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr(email_service,'urlopen',fake_urlopen)
    with app.app_context():
        with pytest.raises(EmailDeliveryError,match='destinatario autorizado'):
            email_service.send_login_code('other@example.test','123456')
        email_service.send_login_code('OSCARQUINONESVALLEJOS@gmail.com','123456')

    assert len(sent)==1
    assert sent[0]['to']==[{'email':'OSCARQUINONESVALLEJOS@gmail.com'}]
    assert 'headers' not in sent[0]
