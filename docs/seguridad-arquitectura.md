# Seguridad, arquitectura y recuperación de contraseña

## 1. Arquitectura comprobada

| Capa | Tecnología real | Responsabilidad |
|---|---|---|
| Frontend | Plantillas Jinja + CSS/JS propios (sin frameworks) | Mostrar, recoger datos, validación visual, `fetch` al backend |
| Backend | Flask 3 (blueprint `main` y API `/api/v1`), Flask-Login, Flask-WTF (CSRF) | Autenticación, roles, reglas de negocio, validación, correo |
| Datos | SQLAlchemy sobre MySQL, PostgreSQL o SQLite; migraciones con Alembic | Persistencia con ORM parametrizado |

**Resultado de la auditoría (frontend ↔ base de datos):**
- Ningún archivo servido al navegador (`app/static`, `app/templates`) contiene cadenas de conexión, drivers, SQL, claves ni hashes. Lo comprueba `tests/test_security_architecture.py`.
- Los únicos `fetch` van al propio backend (`/operation-risk`, `/camera/consent`). No hay Supabase, Firebase ni integraciones de cliente que justificar.
- Las credenciales de base de datos y de correo solo salen de variables de entorno (`config.py`); `.env` está ignorado por Git.
- Todas las rutas privadas usan `@login_required`, comprueban el rol en el servidor (`_require_client`, admin, soporte) y filtran por propietario (`user_id=current_user.id`). Cambiar identificadores en la URL devuelve 404.
- CSRF activo en todos los formularios; cookies `HttpOnly` y `SameSite=Lax` (`Secure` con `COOKIE_SECURE`/producción); sin CORS abierto; errores sin trazas.

## 2. Política única de contraseña

Implementada en `app/services/password_policy.py` y usada por el registro y la recuperación (no existe cambio de contraseña desde la cuenta, así que no hay otro formulario).

- Entre 8 y 10 caracteres (ambos incluidos), con al menos una mayúscula `A-Z`, una minúscula `a-z`, un número `0-9` y un punto `.`.
- No se exigen otros símbolos y no se recorta ni se transforma nada (los espacios cuentan). Se rechazan caracteres de control.
- El navegador solo ayuda: las reglas se publican desde el servidor (`data-pattern`) y `password-policy.js` las evalúa; el servidor vuelve a validar siempre.
- Las cuentas existentes no se tocan: el inicio de sesión no aplica la política, solo la aplican las contraseñas nuevas.
- Hash: el mecanismo que ya usa el proyecto (`werkzeug.security`, scrypt). La longitud máxima de 10 limita la resistencia a la adivinación; se mantienen los límites de intentos, el bloqueo temporal y el código por correo.

## 3. Recuperación exclusiva para clientes

| Ruta | Método | Función |
|---|---|---|
| `/recuperar` | GET, POST | Solicitud por correo. Misma respuesta pública exista o no la cuenta, sea cliente o no, o esté limitada |
| `/recuperar/<token>` | GET, POST | Valida el enlace y fija la contraseña nueva |

- Token de 256 bits (`secrets.token_urlsafe(32)`); en la base solo se guarda su HMAC-SHA256. Caduca a los 30 minutos y es de un solo uso.
- Solo `role='CLIENTE'` con correo registrado. Se comprueba al emitir **y** al consumir: un token nunca cambia la clave de un administrador o soporte.
- Una solicitud nueva deja sin efecto las anteriores. Un intento rechazado por la política no consume el enlace.
- El consumo es atómico: `UPDATE ... WHERE used_at IS NULL AND expires_at > ahora` y el cambio de contraseña van en la misma transacción. Probado con dos hilos simultáneos.
- Tras el cambio: se invalidan los demás tokens y los códigos de acceso pendientes, y las sesiones abiertas dejan de valer (`users.password_changed_at` + marca en la cookie de sesión).
- Límites: 5 solicitudes por conexión y 3 correos por cuenta cada hora; todas cuentan, también las de cuentas inexistentes.
- Las páginas con token se sirven con `Cache-Control: no-store` y `Referrer-Policy: no-referrer`. Tokens y contraseñas no se registran en logs.

