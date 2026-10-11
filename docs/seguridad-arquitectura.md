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
