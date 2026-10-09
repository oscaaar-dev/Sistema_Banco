# Progreso de transformación a NORTE.

## Seguridad de trabajo

- Rama activa: `fix/auditoria`.
- La copia local está desvinculada de Railway; no hay remoto configurado ni despliegues autorizados.
- La base de pruebas usa SQLite en memoria. No se usan credenciales, esquemas ni datos de producción.
- Después de cada commit se ejecutan `python -m pytest -q` y `npm run test:e2e`.

## Fase actual

Fase 2 — preparando migraciones versionadas; ninguna migración persistente ni conexión a Railway se ha ejecutado.

## Hecho

- Paso 0: inventario de efectos, capturas de referencia y red E2E inicial.
- Se añadió una configuración de pytest que fuerza SQLite en memoria y elimina `MYSQL_URL` antes de importar la aplicación.
- La red E2E cubre parallax de inicio de sesión, menú móvil y diálogos de ayuda/cookies.
- Fase 1 cerrada: recursos SVG propios de NORTE., favicon, imagen social, tokens de color, textos legales centralizados, rutas `/terminos` y `/privacidad`, enlaces en los pies, páginas de error de marca, transparencia sobre la cámara y credenciales eliminadas de la pantalla de login.
- Fase 6 cerrada: lenguaje público de portada, acceso, registro, espacio personal, tarjetas, operaciones, movimientos, comprobantes, cámara, administración y textos informativos revisado; el aviso legal y las aclaraciones de alcance permanecen visibles. Se actualizaron pruebas de copy sin cambiar efectos.
- Captura comparativa agregada para portada, login y dashboard en escritorio y móvil. La vista local vigente es `http://127.0.0.1:5001`; la instancia anterior en el puerto 5000 quedó detenida.
- Fase 6 validada después del commit `3312331`: 30 pruebas de pytest y 7 pruebas E2E aprobadas.
- Fase 7: la muestra fue aprobada con “continua”. Generador reproducible: cinco perfiles, 12 meses, 64 registros por persona y saldos calculados por movimiento; registros seed con `is_seed=true`, tarjeta Luhn validada sin guardar el PAN, email `.test`, teléfonos ficticios y CCI de muestra no utilizables. Está protegido para aceptar únicamente SQLite en memoria con `APP_ENV=testing`; no se ejecutó en MySQL local ni en Railway.
- Fase 2: la auditoría encontró que el arranque aplicaba `ALTER TABLE` y `create_all` fuera de pruebas. Se eliminó ese bootstrap automático; entornos persistentes ahora requieren un esquema ya migrado y compatible, o la app se detiene sin cambiar la base. Se agregaron bloqueo de fila e idempotencia por cuenta para las operaciones internas, validados solo con SQLite. Se preparó Alembic y la revisión inicial `c9f9cff56362`; subida y bajada se probaron exclusivamente en SQLite en memoria. La integración PostgreSQL y migración de datos siguen pendientes.

## Pendiente en el orden autorizado

1. Fases 2, 3, 4, 5, 8 y 9, respetando las paradas obligatorias indicadas por la persona usuaria.
2. Aplicar cualquier migración fuera de SQLite en memoria solo tras autorización expresa.

## Registro de avance

