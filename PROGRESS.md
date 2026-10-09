# Progreso de transformación a NORTE.

## Seguridad de trabajo

- Rama activa: `fix/auditoria`.
- La copia local está desvinculada de Railway; no hay remoto configurado ni despliegues autorizados.
- La base de pruebas usa SQLite en memoria. No se usan credenciales, esquemas ni datos de producción.
- Después de cada commit se ejecutan `python -m pytest -q` y `npm run test:e2e`.

## Fase actual

Fase 6 — limpieza de textos demo/ficticio, después de cerrar la Fase 1.

## Hecho

- Paso 0: inventario de efectos, capturas de referencia y red E2E inicial.
- Se añadió una configuración de pytest que fuerza SQLite en memoria y elimina `MYSQL_URL` antes de importar la aplicación.
- La red E2E cubre parallax de inicio de sesión, menú móvil y diálogos de ayuda/cookies.
- Fase 1 cerrada: recursos SVG propios de NORTE., favicon, imagen social, tokens de color, textos legales centralizados, rutas `/terminos` y `/privacidad`, enlaces en los pies, páginas de error de marca, transparencia sobre la cámara y credenciales eliminadas de la pantalla de login.

## Pendiente en el orden autorizado

1. Fase 6 — limpieza de lenguaje demo/ficticio.
2. Fase 7 — muestra de datos sintéticos y contenido; detener para aprobación antes de generarlo completo.
3. Fases 2, 3, 4, 5, 8 y 9, respetando las paradas obligatorias indicadas por la persona usuaria.

## Hallazgos abiertos

- `data-home-carousel` no existe en la portada, aunque el carrusel principal sí funciona mediante `data-hero-carousel`. Es un efecto protegido: no se toca sin autorización expresa.
- Los identificadores locales de MySQL (`norte_bcp`, `norte_bcp_app` y la carpeta `NorteBcpMySQL`) están en uso. No se renombrarán hasta contar con autorización y un plan de migración.
- **Riesgo legal de privacidad:** `camera.html` ya informa que `app.js` envía fotogramas JPEG al servidor para `POST /camera/analyze` aproximadamente cada 1.2 segundos. El flujo sigue sin consentimiento específico ni política de retención aprobada; no se modificarán captura, envío ni almacenamiento hasta definir y aprobar el diseño de privacidad de la Fase 4.
