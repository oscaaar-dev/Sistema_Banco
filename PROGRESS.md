# Progreso de transformación a NORTE.

## Seguridad de trabajo

- Rama activa: `fix/auditoria`.
- La copia local está desvinculada de Railway; no hay remoto configurado ni despliegues autorizados.
- La base de pruebas usa SQLite en memoria. No se usan credenciales, esquemas ni datos de producción.
- Después de cada commit se ejecutan `python -m pytest -q` y `npm run test:e2e`.

## Fase actual

Fase 2 — auditando persistencia e identificando límites de migración; Fase 7 implementada y validada solo en SQLite de pruebas.

## Hecho

- Paso 0: inventario de efectos, capturas de referencia y red E2E inicial.
- Se añadió una configuración de pytest que fuerza SQLite en memoria y elimina `MYSQL_URL` antes de importar la aplicación.
- La red E2E cubre parallax de inicio de sesión, menú móvil y diálogos de ayuda/cookies.
- Fase 1 cerrada: recursos SVG propios de NORTE., favicon, imagen social, tokens de color, textos legales centralizados, rutas `/terminos` y `/privacidad`, enlaces en los pies, páginas de error de marca, transparencia sobre la cámara y credenciales eliminadas de la pantalla de login.
- Fase 6 cerrada: lenguaje público de portada, acceso, registro, espacio personal, tarjetas, operaciones, movimientos, comprobantes, cámara, administración y textos informativos revisado; el aviso legal y las aclaraciones de alcance permanecen visibles. Se actualizaron pruebas de copy sin cambiar efectos.
- Captura comparativa agregada para portada, login y dashboard en escritorio y móvil. La vista local vigente es `http://127.0.0.1:5001`; la instancia anterior en el puerto 5000 quedó detenida.
- Fase 6 validada después del commit `3312331`: 30 pruebas de pytest y 7 pruebas E2E aprobadas.
- Fase 7: la muestra fue aprobada con “continua”. Generador reproducible: cinco perfiles, 12 meses, 64 registros por persona y saldos calculados por movimiento; registros seed con `is_seed=true`, tarjeta Luhn validada sin guardar el PAN, email `.test`, teléfonos ficticios y CCI de muestra no utilizables. Está protegido para aceptar únicamente SQLite en memoria con `APP_ENV=testing`; no se ejecutó en MySQL local ni en Railway.
- Fase 2: la auditoría encontró que el arranque aplicaba `ALTER TABLE` y `create_all` fuera de pruebas. Se eliminó ese bootstrap automático; entornos persistentes ahora requieren un esquema ya migrado y compatible, o la app se detiene sin cambiar la base.

## Pendiente en el orden autorizado

1. Fases 2, 3, 4, 5, 8 y 9, respetando las paradas obligatorias indicadas por la persona usuaria.
2. Aplicar cualquier migración fuera de SQLite en memoria solo tras autorización expresa.

## Registro de avance

- 2026-10-09 — Fase 6 cerrada; 30 pruebas de pytest y 7 pruebas E2E aprobadas; seis capturas generadas y comparadas. Muestra de Fase 7 lista; detenida para aprobación previa a generar datos.
- 2026-10-09 — Aprobada la muestra de Fase 7 (“continua”). Se añadió el generador restringido a SQLite de pruebas en memoria y pruebas de idempotencia/conciliación. Pendiente ejecutar las suites antes del commit.
- 2026-10-09 — Fase 7 validada tras commits `36c21c3` y `016c4e2`: 32 pruebas pytest y 7 E2E aprobadas; incluye capturas E2E de referencia sin modificaciones visuales esperadas. Se inicia auditoría de Fase 2; no se migrará una BD persistente sin aprobación.
- 2026-10-09 — Cambio de seguridad de arranque en Fase 2 preparado; falta validación post-commit. Advertencia: hasta aplicar una migración aprobada, una base persistente previa puede no incluir `phone`, `cci` e `is_seed`, y la aplicación actualizada se negará a iniciar en ella.

## Hallazgos abiertos

- `data-home-carousel` no existe en la portada, aunque el carrusel principal sí funciona mediante `data-hero-carousel`. Es un efecto protegido: no se toca sin autorización expresa.
- Los identificadores locales de MySQL (`norte_bcp`, `norte_bcp_app` y la carpeta `NorteBcpMySQL`) están en uso. No se renombrarán hasta contar con autorización y un plan de migración.
- La aplicación sigue configurada para MySQL en el despliegue anterior; no hay migraciones versionadas todavía. Fase 2 requiere decidir y preparar migración hacia PostgreSQL antes de poder iniciar una copia nueva con el esquema actual.
- **Riesgo legal de privacidad:** `camera.html` ya informa que `app.js` envía fotogramas JPEG al servidor para `POST /camera/analyze` aproximadamente cada 1.2 segundos. El flujo sigue sin consentimiento específico ni política de retención aprobada; no se modificarán captura, envío ni almacenamiento hasta definir y aprobar el diseño de privacidad de la Fase 4.
