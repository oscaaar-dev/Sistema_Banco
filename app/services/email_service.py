import smtplib
import ssl
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from email.message import EmailMessage

from flask import current_app


class EmailDeliveryError(Exception):
    """Raised when SMTP is unavailable or not fully configured."""


def send_login_code(recipient, code):
    _send_security_code(recipient, code, 'Tu código de acceso · Norte',
                        'iniciar sesión en Norte')


def send_operation_code(recipient, code, details):
    _send_security_code(recipient, code, 'Confirma tu operación · Norte',
                        'confirmar esta operación en Norte: '+details)


def send_password_reset_link(recipient, link, minutes):
    """Envía el enlace de recuperación. El enlace contiene el token: nunca se registra en logs."""
    body=('Recibimos una solicitud para restablecer la contraseña de tu cuenta de Norte.\n\n'
          f'Abre este enlace para elegir una contraseña nueva (válido por {minutes} minutos y de un solo uso):\n{link}\n\n'
          'Si no fuiste tú, ignora este mensaje: tu contraseña actual no cambiará.\n'
          + current_app.config['LEGAL_NOTICE'])
    _send_email(recipient, 'Restablece tu contraseña · Norte', body)


def send_admin_activation_link(recipient, link, hours):
    """Enlace para que una cuenta administrativa nueva defina su contraseña. Contiene el token: nunca se registra en logs."""
    body=('Se creó una cuenta administrativa de Norte a tu nombre.\n\n'
          f'Abre este enlace para definir tu contraseña (válido por {hours} horas y de un solo uso):\n{link}\n\n'
          'Después deberá activar tu cuenta un Administrador General. Si no esperabas este mensaje, ignóralo: nadie podrá entrar con esta cuenta.\n'
          + current_app.config['LEGAL_NOTICE'])
    _send_email(recipient, 'Activa tu cuenta administrativa · Norte', body)


def _send_security_code(recipient, code, subject, purpose):
    body=(f'Tu código de seguridad para {purpose} es: {code}\n\n'
          'Vence en 5 minutos. Si no solicitaste este código, ignora este mensaje.\n'
          + current_app.config['LEGAL_NOTICE'])
    _send_email(recipient, subject, body)


def _send_email(recipient, subject, body):
    if current_app.config.get('EMAIL_TRANSPORT')=='brevo_api':
        _send_with_brevo_api(recipient, subject, body)
        return

    host=current_app.config.get('SMTP_HOST')
    port=current_app.config.get('SMTP_PORT')
    username=current_app.config.get('SMTP_USERNAME')
    password=current_app.config.get('SMTP_PASSWORD')
    sender=current_app.config.get('SMTP_FROM') or username
    use_tls=current_app.config.get('SMTP_USE_TLS',True)
    use_ssl=current_app.config.get('SMTP_USE_SSL',False)

    if (
        not all((host,port,username,password,sender))
        or not isinstance(port,int)
        or not 1<=port<=65535
        or use_tls==use_ssl
    ):
        raise EmailDeliveryError('SMTP está incompleto o la configuración de cifrado no es válida.')

    message=EmailMessage()
    message['Subject']=subject
    message['From']=sender
    message['To']=recipient
    message.set_content(body)

    try:
        context=ssl.create_default_context()
        if use_ssl:
            with smtplib.SMTP_SSL(host,port,timeout=10,context=context) as server:
                server.login(username,password)
                server.send_message(message)
        else:
            with smtplib.SMTP(host,port,timeout=10) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(username,password)
                server.send_message(message)
    except (OSError,smtplib.SMTPException) as error:
        raise EmailDeliveryError('No se pudo entregar el correo.') from error


def _send_with_brevo_api(recipient,subject,body):
    api_key=current_app.config.get('BREVO_API_KEY')
    sender=current_app.config.get('SMTP_FROM')
    if not api_key or not sender:
        raise EmailDeliveryError('La configuración de Brevo API está incompleta.')

    payload={
        'sender':{'email':sender,'name':'Norte'},
        'to':[{'email':recipient}],
        'subject':subject,
        'textContent':body,
    }
    if current_app.config.get('BREVO_SANDBOX'):
        payload['headers']={'X-Sib-Sandbox':'drop'}
    request=Request(
        'https://api.brevo.com/v3/smtp/email',
        data=json.dumps(payload).encode('utf-8'),
        headers={'accept':'application/json','api-key':api_key,'content-type':'application/json'},
        method='POST',
    )
    try:
        with urlopen(request,timeout=15) as response:
            if not 200<=response.status<300:
                raise EmailDeliveryError('Brevo rechazó el envío del correo.')
    except (HTTPError,URLError,OSError) as error:
        raise EmailDeliveryError('No se pudo entregar el correo.') from error
