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
