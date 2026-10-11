# Norte — Sistema Inteligente de Operaciones Bancarias (simulación académica)

Aplicación web educativa construida con Flask y SQLAlchemy. Tiene autenticación con hash de contraseñas, roles, cuentas ficticias, transferencias/pagos/recargas simuladas, movimientos, tarjetas, auditoría y métricas básicas de interacción. **Norte es un proyecto académico de demostración, independiente y sin relación con el BCP. Sus productos, saldos y operaciones son ficticios; no presta servicios financieros ni mueve dinero real.**

## Ejecutar en Windows

1. Instala Python 3.11 o 3.12.
2. Abre PowerShell en esta carpeta y ejecuta:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:APP_ENV = 'testing'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:SECRET_KEY = 'solo-para-pruebas-locales-cambiar'
$env:PORT = '5001'
python run.py
```

3. Abre http://127.0.0.1:5001

La ruta `/` muestra la portada pública de Norte. Sus botones de banca digital y creación de cuenta enlazan al flujo existente de autenticación y registro (`/login` y `/register`). Después de autenticarte, las rutas protegidas mantienen el dashboard y las funciones actuales.

Este comando arranca en modo de pruebas con SQLite en memoria: las tablas y los datos sintéticos de demostración solo existen mientras el proceso está activo y se descartan al detenerlo. No apunta a MySQL ni cambia bases persistentes. Para usar la base MySQL dedicada y transferir datos locales, revisa cuidadosamente la guía siguiente; su inicializador migra datos y no debe ejecutarse sin confirmar el origen y el destino.

## Base MySQL independiente del proyecto

Este proyecto puede usar su propia instancia de MySQL 8.4, independiente de otros proyectos y de MySQL Workbench: escucha solo en `127.0.0.1:3307`. La instancia MySQL existente en el puerto 3306 no se modifica.

La instalación local ya creada conserva la carpeta `%LOCALAPPDATA%\NorteBcpMySQL\data`, el esquema `norte_bcp` y los usuarios `norte_bcp_app` y `norte_bcp_viewer`. Son identificadores técnicos heredados, no nombres de marca. Se mantienen para no desconectar la base existente; renombrarlos exige una migración planificada y no forma parte de esta fase.

Para inicializarla y migrar los datos locales de SQLite una sola vez, abre PowerShell en esta carpeta y ejecuta:

```powershell
python -m scripts.bootstrap_dedicated_mysql
```

El comando inicializa el directorio de datos nuevo, crea usuarios y contraseñas aleatorias, guarda las credenciales solo en `.env` y en el directorio local del usuario, copia las 14 tablas y verifica los conteos. El archivo SQLite original no se borra. Revisa la salida para confirmar que la migración terminó.

Para ver la base en MySQL Workbench, crea una conexión con Host `127.0.0.1`, Port `3307`, User `norte_bcp_viewer` y Default Schema `norte_bcp`. La contraseña de solo lectura está en `%LOCALAPPDATA%\NorteBcpMySQL\workbench-login.txt`; no la compartas. No uses la contraseña de la aplicación ni la conexión del otro proyecto.

Si se necesita rotar otra vez la contraseña de la app, ejecuta `python -m scripts.repair_dedicated_mysql_access` y reinicia Flask.

Después de reiniciar Windows, inicia esta instancia con:

```powershell
.\scripts\start_dedicated_mysql.ps1
```

Si la carpeta de datos ya existe, el inicializador aborta y no reemplaza nada.

## Accesos de demostración

- Cliente: `demo` / `Demo123!`
- Administración: `admin` / `Admin123!`

Cambia estas claves antes de una demostración compartida. Las claves demo solo son apropiadas para el entorno local.

## Registro de clientes demo

El formulario `/register` crea una cuenta ficticia con nombre completo, DNI peruano de 8 dígitos, correo único y contraseña. El DNI y el correo también pueden usarse para iniciar sesión; el acceso sigue requiriendo el código OTP por correo configurado arriba. Cada cuenta nueva recibe una cuenta de ahorros demo con saldo inicial S/ 0.00. No se valida el DNI con RENIEC ni se crea una cuenta bancaria real. La contraseña debe tener entre 8 y 10 caracteres, al menos una mayúscula, una minúscula y un número. Se aceptan letras (incluida ñ y vocales con tilde), números y puntos (`.`), sin espacios.

## Segundo factor por correo (OTP)

El inicio de sesión requiere usuario y contraseña, y luego un código de seis dígitos enviado al correo asociado, guardado en el registro del usuario en la base de datos. El código vence en 5 minutos, admite hasta 5 intentos y solo se puede volver a enviar una vez transcurrido el intervalo de espera de 60 segundos. Para desarrollo local, Brevo puede usarse por SMTP (`SMTP_HOST=smtp-relay.brevo.com`, puerto 587, STARTTLS, login y clave SMTP). Para Railway configura `EMAIL_TRANSPORT=brevo_api`, `BREVO_API_KEY` con la clave API de Brevo y `SMTP_FROM` con un remitente verificado; la app envía por HTTPS y no requiere SMTP saliente. `SMTP_FROM` es el remitente; los correos de demo/admin y clientes se conservan en la tabla `users`, no como configuración de producción. Configura también una `SECRET_KEY` aleatoria y persistente. Los secretos solo van en `.env` local o en las variables privadas del hosting, nunca en el repositorio. Sin un transporte de correo correctamente configurado, el acceso se deniega en lugar de omitir el segundo factor.

Para staging, `BREVO_SANDBOX=true` usa el modo sandbox oficial de Brevo: valida la solicitud pero descarta el correo, por lo que no se recibe el OTP. Con `BREVO_SANDBOX=false`, cualquier cliente registrado con correo puede recibir su código. Para reducir abusos, se limita el OTP por IP (5 cada 15 minutos) y por cuenta (3 cada 15 minutos, además de 60 segundos entre reenvíos); el registro se limita a 5 cuentas por IP cada hora. Las IP se guardan como HMAC en la auditoría, no en texto plano. Railway proporciona `X-Real-IP` para identificar la IP del cliente. Estos límites reducen el abuso automatizado, pero no sustituyen CAPTCHA/antibot ni una política de monitoreo para un servicio financiero real.

Este OTP es una medida demostrativa para la aplicación académica; no debe tratarse como protección suficiente para operaciones financieras reales.

## Incluye

- Dashboard responsive con saldo demo, accesos rápidos, tarjetas y movimientos.
- Transferencia a beneficiarios demo, pagos de servicios y recargas.
- Comprobantes y validación de saldo/montos.
- Administración con sesiones y auditoría.
- CSRF en formularios y sesiones de Flask protegidas.
- Modelo de datos creado desde SQLAlchemy para SQLite y MySQL.
- Vista previa de cámara opcional y local; las operaciones se confirman mediante el código OTP enviado al correo.
- Clustering K-Means exploratorio sobre métricas de interacción en `app/ai/interaction_model.py`.
- Indicador demostrativo, basado en reglas transparentes, sobre montos y frecuencia de operaciones; no decide ni bloquea operaciones.
- Escenarios de prueba sintéticos del indicador en `tests/fixtures/operation_risk_scenarios.csv`; se usan para validar niveles, no para entrenar un detector de fraude.

## Alcance honesto de IA y privacidad

La cámara solo muestra una vista previa local opcional en el navegador. La interfaz registra el consentimiento explícito (usuario, versión del aviso y fecha), pero **no transmite fotogramas, no analiza ni identifica rostros y no guarda imágenes ni nuevos datos biométricos**. La cámara no autentica ni es necesaria para operar; cada operación sigue requiriendo el OTP de correo. El endpoint de análisis de cámara está deshabilitado. El esquema conserva por compatibilidad el modelo histórico de análisis facial; esta actualización no inspecciona ni elimina registros antiguos, pero el código nuevo no consulta sus métricas faciales para agrupar interacciones ni para mostrar comprobantes. K-Means agrupa señales operativas no faciales (duración, errores, pasos y retrocesos) únicamente cuando hay suficientes sesiones variadas; sus etiquetas son exploratorias, no diagnósticos ni evaluaciones personales. El indicador de riesgo de operación calcula un puntaje de alerta de 0 a 100 basado en reglas: monto frente al saldo, comparación con operaciones del mismo tipo, intensidad de operaciones recientes y duración del formulario (3 minutos o más suma 15 puntos). La duración se mide en el navegador desde que se carga el formulario hasta el envío y se muestra después de registrar la operación. Los niveles son Bajo (0-19), Moderado (20-59) y Alto (60-100). **No es una probabilidad calibrada ni un modelo entrenado**: el porcentaje es únicamente un puntaje de demostración, puede tener historial limitado y no bloquea ni aprueba transacciones. Los ejemplos sintéticos sirven para probar que las reglas asignen niveles esperados; no enseñan al sistema qué es fraude. Para entrenar y validar un modelo predictivo harían falta transacciones reales debidamente anonimizadas y etiquetadas tras una investigación, con controles de calidad y privacidad. Ninguno de estos indicadores debe usarse para decisiones financieras reales.

## Estructura

```text
run.py                      punto de entrada (python run.py · gunicorn run:app)
config.py                   configuración leída de variables de entorno
app/
  __init__.py               create_app y validación del esquema
  models.py                 modelos SQLAlchemy
  routes/__init__.py        blueprint principal (sitio público, acceso y dashboard)
  api.py                    API de solo lectura bajo /api/v1
  ai/                       clustering exploratorio e indicador de riesgo por reglas
  services/                 banca, correo, estado de cuenta y datos de muestra
  static/                   css/, js/, brand/, images/, team/
  templates/                páginas, partials/ y components/