## 4. Base de datos y migración

Migración `d3a91c5e7f20_add_password_recovery`: crea `password_reset_tokens` (hash único, índices por usuario y caducidad, FK con borrado en cascada) y añade `users.password_changed_at` (nullable). No borra ni modifica datos.

```powershell
alembic upgrade head      # con DATABASE_URL apuntando a la base elegida
```

La aplicación **no** crea ni altera esquemas persistentes: en producción se detiene si falta la migración. Ejecútala solo con autorización para esa base.

## 5. Variables de entorno

| Variable | Obligatoria | Uso |
|---|---|---|
| `PUBLIC_BASE_URL` | En producción | Base `https://…` del enlace del correo. Sin ella en producción no se envían correos de recuperación (evita envenenar el enlace con un `Host` manipulado) |
| `EMAIL_TRANSPORT`, `SMTP_*` o `BREVO_API_KEY` + `SMTP_FROM` | Sí, para enviar | Mismo servicio de correo que el código de acceso |

Los valores de límites y caducidad (`PASSWORD_RESET_*`) están en `config.py`.

## 6. Cómo probarlo a mano

1. Con el correo configurado, abre `/register` y crea una cuenta con `Norte2026.`: la lista de requisitos se marca al escribir.
2. En `/login` pulsa «¿Olvidaste tu contraseña?», escribe el correo del cliente y revisa la bandeja.
3. Abre el enlace, elige una contraseña válida y vuelve a entrar por `/login` (llegará el código por correo).
4. Reutiliza el mismo enlace: debe mostrar «No pudimos validar el enlace».
5. Repite con el correo de un administrador o de soporte: la pantalla es idéntica y no llega ningún correo.

## 7. Pendiente y límites

- El inicio de sesión es **compartido** por todos los roles: el enlace «¿Olvidaste tu contraseña?» se ve en esa pantalla, pero el servidor restringe el flujo a clientes.
- Enviar el correo toma más tiempo para cuentas de cliente existentes que para correos desconocidos (diferencia medible). Mitigarlo exigiría enviar en segundo plano.
- `seed_service.py` conserva contraseñas de demostración por defecto (`DEMO_PASSWORD`, `ADMIN_PASSWORD`); no cumplen la política nueva y son solo para pruebas. Defínelas por variable de entorno fuera de ellas.
- No se probó la entrega real por SMTP/Brevo: falta configurar el servicio de correo en tu entorno.

## 8. Nori, asistente de las páginas públicas

Resumen de arquitectura y seguridad; el detalle de mantenimiento está en `docs/nori.md`.

| Tema | Comportamiento comprobado por pruebas |
|---|---|
| Alcance | Solo páginas públicas; nunca en acceso, registro, recuperación, errores ni panel privado |
| Respuestas | Reglas predefinidas + recuperación documental local (TF-IDF) sobre documentos autorizados; sin IA generativa ni servicios externos ni dependencias nuevas |
| CSRF | Activo: `GET /api/nori/csrf` entrega el token y `POST /api/nori/chat` lo exige en `X-CSRFToken` |
| Entradas | Máximo 500 caracteres; DNI, teléfonos, tarjetas, códigos y claves se rechazan y no se procesan ni se guardan |
| Límites | 20 mensajes por minuto y 300 por día por conexión (IP con hash); respaldo en memoria si la base falla |
| Memoria | Últimos 8 turnos en `sessionStorage`, 30 min de inactividad; el servidor valida el historial y lo descarta si el *scope* (HMAC de la sesión y la identidad) no coincide: cerrar o iniciar sesión, otro usuario o cookie borrada |
| Persistencia | Ninguna conversación en base de datos ni archivos. Solo una fila de auditoría `NORI_CHAT` por mensaje (fecha, hora, hash de IP), vencida a las 24 h y purgada al recibir solicitudes o con `purge-nori-audit` |
| Documentos | Se tratan como no confiables (sin secretos, correos, instrucciones a un asistente, URL externas ni enlaces simbólicos); nunca se indexan `data/`, `docs/` ni `.env` |

