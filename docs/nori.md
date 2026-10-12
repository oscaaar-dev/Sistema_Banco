# Nori · asistente de la web pública (mantenimiento)

Nori es el asistente de las **páginas públicas** de Norte (portada, Personas, Herramientas, Seguridad, Acerca de Norte, Términos
y Privacidad). No aparece en acceso, registro, recuperación de contraseña, errores ni en el panel privado.

**Costo cero y sin servicios externos.** Responde con reglas y con recuperación documental local. No usa modelos generativos,
APIs de IA, embeddings descargados ni servicios de terceros, y no añade dependencias: usa Flask, Jinja, JavaScript sin
frameworks y `scikit-learn` (ya requerido por Norte).

## Cómo responde

1. `app/nori/safety.py` limpia el mensaje (500 caracteres) y bloquea datos sensibles (DNI, teléfonos, tarjetas, códigos,
   frases del tipo «mi contraseña es…»): esos mensajes no se procesan ni se guardan.
2. `app/nori/engine.py` decide, en este orden:
   1. **Regla fuerte** (frase exacta o varias palabras clave) → respuesta predefinida (`kind: reglas`), salvo que un fragmento
      muy específico de la pregunta (3 o más términos en común y alta relevancia) responda mejor.
   2. **Documentación**: si hay fragmentos relevantes (varios términos en común y relevancia mínima) se citan con su fuente
      (`kind: documentacion`). Una regla débil cede ante un fragmento específico.
   3. Si no hay nada suficiente → reconoce la limitación y enlaza a las páginas oficiales (`kind: sin_informacion`).
3. «Más información» y «repítelo» usan el historial **ya validado** (`app/nori/memory.py`): el tema de la respuesta anterior o la
   última pregunta del usuario.

El navegador muestra bajo cada respuesta si es *predefinida* o *según la documentación de Norte*, con enlaces a las fuentes.

## Recuperación documental (RAG local)

* `app/nori/knowledge.py` reúne los fragmentos autorizados:
  * archivos `app/nori/knowledge/*.md` con `status: verificado` (carpeta exclusiva de Nori, ruta fija);
  * el texto de las páginas públicas, tomado de las mismas fuentes que las renderizan (`PUBLIC_INFO_PAGES`, Términos y Privacidad).
* `app/nori/rag.py` construye **en memoria, una vez por proceso**, un índice TF-IDF (`TfidfVectorizer`, raíces tosca en español,
  unigramas y bigramas). No escribe archivos, no descarga modelos y no hace llamadas de red. Si falla, Nori sigue con reglas.
* Cada documento es **contenido no confiable**: se descartan archivos con secretos, correos, números largos, cadenas de conexión
  o instrucciones dirigidas a un asistente; se limita el tamaño; se reduce a texto plano; las URL deben ser rutas internas.
  El índice solo contiene documentos, nunca conversaciones.
* No se indexan `data/`, `docs/`, `README`, `.env`, bases de datos ni nada fuera de `app/nori/knowledge/`.

### Actualizar los documentos autorizados

1. Edita o crea un `.md` en `app/nori/knowledge/` con esta cabecera:

   ```
   ---
   title: Título que se muestra como fuente
   url: /ruta-interna-oficial
   status: verificado
   sources: dónde se comprobó cada dato (archivo de código o página pública)
   ---

   ## Encabezado de sección
   Texto verificable, en español claro…
   ```

2. Escribe **solo** información que puedas comprobar en las páginas públicas o en el código. No inventes tasas, plazos, enlaces ni
   procedimientos. No incluyas contraseñas, claves, correos, datos de clientes ni documentación interna.
3. Regenera y verifica el índice local:

   ```bash
   flask --app run nori-index --check
   ```

   Muestra cuántos fragmentos y de qué fuentes; con `--check` termina con error si algún documento no se indexó (falta
   `status: verificado`, contiene secretos o su `url` no es válida). En producción el índice se reconstruye al reiniciar el
   servicio (cada worker lo construye en su primera consulta).
4. Si cambias un dato que depende del código (límites, vigencias), actualiza también el documento: las pruebas
   `tests/test_nori_rag.py` comparan las cifras clave con la configuración.

## Memoria, sesión y privacidad

* La conversación (últimos 8 turnos) vive solo en `sessionStorage` de la pestaña y expira a los 30 minutos sin actividad; hay un
  botón «Nueva conversación». El servidor no guarda mensajes.