migrations/                 esquema versionado con Alembic (fuente de verdad)
scripts/                    herramientas de la MySQL local dedicada
tests/ · e2e/               pytest y Playwright
database/create_database.sql  alta manual de rol y base en PostgreSQL (sin clave real)
docs/                       auditorías y registros de avance
```

El esquema vive en las migraciones de Alembic; no hay un `schema.sql` paralelo que mantener.

### Rutas y variables opcionales de herramientas

Ninguna ruta del código depende de un usuario o equipo concretos. Si tu instalación es distinta, puedes sustituir estos valores sin editar archivos:

| Variable | Para qué sirve | Valor por defecto |
|---|---|---|
| `MYSQL_BIN` | Carpeta `bin` de MySQL Server (scripts de `scripts/`) | `mysqld` del `PATH` o la carpeta estándar del instalador de Windows |
| `NORTE_MYSQL_ROOT` | Datos, logs y archivos privados de la MySQL local dedicada | `%LOCALAPPDATA%\NorteBcpMySQL` (o `~/.local/share/NorteBcpMySQL`) |
| `CHROME_EXECUTABLE_PATH` | Chrome concreto para las pruebas E2E | Chrome instalado (Playwright `channel: 'chrome'`) |
| `PYTHON` | Intérprete que usan las pruebas E2E | `python` |
| `VISUAL_CAPTURE_DIR` | Carpeta de las capturas de `e2e/visual-captures.spec.cjs` | `test-results/visual-captures` |

### Contraseñas y recuperación (clientes)

Las contraseñas nuevas deben tener **8 a 10 caracteres con mayúscula, minúscula, número y punto (.)**; la regla vive en `app/services/password_policy.py` y se aplica igual al registrarse y al recuperar. La recuperación (`/recuperar`) es exclusiva para clientes, usa tokens de un solo uso y requiere correo configurado y, en producción, `PUBLIC_BASE_URL`. Hace falta aplicar la migración `d3a91c5e7f20` (`alembic upgrade head`). Detalles y pasos de prueba en [`docs/seguridad-arquitectura.md`](docs/seguridad-arquitectura.md).

### Datos de contacto y redes (cabecera y pie)

Se leen **solo** de variables de entorno (ver `.env.example`); las plantillas no repiten ningún dato. Los canales que no se configuren, o que no sean válidos, simplemente no se muestran.

| Variable | Ejemplo | Notas |
|---|---|---|
| `NORTE_INSTAGRAM_URL` · `NORTE_INSTAGRAM_HANDLE` | `https://instagram.com/tu_usuario` · `@tu_usuario` | Solo enlaces `https` |
| `NORTE_WHATSAPP_NUMBER` · `NORTE_WHATSAPP_DISPLAY` | `51900000000` · `+51 900 000 000` | Solo dígitos con código de país; genera `https://wa.me/<número>` |
| `NORTE_FACEBOOK_URL` · `NORTE_FACEBOOK_NAME` | `https://facebook.com/tu_pagina` · `Norte` | Solo enlaces `https` |
| `NORTE_CONTACT_EMAIL` | `contacto@example.com` | Opcional |
| `NORTE_CONTACT_HOURS` | `Lunes a viernes · 9:00 a 18:00` | Por defecto, un horario de demostración |