Límites conocidos: la recuperación es léxica; solo responde en español; la expiración por inactividad la aplica el navegador.

## 9. Lista de verificación de producción (SECRET_KEY, workers e IP del cliente)

Las variables obligatorias y su formato están en el README (apartado de despliegue) y en la sección 5; aquí solo se anota lo que
afecta a varios workers de Gunicorn (el `Procfile` usa `--workers 2`) y a Nori.

| Comprobación | Estado |
|---|---|
| `APP_ENV=production` en el servicio | Debe confirmarse en el entorno real. Fuera de producción la aplicación sigue arrancando sin `SECRET_KEY` |
| `SECRET_KEY` definida, **estable y con el mismo valor en todos los workers y réplicas** | En producción el arranque se detiene si falta, está vacía o solo tiene espacios (`config.py`; probado en `tests/test_production_readiness.py`). Si no está definida fuera de producción, cada proceso genera una clave propia y se registra un aviso; con varios workers las sesiones, el CSRF y el *scope* de Nori no coincidirían. Una clave de menos de 32 caracteres genera un aviso (sin revelar la clave) pero no impide el arranque |
| `DATABASE_URL` | Obligatoria en producción; sin ella el arranque se detiene |
| Dos workers comparten sesión | Probado con dos aplicaciones y la misma clave (cookie, token CSRF y *scope* compatibles; con claves distintas, no). **No se ha ejecutado Gunicorn con varios workers** |
| Identificación de la IP detrás del proxy | **Pendiente de verificar en Railway** (ver abajo) |

### Identificación de la IP del cliente: riesgo pendiente

`_request_ip_hash` (`app/routes/__init__.py`) decide la IP con la que se aplican los límites del login con OTP, de Nori y de otros
formularios:

* Si existe `RAILWAY_ENVIRONMENT_NAME`, usa la cabecera `X-Real-IP` tal como llega y, si falta o está vacía, `remote_addr`.
* En cualquier otro caso usa solo `remote_addr` y no tiene en cuenta `X-Real-IP` ni `X-Forwarded-For`. Detrás de un proxy que no sea
  Railway, `remote_addr` sería la dirección del proxy y todos los visitantes compartirían el mismo límite.
* No se usa `ProxyFix` ni `X-Forwarded-For`.

La documentación pública de Railway indica `X-Real-IP` como la cabecera con la IP remota del cliente, pero no dice si el proxy la
sobrescribe siempre ni si un cliente puede enviarla falsificada. **Mientras eso no se compruebe, la confianza en `X-Real-IP` no está
validada**: si el proxy no la sobrescribe, quien rote esa cabecera podría eludir los límites. Las pruebas actuales solo describen qué
cabecera se usa, no demuestran que sea segura.

Evidencia necesaria para cerrar el riesgo (todas **pendientes; no se ha ejecutado ninguna**):

1. En un entorno de **staging** propio (nunca producción), enviar con `curl`, conservando la cookie de sesión y el token de
   `GET /api/nori/csrf`, más mensajes por minuto que el límite (`NORI_RATE_LIMIT_MESSAGES`, 20 por defecto) a `POST /api/nori/chat`,
   cambiando en cada petición el valor de `X-Real-IP`.
2. Si pasado el límite se recibe `429`, el proxy sobrescribe la cabecera y la confianza actual es adecuada. Si las peticiones
   siguen respondiendo `200`, la cabecera es falsificable: no usarla tal cual y revisar la estrategia (por ejemplo, tomar la
   dirección que el proxy añade al final de `X-Forwarded-For`) con la topología real confirmada por Railway.
3. Anotar fecha, entorno y resultado en `PROGRESS.md`.
4. Con Gunicorn en Linux: arrancar con 2 workers y la misma `SECRET_KEY`, iniciar sesión y navegar varias veces seguidas para
   comprobar que la sesión no se pierde, y confirmar que los registros de arranque no muestran el aviso de clave temporal.
