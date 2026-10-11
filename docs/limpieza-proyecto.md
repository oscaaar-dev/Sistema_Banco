# Limpieza, reorganización y normalización de rutas

Rama `design/nuevo-diseno`. Cada eliminación se justifica con evidencia comprobada en el código real.

## 1. Estado inicial

- Dos puntos de entrada casi idénticos (`app.py` y `run.py`); `app.py` compartía nombre con el paquete `app/`.
- `app/routes.py` coexistía con el paquete `app/routes/`: en ejecución gana el paquete, así que el archivo era código muerto.
- Un módulo de análisis facial (`app/ai/camera_analysis.py`) que nadie importa, con una dependencia pesada (OpenCV).
- `database/schema.sql` desactualizado respecto a los modelos; `database/create_database.sql` con una clave en texto plano.
- Rutas absolutas del equipo en pruebas E2E y scripts de MySQL; configuración de Playwright atada a PowerShell y a una ruta de Chrome.
- Un bloque de ~127 líneas en `home.js` para un carrusel que ya no existe en ninguna plantilla.

## 2. Archivos eliminados

| Archivo | Evidencia |
|---|---|
| `app/routes.py` | `import app.routes` resuelve a `app/routes/__init__.py` (comprobado en ejecución); todos los tests importan el paquete; el archivo ni siquiera era importable (`from . import bp`). |
| `app/templates/camera.html` | Ninguna ruta lo renderiza, ninguna plantilla lo incluye, y no hay llamadas dinámicas a `render_template`. |
| `app/ai/camera_analysis.py` | Ningún `import` lo referencia; `/camera/analyze` responde 410 y el README y la política de privacidad declaran que no hay análisis facial en servidor. |
| `database/schema.sql` | Solo 9 de las 17 tablas y columnas ausentes; ningún código, test ni despliegue lo usa. La fuente de verdad son las migraciones de Alembic. |
| `app.py` | Duplicado de `run.py`. Su única referencia era `playwright.config.cjs`; su lógica (`threaded` en pruebas) se fusionó en `run.py`. `Procfile` usa `run:app`. |
| `__pycache__/` (9), `.pytest_cache/`, `test-results/` | Artefactos regenerables, ya ignorados por Git. |

## 3. Archivos movidos

Ninguno. No había una reorganización que aportara valor suficiente para justificar el riesgo.

## 4. Archivos modificados

| Archivo | Motivo |
|---|---|
| `run.py` | Punto de entrada único; incorpora `threaded` según `APP_ENV`. |
| `config.py` | Se quita el valor por defecto de PostgreSQL con clave de relleno, inalcanzable porque sin `DATABASE_URL` se usa SQLite. Se comprobaron 6 variantes de URL. |
| `playwright.config.cjs` | Chrome por `channel`, servidor con `env` y `python run.py`; sin PowerShell ni rutas fijas. |
| `e2e/visual-captures.spec.cjs` | Capturas en `test-results/visual-captures` (o `VISUAL_CAPTURE_DIR`) con `path.join`. |
| `scripts/local_mysql.py` (nuevo) | Centraliza rutas de la MySQL local; sustituibles con `MYSQL_BIN` y `NORTE_MYSQL_ROOT`. |
| `scripts/bootstrap_dedicated_mysql.py`, `scripts/repair_dedicated_mysql_access.py`, `scripts/start_dedicated_mysql.ps1` | Usan el módulo anterior; ya no fallan fuera de Windows por `LOCALAPPDATA`. |
| `database/create_database.sql` | La clave en texto plano pasa a un valor de relleno. |
| `app/ai/interaction_model.py` | `LABELS` estaba declarada y no se usaba; ahora evita las cadenas duplicadas. |
| `app/services/seed_service.py`, `scripts/repair_dedicated_mysql_access.py`, `tests/test_app.py` | Imports sin uso (pyflakes). |
| `app/static/js/home.js` | Bloque muerto del carrusel `[data-home-carousel]`: ninguna plantilla ni test usa ese marcado. |
| `requirements.txt` | Se retira `opencv-python-headless`; `numpy` queda fijado y documentado. |
| `README.md` | Estructura real, tabla de variables opcionales y sin referencias a archivos eliminados. |

## 5. Rutas absolutas corregidas

| Archivo | Problema | Solución |
|---|---|---|
| `e2e/visual-captures.spec.cjs` | Carpeta de otro usuario bajo `C:\Users\…\.codex\…` | `path.join(__dirname, '..', 'test-results', …)` |
| `playwright.config.cjs` | `C:\Program Files\Google\Chrome\…` y comando PowerShell | `channel: 'chrome'` y `webServer.env` |
| `scripts/*` MySQL | `LOCALAPPDATA` obligatorio y carpeta de instalación fija | `scripts/local_mysql.py` con variables de entorno |

Se conserva como **último valor por defecto** la carpeta estándar del instalador de MySQL en Windows (no es una ruta de tu usuario) y siempre se puede sustituir con `MYSQL_BIN`.

## 6. Código innecesario

Imports sin uso en 3 archivos, la constante duplicada `LABELS`, el bloque del carrusel y los módulos y plantillas de la sección 2. Herramientas: `pyflakes` y `vulture` (instalados fuera del proyecto).

## 7. Dependencias

Se quita `opencv-python-headless` (solo lo importaba el módulo eliminado). El resto se usa: Flask, SQLAlchemy, Login, WTF, dotenv, Alembic (migraciones y tests), Gunicorn (`Procfile`), `psycopg` y `PyMySQL` (URL de la base y scripts), pandas y scikit-learn (`interaction_model`). `package.json` no cambia.

## 8. Pruebas

| Comando | Resultado |
|---|---|
| `python -m pytest -q` | 64 aprobadas (antes y después) |
| `npx playwright test` | 12 aprobadas (11 + capturas) |
| `pytest` en una copia en otra ruta, sin `.env` ni `node_modules` | 64 aprobadas |
| Arranque desde otro directorio de trabajo: `/`, `/login`, CSS | 200, 200, 200 |
| `pyflakes` sobre `app`, `scripts`, `tests`, `migrations`, `config.py`, `run.py` | Sin avisos |

## 9. Pendientes y riesgos (no se tocaron)

1. **Clave expuesta en el historial de Git.** El valor anterior de `database/create_database.sql` sigue en commits antiguos. Si esa clave se usó en algún servicio, hay que rotarla; limpiar el historial exige reescribirlo y requiere tu decisión.
2. **Credenciales de demostración por defecto** en `seed_service.py` (variables `DEMO_PASSWORD` y `ADMIN_PASSWORD` con valor de respaldo). Los tests dependen de ellas; conviene exigir variables fuera de pruebas.
3. **CSS y JS heredados** (`home.css`, `nubank.css`, `reveal.js`): aún los cargan las páginas informativas y de acceso, por eso no se eliminaron. Se retiran al terminar la migración al nuevo sistema de diseño.
4. **`norte-logo.svg` y `norte-logo-light.svg`** no tienen referencias, pero son parte del kit de marca; se conservan.
5. **Entornos virtuales** `.venv`, `.venv-1`, `.venv-2`: están ignorados por Git y no se pudo saber cuál usas.
6. `requirements.txt` fija Flask 3.1.3 y el entorno actual tiene 3.1.1; `Flask-Migrate` está instalado pero no se usa ni se declara.
7. `docs/phase6-text-audit.md` menciona `camera.html`; es un registro histórico y no se reescribió.
8. Las migraciones conservan las tablas históricas de análisis facial; quitarlas requiere una migración aprobada.
