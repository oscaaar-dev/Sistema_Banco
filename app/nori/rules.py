"""Respuestas de Nori por reglas (palabras clave) sobre lo que Norte realmente ofrece.

Cada texto describe funciones que existen en el proyecto o repite lo que ya dicen sus páginas públicas; no se
mencionan tasas, productos, plazos ni procedimientos que Norte no tenga. Los enlaces se resuelven con ``url_for``
a partir de una lista cerrada de páginas públicas, así nunca apuntan fuera del sitio.
"""
from flask import current_app, url_for

from .safety import normalize

# Páginas oficiales a las que Nori puede enviar (etiqueta, endpoint).
PAGES={
    'login':('Banca por Internet','main.login'),
    'register':('Abrir cuenta','main.register'),
    'forgot':('Recuperar contraseña','main.forgot_password'),
    'personas':('Personas','main.personas'),
    'herramientas':('Herramientas','main.herramientas'),
    'seguridad':('Seguridad','main.seguridad'),
    'acerca':('Acerca de Norte','main.acerca'),
    'terminos':('Términos','main.terms'),
    'privacidad':('Privacidad','main.privacy'),
}
OFFICIAL_PAGES=('personas','herramientas','seguridad','acerca')
DEFAULT_SUGGESTIONS=('¿Cómo ingreso?','¿Es seguro?','Abrir una cuenta','¿Qué es Norte?')

# name, palabras clave, respuesta (texto o función que la arma), mood (pose de Nori), páginas sugeridas
INTENTS=[
    {'name':'saludo','kw':['hola','buenas','buenos dias','buenas tardes','buenas noches','hey','que tal'],
     'reply':'¡Hola! Soy Nori, el asistente automático de Norte. Puedo orientarte sobre cómo ingresar, abrir una cuenta, '
             'la seguridad y las funciones de esta demostración. ¿Qué te gustaría saber?','mood':'hola','suggest':True},
    {'name':'gracias','kw':['gracias','muchas gracias','genial','excelente','perfecto'],
     'reply':'¡De nada! Si necesitas algo más, aquí estoy.','mood':'exito','suggest':True},
    {'name':'adios','kw':['adios','chau','chao','hasta luego','nos vemos'],
     'reply':'¡Hasta pronto! Que tengas un lindo día.','mood':'hola','suggest':True},
    {'name':'quien','kw':['quien eres','quien es nori','como te llamas','que eres','eres un bot','eres una persona','eres humano','eres real'],
     'reply':'Soy Nori, la mascota de Norte. Soy un asistente automático: respondo con reglas predefinidas sobre este sitio, '
             'no soy una persona ni uso inteligencia artificial generativa.','mood':'hola'},
    {'name':'ayuda','kw':['ayuda','ayudame','que puedes hacer','opciones','temas','menu'],
     'reply':'Puedo orientarte sobre: cómo ingresar, abrir una cuenta, recuperar tu contraseña, seguridad y cámara, '
             'las herramientas disponibles, transferencias y pagos de demostración, el estado de cuenta, horarios y datos legales.',
     'mood':'hola','pages':['herramientas','seguridad'],'suggest':True},
    {'name':'recuperar','kw':['olvide','olvido','recuperar','recuperar contrasena','restablecer','cambiar contrasena','contrasena'],
     'reply':'Si olvidaste tu contraseña, usa «¿Olvidaste tu contraseña?» en la pantalla de ingreso e indica tu correo. '
             'Nunca la compartas con nadie, tampoco conmigo.','mood':'seguridad','pages':['forgot']},
    {'name':'ingresar','kw':['ingresar','ingreso','login','entrar','iniciar sesion','acceder','banca por internet','como ingreso','como entro'],
     'reply':'Para ingresar usa «Banca por Internet»: escribe tu DNI o correo y tu contraseña. Luego Norte envía un código de '
             'seguridad al correo registrado, que debes escribir para entrar.','mood':'hola','pages':['login']},
    {'name':'abrir','kw':['abrir cuenta','abrir una cuenta','crear cuenta','crear una cuenta','registrar','registrarme','registro','nueva cuenta','ser cliente','quiero una cuenta'],
     'reply':'Para crear tu perfil usa «Abrir cuenta»: necesitas tu nombre completo, tu DNI, un correo y una contraseña que cumpla '
             'los requisitos. Norte es una demostración académica: la cuenta es ficticia y no mueve dinero real.','mood':'exito','pages':['register']},
    {'name':'camara','kw':['camara','foto','fotografia','webcam'],
     'reply':lambda: ('Para confirmar una transferencia, un pago o una recarga, Norte pide aceptar un aviso y tomar una foto con la cámara, '
                      f"además del código enviado a tu correo. La foto se conserva {current_app.config['CAPTURE_RETENTION_DAYS']} días."),
     'mood':'seguridad','pages':['privacidad']},
    {'name':'seguridad','kw':['seguro','seguridad','proteger','proteccion','fraude','estafa','verificacion','doble factor','codigo de seguridad'],
     'reply':'El ingreso tiene dos pasos: tu contraseña y un código enviado a tu correo. Para confirmar operaciones también se pide un '
             'código por correo y una foto con la cámara. Nunca compartas tu contraseña ni tus códigos.','mood':'seguridad','pages':['seguridad','privacidad']},
    {'name':'personas','kw':['personas','equipo','roles','administrador','soporte','gerencia'],
     'reply':'La sección Personas presenta los roles del equipo detrás de una plataforma como Norte (administración, gerencia, '
             'sistemas y soporte) y qué funciones tiene cada uno en esta demostración.','mood':'hola','pages':['personas']},
    {'name':'herramientas','kw':['herramientas','funciones','que ofrece','que puedo hacer','recorridos'],
     'reply':'En Herramientas ves los recorridos disponibles: resumen y cuentas, movimientos, transferencias internas, pagos y '
             'recargas, y tarjetas de muestra. Todo funciona con datos de demostración.','mood':'hola','pages':['herramientas']},
    {'name':'operaciones','kw':['transferir','transferencia','transferencias','contactos','agregar contacto','agregar un contacto','agregar contactos','agrego un contacto','agrego contacto','eliminar contacto','eliminar un contacto','elimino un contacto','enviar dinero','pagar','pagos','recarga','recargas','recargar'],
     'reply':'Dentro de tu cuenta puedes registrar transferencias a otros clientes de Norte (los eliges como contactos), pagos de '
             'servicios y recargas de demostración. Se confirman con un código por correo y la cámara, y no se mueve dinero real.',
     'mood':'hola','pages':['herramientas','login']},
    {'name':'cuenta','kw':['estado de cuenta','pdf','movimientos','saldo','mi cuenta','cci','numero de cuenta'],
     'reply':'En «Cuentas» ves el saldo y los datos de tu cuenta de demostración, y en «Movimientos» puedes filtrar tu historial y '
             'descargar el estado de cuenta en PDF. Los saldos y operaciones son ficticios.','mood':'hola','pages':['herramientas','login']},
    {'name':'horario','kw':['horario','horarios','atencion','contacto','contactar','contactarlos','telefono','hora de atencion','escribirles'],
     'reply':lambda: _contact_reply(),'mood':'hola'},
    {'name':'acerca','kw':['que es norte','acerca','es real','bcp','ficticio','academico','de verdad','dinero real','afiliado'],
     'reply':lambda: current_app.config['LEGAL_NOTICE'],'mood':'reposo','pages':['acerca','terminos']},
    {'name':'legal','kw':['terminos','condiciones','privacidad','datos personales','politica','cookies'],
     'reply':'Puedes leer cómo se tratan los datos personales y las reglas de uso en estas páginas.','mood':'reposo','pages':['privacidad','terminos']},
    {'name':'productos','kw':['tasa','tasas','interes','intereses','prestamo','prestamos','credito','creditos','deposito','plazo fijo','cts','dolares','sbs'],
     'reply':'Norte es una demostración académica y no ofrece productos financieros reales, así que no tengo tasas, intereses ni '
             'condiciones que informarte.','mood':'reposo','pages':['acerca','terminos']},
]

