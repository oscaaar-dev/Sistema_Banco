---
title: Acceso a Norte
url: /login
status: verificado
sources: app/routes/__init__.py (login, verify_login, resend_login_code); config.py (LOGIN_CODE_*); app/routes/recovery.py
---

## Ingreso con contraseña y código
Para ingresar a Norte escribes tu DNI, tu usuario o tu correo y tu contraseña. Si son correctos, Norte envía un código de seguridad de 6 dígitos al correo registrado y debes escribirlo para entrar. El acceso siempre requiere ambos pasos.

## Vigencia e intentos del código de acceso
El código de acceso vence a los 5 minutos y admite hasta 5 intentos. Si no llega, puedes pedir otro después de 60 segundos. Si se supera el límite de códigos enviados, se bloquea temporalmente la solicitud de nuevos códigos.

## Bloqueo por intentos fallidos
Después de 5 intentos fallidos de usuario y contraseña en 15 minutos, el acceso queda bloqueado durante 15 minutos. El mensaje de error es el mismo si el usuario no existe o la contraseña es incorrecta.

## Duración de la sesión
Después de ingresar, la sesión dura 30 minutos. Pasado ese tiempo debes volver a ingresar. Si cambias tu contraseña, las sesiones anteriores se cierran.

## Recuperar la contraseña
Si olvidaste tu contraseña, usa la opción «¿Olvidaste tu contraseña?» de la pantalla de ingreso e indica tu correo. La respuesta es la misma exista o no la cuenta. Si corresponde a una cuenta de cliente, recibes un enlace por correo que vence a los 30 minutos y te permite fijar una contraseña nueva.
