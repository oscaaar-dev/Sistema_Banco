"""Respuestas de Nori por reglas (palabras clave) sobre lo que Norte realmente ofrece.

Cada texto describe funciones que existen en el proyecto o repite lo que ya dicen sus páginas públicas; no se
mencionan tasas, productos, plazos ni procedimientos que Norte no tenga. Los enlaces se resuelven con ``url_for``
a partir de una lista cerrada de páginas públicas, así nunca apuntan fuera del sitio.
"""
from flask import current_app, url_for

from ..services.validation import MAX_AMOUNT
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

# Detalle adicional por tema (para «más información»). Cada dato sale de las páginas públicas o del código de Norte.
def _minutes(seconds):
    return max(1, round(seconds / 60))


def _more_operaciones():
    from ..routes import OPERATION_CODE_TTL
    return (f'Cada operación se confirma con un código enviado a tu correo, que vence en {_minutes(OPERATION_CODE_TTL.total_seconds())} minutos, '
            f'y con la cámara. El monto máximo por operación es S/ {MAX_AMOUNT:,.2f}.')


MORE={
    'ingresar':lambda: ('Si es tu primera vez, primero crea tu perfil con «Abrir cuenta». El código de seguridad llega al correo registrado y vence en '
                        f"{_minutes(current_app.config['LOGIN_CODE_TTL_SECONDS'])} minutos; si olvidaste tu contraseña usa «¿Olvidaste tu contraseña?»."),
    'abrir':'La contraseña debe cumplir los requisitos que se muestran mientras la escribes. Al terminar, ingresa con tu DNI o correo y Norte te enviará un código de seguridad.',
    'recuperar':lambda: ('El enlace de recuperación llega al correo registrado y vence en '
                         f"{_minutes(current_app.config['PASSWORD_RESET_TTL_SECONDS'])} minutos. Por seguridad, la respuesta es la misma exista o no la cuenta."),
    'seguridad':'Norte no es una entidad financiera, así que sus controles no sustituyen los de un banco real. Usa datos de prueba y no reutilices contraseñas de otros servicios.',
    'camara':'Si no aceptas el aviso o no activas la cámara, la operación no se completa y puedes cancelarla. La foto no identifica personas ni reemplaza el código del correo.',
    'operaciones':_more_operaciones,
    'cuenta':'El estado de cuenta en PDF respeta los filtros de fechas y de tipo que elijas en Movimientos.',
    'herramientas':'Cada recorrido funciona con datos de demostración: nada de lo que ves se conecta a bancos ni a empresas de servicios.',
    'personas':'Gerencia es un perfil de referencia: la demostración aún no tiene un rol ni un tablero gerencial independiente. Soporte ve conteos y eventos técnicos, sin datos personales.',
    'acerca':'Norte es independiente y no está afiliado, patrocinado ni respaldado por el BCP.',
}

# Frases de seguimiento (ya normalizadas). Solo se usan si el mensaje no coincide con ningún tema por sí mismo.
MORE_PHRASES=('mas','mas informacion','mas detalles','dame mas detalles','cuentame mas','explicame','explicame mas','y eso','como asi','detalles','amplia')
REPEAT_PHRASES=('repite','repitelo','repite eso','otra vez','no entendi','no te entendi','que dijiste','puedes repetir')
NO_CONTEXT_REPLY=('¿Sobre qué tema quieres saber? Puedo ayudarte con el ingreso, abrir una cuenta, seguridad, cámara, herramientas, '
                  'transferencias o el estado de cuenta.')


def _text(value):
    return value() if callable(value) else value


def reply_index():
    """Texto normalizado de cada respuesta conocida de Nori -> (tema, variante) para entender el historial recibido.

    La variante es ``'base'`` (primera respuesta) o ``'more'`` (la ampliación de «más información»)."""
    index={}
    for intent in INTENTS:
        index[normalize(_text(intent['reply']))]=(intent['name'],'base')
        if intent['name'] in MORE:
            index[normalize(_text(MORE[intent['name']]))]=(intent['name'],'more')
    return index


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


def _is_phrase(normalized,phrases):
    """El mensaje es una de las frases (o empieza por ella y es corto): «más información», «repítelo», etc."""
    words=normalized.split()
    return len(words)<=5 and any(normalized==phrase or normalized.startswith(phrase+' ') for phrase in phrases)


def _topic_reply(intent,reply=None):
    return {'reply':reply if reply is not None else _text(intent['reply']),'mood':intent['mood'],'links':_links(intent.get('pages',[])),
            'suggestions':list(DEFAULT_SUGGESTIONS) if intent.get('suggest') else [],'intent':intent['name']}


def is_menu_suggestion(message):
    """El mensaje es una de las sugerencias del menú de Nori (sus respuestas son curadas y se conservan tal cual)."""
    return normalize(message) in {normalize(label) for label in DEFAULT_SUGGESTIONS}


def best_match(message):
    """(regla, puntaje) que mejor coincide con el mensaje; ``(None, 0)`` si ninguna palabra clave aparece."""
    normalized=normalize(message)
    best,best_score=None,0
    for intent in INTENTS:
        score=_score(normalized,intent['kw'])
        if score>best_score:
            best,best_score=intent,score
    return best,best_score


def follow_up_kind(message):
    """``'more'`` («más información»), ``'repeat'`` («repítelo») o ``None`` si el mensaje no es de seguimiento."""
    normalized=normalize(message)
    if _is_phrase(normalized,MORE_PHRASES):
        return 'more'
    if _is_phrase(normalized,REPEAT_PHRASES):
        return 'repeat'
    return None


def answer(message,last_intent=None):
    """Respuesta de Nori: ``reply``, ``mood``, ``links`` y ``suggestions``.

    ``last_intent`` es ``(tema, variante)`` de la respuesta anterior (de la memoria de la conversación) y solo sirve para
    preguntas de seguimiento. Sin evidencia suficiente, Nori lo reconoce y orienta a las páginas oficiales.
    """
    normalized=normalize(message)
    best,best_score=None,0
    for intent in INTENTS:
        score=_score(normalized,intent['kw'])
        if score>best_score:
            best,best_score=intent,score
    if best is not None:
        return _topic_reply(best)
    last_name,last_variant=last_intent if last_intent else (None,None)
    previous=next((intent for intent in INTENTS if intent['name']==last_name),None)
    if _is_phrase(normalized,MORE_PHRASES):
        if previous is None:
            return {'reply':NO_CONTEXT_REPLY,'mood':'hola','links':[],'suggestions':list(DEFAULT_SUGGESTIONS),'intent':None}
        if previous['name'] in MORE:
            return _topic_reply(previous,_text(MORE[previous['name']]))
        return {'reply':'Sobre ese tema no tengo más detalles. Puedes revisar las páginas oficiales de Norte.','mood':'reposo',
                'links':_links(OFFICIAL_PAGES),'suggestions':[],'intent':previous['name']}
    if _is_phrase(normalized,REPEAT_PHRASES):
        if previous is None:
            return {'reply':NO_CONTEXT_REPLY,'mood':'hola','links':[],'suggestions':list(DEFAULT_SUGGESTIONS),'intent':None}
        if last_variant=='more' and previous['name'] in MORE:
            return _topic_reply(previous,_text(MORE[previous['name']]))
        return _topic_reply(previous)
    return {'reply':FALLBACK_REPLY,'mood':'confundido','links':_links(OFFICIAL_PAGES),
            'suggestions':list(DEFAULT_SUGGESTIONS),'intent':None}