- 2026-10-09 — Se observó un fallo E2E aislado del carrusel principal (no avanzó dentro del timeout; las tres pasadas posteriores aprobaron 7/7) y un error SQLite `InterfaceError` en solicitudes concurrentes durante dos ejecuciones. Se serializó el servidor de desarrollo solo bajo `APP_ENV=testing`; sin tocar el código del carrusel. Commit `9fc0dcc`: 34 pytest y 7 E2E aprobadas; sin nuevos errores SQLite y las capturas E2E volvieron a generarse.
- 2026-10-09 — Fase 6 cerrada; 30 pruebas de pytest y 7 pruebas E2E aprobadas; seis capturas generadas y comparadas. Muestra de Fase 7 lista; detenida para aprobación previa a generar datos.
- 2026-10-09 — Aprobada la muestra de Fase 7 (“continua”). Se añadió el generador restringido a SQLite de pruebas en memoria y pruebas de idempotencia/conciliación. Pendiente ejecutar las suites antes del commit.
- 2026-10-09 — Fase 7 validada tras commits `36c21c3` y `016c4e2`: 32 pruebas pytest y 7 E2E aprobadas; incluye capturas E2E de referencia sin modificaciones visuales esperadas. Se inicia auditoría de Fase 2; no se migrará una BD persistente sin aprobación.
- 2026-10-09 — Commit `78fde02`: el arranque persistente dejó de alterar esquema automáticamente; 33 pruebas pytest y 7 E2E aprobadas.
- 2026-10-09 — Commit `71796e3`: agregada clave de idempotencia por cuenta y bloqueo transaccional antes de registrar operaciones; el reenvío idéntico devuelve el comprobante existente. Post-commit: 33 pytest y 7 E2E aprobadas. No se afirma haber probado concurrencia PostgreSQL.
- 2026-10-09 — La primera E2E posterior registró un 500 aislado de SQLite al cargar un recurso estático tras autenticación; la segunda ejecución completa pasó limpia (7/7). Hallazgo no reproducido, pendiente de la auditoría de Fase 8.
- 2026-10-09 — Revisión de historia: no hay rutas `.env` rastreadas; el escaneo de 210 blobs solo detectó valores marcadores/test en `config.py`, `.env.example` y la cookie E2E. No se imprimieron valores. No hay remoto Git configurado.
- 2026-10-09 — Añadido entorno Alembic explícito: exige `MIGRATION_DATABASE_URL`, solo permite SQLite con `APP_ENV=testing` y no usa automáticamente `DATABASE_URL`/`MYSQL_URL`. La revisión inicial se generó desde modelos con SQLite de prueba; DDL PostgreSQL también se generó offline sin conexión. La ejecución real en un Postgres de prueba queda pendiente.
- 2026-10-09 — Commit `73f0092` validado: 34 pytest y 7 E2E aprobadas; seis capturas escritorio/móvil generadas. La revisión Alembic sube y baja solo en SQLite en memoria; el DDL PostgreSQL se generó offline. Ninguna base persistente fue conectada o modificada.

## Hallazgos abiertos

- `data-home-carousel` no existe en la portada, aunque el carrusel principal sí funciona mediante `data-hero-carousel`. Es un efecto protegido: no se toca sin autorización expresa.
- Los identificadores locales de MySQL (`norte_bcp`, `norte_bcp_app` y la carpeta `NorteBcpMySQL`) están en uso. No se renombrarán hasta contar con autorización y un plan de migración.
- La aplicación sigue configurada para MySQL en el despliegue anterior. Existe una revisión inicial, pero aún no hay revisión de datos para mover registros de MySQL a PostgreSQL ni se ha probado contra PostgreSQL.
- **Parada obligatoria pendiente:** antes de crear/vincular un servicio PostgreSQL en Railway, aplicar una migración fuera de SQLite de pruebas o trasladar los datos de MySQL se requiere autorización explícita. No se ha conectado, migrado, cambiado variables ni desplegado.
- Aviso operativo: una instancia ya iniciada puede seguir respondiendo con el código cargado previamente, pero el código actual se negará a arrancar contra una base persistente antigua hasta que se apruebe y aplique una migración compatible. No reiniciar el servidor local con la base actual antes de resolver ese paso.
- **Riesgo legal de privacidad:** `camera.html` ya informa que `app.js` envía fotogramas JPEG al servidor para `POST /camera/analyze` aproximadamente cada 1.2 segundos. El flujo sigue sin consentimiento específico ni política de retención aprobada; no se modificarán captura, envío ni almacenamiento hasta definir y aprobar el diseño de privacidad de la Fase 4.