## Notas

- Toda la actividad bancaria es ficticia y solo actualiza la base local de la demo.
- `.env` y bases locales están excluidos por `.gitignore`.
- Para publicar fuera de localhost, configura `SECRET_KEY`, HTTPS y `COOKIE_SECURE=true` y reemplaza credenciales de demostración.

## Publicación en un servicio web

El proceso de producción usa Gunicorn y escucha en el puerto `PORT` que proporciona el hosting (`Procfile`: `gunicorn run:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`). Antes de desplegar, configura estas variables como secretos del servicio, nunca dentro del repositorio:

- `APP_ENV=production`
- `SECRET_KEY`: valor aleatorio largo y persistente.
- `DATABASE_URL`: URL de MySQL persistente. En Railway puedes referenciar la variable `MYSQL_URL` del servicio de base de datos; la app adapta `mysql://` para PyMySQL. En producción no se debe usar SQLite en almacenamiento efímero.
- `COOKIE_SECURE=true` para HTTPS.
- Los usuarios, sus contraseñas hasheadas y sus correos se migran desde la base actual; no se pasan por variables del hosting.
- Para Railway Hobby: `EMAIL_TRANSPORT=brevo_api`, `BREVO_API_KEY` y `SMTP_FROM` verificado en Brevo. Alternativamente, para SMTP, configura `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` y `SMTP_USE_SSL` (SMTP saliente requiere Railway Pro).

El punto de entrada WSGI es `run:app` (Procfile: `gunicorn run:app`). En `APP_ENV=production`, el arranque valida que el esquema ya esté migrado y sea compatible; si faltan tablas o columnas, se detiene sin crearlas, alterarlas ni sembrar cuentas. Las migraciones deben revisarse y ejecutarse por separado solo con autorización para la base elegida. No cargues el archivo `.env` ni la base SQLite local al hosting. `.railwayignore` excluye secretos, entornos virtuales y datos locales de la subida. No se ha autorizado ni realizado un despliegue de la aplicación.

## Pruebas

Las pruebas de pytest fijan SQLite en memoria y eliminan `MYSQL_URL` antes de importar la app. En PowerShell, con las dependencias instaladas en `.venv`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$venvScripts = (Resolve-Path '.venv\Scripts').Path
$env:PATH = $venvScripts + ';' + $env:PATH
$env:PYTHON = Join-Path $venvScripts 'python.exe'
npm ci
npm run test:e2e
```

Playwright levanta un servidor local de pruebas; la suite no usa Railway ni modifica bases persistentes.