* El historial que llega al servidor se valida (roles, longitud, cantidad) y se descarta si su *scope* no coincide con la sesión y la
  identidad actuales (cerrar o iniciar sesión, cookie borrada, otro usuario). Ver `app/nori/memory.py`.
* CSRF activo: el navegador pide un token (`GET /api/nori/csrf`) y lo envía en `X-CSRFToken`.
* Producción con varios workers: el *scope* y el token CSRF dependen de `SECRET_KEY`, que debe ser la misma en todos. El límite por
  conexión depende de la IP que identifica `_request_ip_hash`, cuya confianza en `X-Real-IP` está pendiente de verificar en Railway.
  Lista de verificación y pruebas manuales pendientes en `docs/seguridad-arquitectura.md` (apartado 9).
* Límites: 20 mensajes por minuto y 300 al día por conexión (IP con hash). Cada mensaje deja una fila de auditoría `NORI_CHAT` con
  fecha, hora y huella de la IP (dato personal seudonimizado), sin texto. Se considera vencida a las 24 h y se borra al recibir una
  solicitud a Nori o con `flask --app run purge-nori-audit` (no hay tarea programada, por lo que puede permanecer más tiempo si
  nadie usa Nori).
* El texto público está en `/privacidad` (apartado 6) y en el diálogo de cookies de la portada. Si cambias el comportamiento,
  actualiza ambos y las pruebas `tests/test_nori_privacy.py`.

## Configuración (`config.py`, prefijo `NORI_`)

| Variable | Valor | Para qué |
|---|---|---|
| `NORI_MAX_MESSAGE_CHARS` | 500 | Largo máximo de un mensaje |
| `NORI_RATE_LIMIT_MESSAGES` / `_WINDOW_SECONDS` / `NORI_DAILY_LIMIT` | 20 / 60 / 300 | Límites por conexión. El número por minuto y el diario se pueden ajustar con las variables de entorno `NORI_RATE_LIMIT_MESSAGES` y `NORI_DAILY_LIMIT` (el E2E las sube en su servidor de pruebas; en producción no se configuran) |
| `NORI_MEMORY_TURNS` / `NORI_MEMORY_TTL_SECONDS` | 8 / 1800 | Memoria del navegador |
| `NORI_MAX_REQUEST_BYTES` | 32768 | Tamaño máximo de una solicitud (mensaje + historial) |
| `NORI_PURGE_INTERVAL_SECONDS` | 60 | Como mucho una purga de auditoría por minuto y proceso |
| `NORI_RAG_ENABLED` | True | Apaga la recuperación documental (queda solo con reglas) |
| `NORI_RAG_MIN_SCORE` | 0.12 | Relevancia mínima de un fragmento |
| `NORI_RAG_STRONG_SCORE` / `NORI_RAG_SINGLE_TERM_SCORE` | 0.20 / 0.45 | Cuándo la documentación gana a una regla débil |
| `NORI_RAG_OVERRIDE_SCORE` | 0.30 | Con 3 o más términos en común, gana incluso a una regla fuerte |
| `NORI_RAG_MAX_SOURCES` / `NORI_RAG_EXCERPT_CHARS` / `NORI_RAG_QUERY_CHARS` | 2 / 380 / 200 | Fuentes citadas y tamaños |

## Pruebas

```bash
python -m pytest tests/test_nori*.py          # reglas, memoria, aislamiento, privacidad, RAG
npx playwright test e2e/nori*.spec.cjs        # widget, memoria, scroll, accesibilidad, RAG
python -m pytest && npx playwright test       # suites completas
```

## Limitaciones

* Solo conoce los documentos autorizados y las reglas; puede no encontrar una respuesta o interpretar mal una pregunta (lo indica).
* La recuperación es léxica (TF-IDF): no entiende sinónimos que no estén en los documentos.
* La expiración por inactividad la aplica el navegador; el servidor no puede imponerla porque no guarda conversaciones.
* No accede a cuentas, saldos ni datos personales.
* Nori solo responde en español: no traduce (no hay traducción automática gratuita y local que sea fiable). Si se le pide otro idioma,
  lo reconoce y remite a las páginas oficiales.

## Verificar que no hay dependencias ni servicios externos

```bash
git diff origin/main -- requirements.txt package.json package-lock.json   # debe estar vacío
grep -rniE "anthropic|openai|api_key|https?://" app/nori app/static/nori   # sin llamadas externas (solo comentarios)
```

La prueba `tests/test_nori_rag.py::test_nori_makes_no_external_calls` y el E2E `e2e/nori-rag.spec.cjs` comprueban que Nori solo
contacta al propio servidor.
