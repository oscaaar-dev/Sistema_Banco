# Despliegue del antifraude: estado, procedimiento, reversión y plan de producción

Este documento no contiene secretos: las variables se nombran, nunca se muestran sus valores. Complementa `docs/fraude.md` (diseño) y `docs/seguridad-arquitectura.md`.

## 1. Estado verificado (2026-10-13)

| | Staging (`Norte-staging` / `staging`) | Producción (`production` del mismo proyecto) |
|---|---|---|
| Servicios | `web` (código de GitHub) y `Postgres` (con volumen) | **Ninguno**: el entorno está vacío |
| Revisión de Alembic | `f7a1c4d9e620` (antifraude), aplicada el 2026-10-13. **Pendiente en staging: `b6e2a9c15d37` (roles administrativos, cabeza actual de la rama)** | No existe base de datos |
| Esquema | 26 tablas: las 20 originales (sin cambios en sus filas) y las 6 antifraude, vacías | — |
| Despliegue de `web` | `SUCCESS`, estable | — |
| `FRAUD_MODE` | `shadow`, explícito | — |
| Modelo activo | Ninguno (`sin_modelo_activo`) | — |
| Python / dependencias | 3.13.15; scikit-learn 1.7.0, numpy 2.2.6, pandas 2.3.0 (las de `requirements.txt`) | — |

**Despliegue automático:** `web` se despliega desde la rama `main` del repositorio de GitHub (activador de Railway: rama `main`, **sin esperar a las comprobaciones de CI**). Cada fusión a
`main` despliega staging. En producción no hay ningún servicio, así que hoy no se despliega nada allí.

**Lección (incidente del 2026-10-13):** al fusionarse el PR #7 en `main`, Railway desplegó el código antifraude en staging antes de aplicar la migración. La aplicación, por diseño
(`_validate_existing_schema`), se detiene al arrancar si faltan tablas o columnas, y `web` quedó en `CRASHED`. Se resolvió aplicando la migración y reiniciando el servicio. **Regla:
la migración se aplica siempre antes de fusionar a `main`.**

## 2. Variables de entorno de `web` (solo nombres)
`APP_ENV`, `SECRET_KEY` (aleatoria, estable y de al menos 32 caracteres), `DATABASE_URL`, `COOKIE_SECURE`, `PUBLIC_BASE_URL`, `EMAIL_TRANSPORT`, `BREVO_API_KEY` y `SMTP_FROM`
(correo), `FRAUD_MODE` (**siempre explícito**: `shadow`; `off` solo para desactivar; `enforce` solo con decisión humana documentada), `FRAUD_MODEL_DIR` (opcional, por defecto
`instance/fraud_models`, efímero en Railway). `MIGRATION_DATABASE_URL` no se define en Railway: se usa solo al migrar, con el driver `postgresql+psycopg`.

## 3. Procedimiento de migración cuando la base no tiene URL pública (probado en staging)
El Postgres de Railway no expone una dirección pública, así que Alembic se ejecuta desde un equipo autorizado a través de un túnel SSH del propio Railway CLI.

1. **Comprobar el entorno** de forma inequívoca: ejecutar `railway ssh -s Postgres -e <entorno> -p <proyecto> -- sh -c 'echo $RAILWAY_PROJECT_NAME $RAILWAY_ENVIRONMENT_NAME'` y
   comparar una huella (SHA-256 de `usuario:contraseña@base`) del contenedor con la de `DATABASE_URL` de `web`, sin mostrarlas.
2. **Revisión actual y esquema** (solo lectura, `psql` dentro del contenedor): `select version_num from alembic_version`; lista de tablas; que no existan los objetos que crea la migración;
   que existan los destinos de las claves foráneas (`users.id`, `accounts.id`, `transactions.id`).
3. **Copia de seguridad recuperable:** `pg_dump --no-owner --no-privileges` dentro del contenedor, enviado por SSH como `gzip | base64` a una carpeta local fuera del repositorio; descodificar, comprobar
   que el gzip es íntegro y que termina con la marca «PostgreSQL database dump complete», y comparar los conteos exactos del volcado con los de la base viva. **Probar la restauración** en una
   base temporal (`CREATE DATABASE restore_check_tmp`) y comparar conteos; la base temporal la elimina una persona (`DROP DATABASE restore_check_tmp;`).
   Esa carpeta contiene datos reales del entorno (incluye hashes de contraseñas): no se sube a GitHub ni se comparte.