FALLBACK_REPLY=('No encontré información suficiente para responder eso con certeza. Para saber más, revisa las páginas oficiales '
                'de Norte: Personas, Herramientas, Seguridad o Acerca de Norte.')


def _contact_reply():
    config=current_app.config
    text=f"Atendemos {config['CONTACT_HOURS']}."
    if config.get('CONTACT_EMAIL'):
        text+=f" También puedes escribir a {config['CONTACT_EMAIL']}."
    return text


def _score(message,keywords):
    padded=f' {message} '
    total=0
    for keyword in keywords:
        phrase=normalize(keyword)
        if phrase and f' {phrase} ' in padded:
            total+=2+len(phrase.split())   # una frase exacta pesa más que una palabra suelta
    return total


def _links(keys):
    return [{'label':PAGES[key][0],'href':url_for(PAGES[key][1])} for key in keys]


def answer(message):
    """Respuesta de Nori: ``reply``, ``mood``, ``links`` y ``suggestions``. Sin evidencia suficiente, lo reconoce y orienta."""
    normalized=normalize(message)
    best,best_score=None,0
    for intent in INTENTS:
        score=_score(normalized,intent['kw'])
        if score>best_score:
            best,best_score=intent,score
    if best is None:
        return {'reply':FALLBACK_REPLY,'mood':'confundido','links':_links(OFFICIAL_PAGES),
                'suggestions':list(DEFAULT_SUGGESTIONS),'intent':None}
    reply=best['reply']() if callable(best['reply']) else best['reply']
    return {'reply':reply,'mood':best['mood'],'links':_links(best.get('pages',[])),
            'suggestions':list(DEFAULT_SUGGESTIONS) if best.get('suggest') else [],'intent':best['name']}
