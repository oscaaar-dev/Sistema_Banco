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
python app.py
```

3. Abre http://127.0.0.1:5000

La ruta `/` muestra la portada pública de Norte. Sus botones de banca digital y creación de cuenta enlazan al flujo existente de autenticación y registro (`/login` y `/register`). Después de autenticarte, las rutas protegidas mantienen el dashboard y las funciones actuales.

La primera ejecución local utiliza SQLite en `instance/norte_local.db` y crea tablas y datos demo. Para usar la base MySQL dedicada y transferir los datos actuales, sigue la guía de migración a continuación.

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

El formulario `/register` crea una cuenta ficticia con nombre completo, DNI peruano de 8 dígitos, correo único y contraseña. El DNI y el correo también pueden usarse para iniciar sesión; el acceso sigue requiriendo el código OTP por correo configurado arriba. Cada cuenta nueva recibe una cuenta de ahorros demo con saldo inicial S/ 0.00. No se valida el DNI con RENIEC ni se crea una cuenta bancaria real. La contraseña debe tener 10-64 caracteres ASCII, mayúscula, minúscula, dígito y al menos un símbolo de `!@#$%&*+-=?`; se rechazan espacios y guion bajo.

## Segundo factor por correo (OTP)

El inicio de sesión requiere usuario y contraseña, y luego un código de seis dígitos enviado al correo asociado, guardado en el registro del usuario en la base de datos. El código vence en 5 minutos, admite hasta 5 intentos y solo se puede volver a enviar una vez transcurrido el intervalo de espera de 60 segundos. Para desarrollo local, Brevo puede usarse por SMTP (`SMTP_HOST=smtp-relay.brevo.com`, puerto 587, STARTTLS, login y clave SMTP). Para Railway configura `EMAIL_TRANSPORT=brevo_api`, `BREVO_API_KEY` con la clave API de Brevo y `SMTP_FROM` con un remitente verificado; la app envía por HTTPS y no requiere SMTP saliente. `SMTP_FROM` es el remitente; los correos de demo/admin y clientes se conservan en la tabla `users`, no como configuración de producción. Configura también una `SECRET_KEY` aleatoria y persistente. Los secretos solo van en `.env` local o en las variables privadas del hosting, nunca en el repositorio. Sin un transporte de correo correctamente configurado, el acceso se deniega en lugar de omitir el segundo factor.

Este OTP es una medida demostrativa para la aplicación académica; no debe tratarse como protección suficiente para operaciones financieras reales.

## Incluye

- Dashboard responsive con saldo demo, accesos rápidos, tarjetas y movimientos.
- Transferencia a beneficiarios demo, pagos de servicios y recargas.
- Comprobantes y validación de saldo/montos.
- Administración con sesiones y auditoría.
- CSRF en formularios y sesiones de Flask protegidas.
- Modelo de datos creado desde SQLAlchemy para SQLite y MySQL.
- Cámara requerida para habilitar la confirmación de transferencias, pagos y recargas; debe permanecer activa hasta enviar.
- Clustering K-Means exploratorio sobre métricas de interacción en `app/ai/interaction_model.py`.
- Indicador demostrativo, basado en reglas transparentes, sobre montos y frecuencia de operaciones; no decide ni bloquea operaciones.
- Escenarios de prueba sintéticos del indicador en `tests/fixtures/operation_risk_scenarios.csv`; se usan para validar niveles, no para entrenar un detector de fraude.

## Alcance honesto de IA y privacidad

La verificación requerida captura fotogramas en el navegador y los envía temporalmente al servidor local para evaluar con OpenCV si hay un solo rostro visible y suficientemente centrado, grande y con los ojos detectables. Los fotogramas se procesan en memoria y no se guardan; se conserva en el registro de la operación solo el resultado numérico de calidad y si se detectó un rostro. La verificación debe haberse aprobado en los últimos 10 segundos para completar la simulación. Es una validación demostrativa de visibilidad/encuadre: **no identifica a la persona, no es autenticación biométrica y no detecta fraude**. La app no analiza emociones. K-Means agrupa métricas de navegación únicamente cuando hay suficientes sesiones variadas; sus etiquetas no son diagnósticos ni evaluaciones personales. El indicador de riesgo de operación calcula un puntaje de alerta de 0 a 100 basado en reglas: monto frente al saldo, comparación con operaciones del mismo tipo, intensidad de operaciones recientes y duración del formulario (3 minutos o más suma 15 puntos). La duración se mide en el navegador desde que se carga el formulario hasta el envío y se muestra después de registrar la operación. Los niveles son Bajo (0-19), Moderado (20-59) y Alto (60-100). **No es una probabilidad calibrada ni un modelo entrenado**: el porcentaje es únicamente un puntaje de demostración, puede tener historial limitado y no bloquea ni aprueba transacciones. Los ejemplos sintéticos sirven para probar que las reglas asignen niveles esperados; no enseñan al sistema qué es fraude. Para entrenar y validar un modelo predictivo harían falta transacciones reales debidamente anonimizadas y etiquetadas tras una investigación, con controles de calidad y privacidad. Ninguno de estos indicadores debe usarse para decisiones financieras reales.

## Estructura

```text
app/
  ai/interaction_model.py
  routes/__init__.py
  services/ (lógica y datos demo)
  static/css/ static/js/
  templates/
  models.py
config.py
 database/schema.sql
```

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

El proyecto usa `run:app` como punto de entrada WSGI. La aplicación crea las tablas y cuentas demo al iniciar una base vacía; configura las variables antes del primer arranque, ya que cambiar las contraseñas en el hosting no reemplaza las claves de usuarios ya creados. No cargues el archivo `.env` ni la base SQLite local al hosting. `.railwayignore` excluye secretos, entornos virtuales y datos locales de la subida.