4. **Túnel:** `railway ssh config -s Postgres -e <entorno> -p <proyecto> --alias pg --path <archivo-temporal> -i <clave>` y después
   `ssh -F <archivo-temporal> -L 127.0.0.1:15432:127.0.0.1:5432 -N pg` (no se modifica `~/.ssh/config`).
5. **Alembic** (desde la raíz del repositorio, con `MIGRATION_DATABASE_URL=postgresql+psycopg://<usuario>:<clave>@127.0.0.1:15432/<base>` solo en el entorno del proceso):
   `alembic heads` (debe haber una sola cabeza), `alembic current` (debe ser la revisión previa a la que se va a aplicar: `e5b2c7d91a40` para el antifraude y `f7a1c4d9e620` para los roles), `alembic upgrade <previa>:<nueva> --sql` para revisar el SQL (solo `ALTER TABLE ... ADD COLUMN`,
   `CREATE TABLE`, `CREATE INDEX` y la actualización de `alembic_version`, sin `DROP`, `DELETE` ni `TRUNCATE`) y, por último, `alembic upgrade head`. El DDL es transaccional: o se aplica entero o nada.
6. **Verificar:** revisión `f7a1c4d9e620`; 6 tablas nuevas y `users.sessions_revoked_at`; conteos exactos de las tablas originales idénticos a los de la copia.
7. **Cerrar el túnel** y reiniciar `web` con el procedimiento normal de Railway (`railway redeploy -s web -e <entorno> -p <proyecto> -y`).
8. **Comprobar:** estado `SUCCESS` estable; `GET /`, `/login`, `/personas` y estáticos con 200; `/admin/fraude`, `/admin/fraude/auditoria` y los exportes redirigen a `/login` sin sesión (302); cabeceras de seguridad y cookie `Secure; HttpOnly`.

**Criterios de aceptación:** revisión esperada; esquema completo; ninguna fila perdida; servicio estable sin reinicios; respuestas HTTP anteriores; `FRAUD_MODE=shadow`; ningún modelo activo; auditoría sin
errores; y, cuando exista un administrador de prueba, las pruebas autenticadas del apartado 5.

## 4. Reversión
* **Del servicio:** `FRAUD_MODE=off` o `shadow`; o volver al código anterior. Los despliegues previos aparecen en Railway como `REMOVED` (no se garantiza que se puedan reactivar), así que lo
  seguro es revertir el cambio en `main` mediante un PR de reversión (que despliega el commit anterior). La migración es aditiva: el código anterior funciona con el esquema nuevo
  (ignora las tablas y la columna), por lo que no hace falta deshacer la migración para volver atrás.
* **De los modelos:** `flask --app run fraud-model-deactivate` devuelve a «solo reglas»; sin modelo activo, el motor funciona igual.
* **De la migración** (solo si hace falta y con autorización): `alembic downgrade e5b2c7d91a40` elimina las 6 tablas y `users.sessions_revoked_at`; **se pierden** las evaluaciones, incidentes y restricciones
  registrados desde entonces. Antes, copia de seguridad.
* **De los datos:** restaurar la copia lógica en una base vacía: `gunzip -c <copia>.sql.gz | psql -d <base> -v ON_ERROR_STOP=1` y fijar la revisión de Alembic de esa copia.

## 5. Pruebas autenticadas en staging (pendientes) y roles administrativos
Siguen **sin hacerse en staging**: exigen una cuenta administrativa real con acceso a su correo (el código de un solo uso llega por correo). Hasta ahora no existía ningún administrador ni un procedimiento para crearlo; la rama incluye los roles
`ADMIN_GENERAL` / `ADMINISTRADOR` / `CLIENTE` y el aprovisionamiento inicial `flask admin-bootstrap` (ver `docs/roles-administrativos.md`). Para probar en staging, en este orden:
1. Aplicar en staging la migración `b6e2a9c15d37` con el procedimiento del apartado 3 (copia de seguridad previa; ya ensayada en una base PostgreSQL desechable) **antes** de fusionar la rama a `main` (se desplegaría solo y `web` se detendría sin el esquema).
2. Fusionar y esperar el despliegue de staging; comprobar `SUCCESS` y las respuestas HTTP del apartado 3.
3. Aprovisionar el primer Administrador General con `admin-bootstrap` y **el correo de la persona responsable** (no se inventa ni se reutiliza el de ninguna otra cuenta); la persona define su contraseña desde el enlace y entra con el código por correo.
4. Probar con esa cuenta: acceso a `/admin/cuentas` y `/admin/fraude`; crear una cuenta `ADMINISTRADOR` de prueba **dedicada** (correo propio), activarla y comprobar que sin permisos solo ve la vista general; un cliente recibe 403 en todo `/admin`;
   suspender la cuenta de prueba y comprobar que su sesión se cierra; exportación CSV y auditoría; y, solo en una copia de pruebas, operación retenida y restricción temporal en `enforce`.
