# Progreso de transformación a NORTE.

## Seguridad de trabajo

- Rama activa: `fix/auditoria`.
- La copia local está desvinculada de Railway; no hay remoto configurado ni despliegues autorizados.
- La base de pruebas usa SQLite en memoria. No se usan credenciales, esquemas ni datos de producción.
- Después de cada commit se ejecutan `python -m pytest -q` y `npm run test:e2e`.

## Fase actual

Fase 7 — implementación del generador sintético aislado en pruebas; falta validar, revisar y cerrar.

## Hecho

- Paso 0: inventario de efectos, capturas de referencia y red E2E inicial.
- Se añadió una configuración de pytest que fuerza SQLite en memoria y elimina `MYSQL_URL` antes de importar la aplicación.
- La red E2E cubre parallax de inicio de sesión, menú móvil y diálogos de ayuda/cookies.
- Fase 1 cerrada: recursos SVG propios de NORTE., favicon, imagen social, tokens de color, textos legales centralizados, rutas `/terminos` y `/privacidad`, enlaces en los pies, páginas de error de marca, transparencia sobre la cámara y credenciales eliminadas de la pantalla de login.
- Fase 6 cerrada: lenguaje público de portada, acceso, registro, espacio personal, tarjetas, operaciones, movimientos, comprobantes, cámara, administración y textos informativos revisado; el aviso legal y las aclaraciones de alcance permanecen visibles. Se actualizaron pruebas de copy sin cambiar efectos.
- Captura comparativa agregada para portada, login y dashboard en escritorio y móvil. La vista local vigente es `http://127.0.0.1:5001`; la instancia anterior en el puerto 5000 quedó detenida.
- Fase 6 validada después del commit `3312331`: 30 pruebas de pytest y 7 pruebas E2E aprobadas.
- Fase 7: la muestra fue aprobada con “continua”. Generador reproducible en desarrollo: cinco perfiles, doce meses y saldos calculados por movimiento. Está protegido para aceptar únicamente SQLite en memoria con `APP_ENV=testing`; no se ejecutó en MySQL local ni en Railway.

## Pendiente en el orden autorizado

1. Cerrar Fase 7 con pruebas y commit.
2. Fases 2, 3, 4, 5, 8 y 9, respetando las paradas obligatorias indicadas por la persona usuaria.

## Registro de avance

- 2026-10-09 — Fase 6 cerrada; 30 pruebas de pytest y 7 pruebas E2E aprobadas; seis capturas generadas y comparadas. Muestra de Fase 7 lista; detenida para aprobación previa a generar datos.
- 2026-10-09 — Aprobada la muestra de Fase 7 (“continua”). Se añadió el generador restringido a SQLite de pruebas en memoria y pruebas de idempotencia/conciliación. Pendiente ejecutar las suites antes del commit.

## Hallazgos abiertos

- `data-home-carousel` no existe en la portada, aunque el carrusel principal sí funciona mediante `data-hero-carousel`. Es un efecto protegido: no se toca sin autorización expresa.
- Los identificadores locales de MySQL (`norte_bcp`, `norte_bcp_app` y la carpeta `NorteBcpMySQL`) están en uso. No se renombrarán hasta contar con autorización y un plan de migración.
- **Riesgo legal de privacidad:** `camera.html` ya informa que `app.js` envía fotogramas JPEG al servidor para `POST /camera/analyze` aproximadamente cada 1.2 segundos. El flujo sigue sin consentimiento específico ni política de retención aprobada; no se modificarán captura, envío ni almacenamiento hasta definir y aprobar el diseño de privacidad de la Fase 4.