5. Desactivar la cuenta de prueba al terminar. No se crean ni se elevan privilegios a usuarios reales para probar.

## 6. Plan de producción (no ejecutado; requiere autorización expresa)
Hoy el entorno `production` está vacío, así que no hay nada que migrar: hay que **crearlo**. Orden:
1. Decidir si el proyecto actual (`Norte-staging`) es el destino o si se crea uno de producción con ese nombre; una base de producción independiente de la de staging.
2. Crear `Postgres` y `web`; fijar las variables del apartado 2 (con `APP_ENV=production`, `COOKIE_SECURE=true`, `SECRET_KEY` nueva y `FRAUD_MODE=shadow` explícito). **No** conectar `web` a la rama `main` hasta
   terminar los pasos 3 y 4, o desplegará sin esquema.
3. Con el servicio aún sin desplegar: `alembic upgrade head` desde la base vacía (crea las 26 tablas) siguiendo el apartado 3 (túnel, comprobaciones y verificación); no hace falta restaurar copia.
4. Cargar solo los datos que se decidan (no el seed de demostración de staging).
5. Conectar `web` a `main` y desplegar; comprobar los criterios de aceptación.
6. Mantener `shadow` durante un periodo de observación y revisar el panel; `enforce` y la activación de cualquier modelo son decisiones aparte, humanas y documentadas.
7. Reversión: apartado 4.

## 7. Modelos candidatos
No se versionan los `.joblib`: son archivos de `pickle` que se cargan con `joblib`, dependen de la versión de Python y scikit-learn (los entrenados con 3.12 no se aceptan en 3.13) y el sistema de
archivos de Railway es efímero. Distribución segura: no se distribuyen; se **reentrenan en el entorno de destino** con `flask --app run fraud-train-events datasets_sinteticos/dataset_antifraude_completo_12000.csv`,
que reproduce las mismas métricas (verificado en el contenedor de staging) y registra candidatos sin activar. Activar uno es una orden manual (`fraud-model-activate`) tras revisar el informe.

## 8. Base temporal `restore_check_tmp` (staging)
Creada el 2026-10-13 para probar que la copia de seguridad se podía restaurar. Comprobado en solo lectura: contiene exactamente las 20 tablas y 1.275 filas de la base de staging **antes** de migrar (revisión `e5b2c7d91a40`), pertenece al usuario `postgres`, tenía 0 conexiones
y ninguna variable ni servicio de Railway la usa (`web` usa la base `railway`). Después se usó como **base de ensayo desechable** de las migraciones y de los roles en PostgreSQL real, por lo que **ya no es una copia fiel de nada** (contiene cuentas de ensayo y las migraciones aplicadas);
la copia de seguridad original (`staging-antes-migracion.sql.gz`, fuera del repositorio) no se tocó. **No se ha eliminado.** Cuando una persona autorizada confirme que ya no se necesita, el procedimiento es:
1. Comprobar la identidad y que no hay conexiones: `select datname, count(*) from pg_stat_activity where datname='restore_check_tmp' group by 1;` debe devolver 0 filas y `\l` debe mostrar solo `postgres`, `railway` y `restore_check_tmp`.
2. Eliminar solo esa base, desde la base `postgres` y nunca estando conectado a ella: `railway ssh -s Postgres -e staging -p <proyecto> -- sh -c 'psql -h localhost -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE restore_check_tmp"'`. Es irreversible.
3. Verificar que `railway` sigue intacta (revisión de Alembic y conteos) y que `web` responde.
