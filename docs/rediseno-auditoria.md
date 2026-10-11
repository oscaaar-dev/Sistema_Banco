# Rediseño visual de Norte · Auditoría y registro de avance

Rama de trabajo: `design/nuevo-diseno`. Este documento resume lo que se encontró en el código real antes de
cambiar nada y se actualiza al cerrar cada fase.

## 1. Stack comprobado

| Capa | Hallazgo |
|---|---|
| Backend | Flask 3.1 (blueprint `main` en `app/routes/__init__.py`, API de solo lectura en `app/api.py`), SQLAlchemy, Flask-Login, Flask-WTF (CSRF), Alembic. No se toca. |
| Plantillas | Jinja2 en `app/templates/`. `base.html` envuelve la intranet y las pantallas de acceso; `public_info.html` es una plantilla independiente para Personas, Herramientas, Seguridad y Acerca de. |
| Estilos | CSS propio sin build (`app.css`, `home.css`, `auth.css`, `intranet.css`, `operation.css`, `readability.css`). Muchas reglas se pisan entre sí por especificidad. |
| Scripts | JS propio sin dependencias: `home.js` (carrusel, parallax, diálogos), `reveal.js`, `auth.js`, `app.js` (cámara y riesgo), `transfer-auth.js`. |
| Dependencias externas | Solo Bootstrap Icons y Google Fonts (CDN). No hay librerías de animación ni de gráficos. |
| Imágenes | 3 retratos JPG con licencia Pexels en `static/team/` (ya documentados) y 4 SVG de marca. No hay más recursos. |
| Datos | La base de pruebas usa SQLite con cuentas, tarjetas y movimientos de muestra; todo es ficticio y así lo declara la interfaz. |
| Tests | 64 de pytest y 11 E2E (Playwright). Fijan contratos de marcado (ver §5). |

## 2. Inventario de páginas y necesidades

| Ruta | Estado antes | Necesita |
|---|---|---|
| `/` | Carrusel de 3 diapositivas con tarjetas blancas, secciones repetidas en cuadrícula | Hero con mockups reales de la interfaz, ritmo alterno claro/oscuro, bento de funciones |
| `/personas` | 3 tarjetas con retrato, fondo vacío | Composición propia, vista por rol, productos conceptuales claramente rotulados |
| `/herramientas` | 5 tarjetas de texto | Recorrido visual, viñetas por herramienta, pestañas con pantallas |
| `/seguridad` | 4 tarjetas de texto | Ilustración de escudo, diagrama del acceso con datos verificados en el código |
| `/acerca` | Hero con icono pequeño, 3 tarjetas y 3 pasos de texto | Brújula 3D, tarjetas diferenciadas, línea de tiempo ilustrada |
| `/terminos`, `/privacidad` | Texto legal sobre fondo plano | Misma identidad, lectura cómoda |
| `/login`, `/register`, `/login/verify` | Panel dividido con tarjetas flotantes | Fondo ambiental, tarjeta conceptual, microinteracciones en formularios |
| `/dashboard` | Tarjeta de saldo, accesos, tabla, tarjeta; sin gráficos | Saldo ocultable, gráfico real de ingresos y egresos, tarjeta financiera |
| `/movements`, `/cards`, `/operation/<kind>`, `/internal-transfer`, `/receipt/<id>` | Funcionales pero planos | Estados, filtros, tarjetas de producto, confirmaciones |
| `/admin`, `/support` | Tablas y 2-3 indicadores | Indicadores con jerarquía; sin inventar métricas |
| Errores 403/404/500 | Tarjeta simple | Misma identidad |

## 3. Problemas detectados

- Tarjetas blancas idénticas en casi todas las secciones y fondos oscuros sin composición.
- Cero gráficos en el dashboard; `/api/v1/accounts/<id>/transactions` ya permite construir uno con datos reales.
- Páginas informativas sin recursos gráficos propios; `home.css` (80 KB) y `readability.css` compiten por especificidad.
- `reveal.js` solo se cargaba en la portada y alternaba la visibilidad en cada scroll.
- Desbordamiento horizontal en móvil en el panel de movimientos del dashboard (corregido en la fase previa).
- Botones y tarjetas sin estados `focus-visible` consistentes.

## 4. Decisiones

1. Sistema de diseño propio (`norte-ds.css`) con tokens de color, espaciado, radios, sombras, curvas y duraciones; los demás archivos lo consumen.
2. Un solo `motion.js` para aparición progresiva, resplandor que sigue al cursor, pestañas y estados de carga. Sin librerías nuevas.
3. Gráficos en SVG generados por JS con los datos reales de la API; nada se inventa.
4. Mockups de interfaz construidos con HTML/CSS reutilizable (macros Jinja) en lugar de capturas, para que nunca queden obsoletos.
5. Ilustraciones 3D originales en SVG (brújula, escudo, pasos) dentro de `static/images/illustrations/`.
6. No hay generador de imágenes ni fotografías nuevas con licencia verificada en este entorno: solo se reutilizan los retratos ya documentados.
7. Importes en mockups enmascarados o rotulados como ejemplo, nunca como información real.

## 5. Contratos que no se pueden romper (los fijan los tests)

- Portada: «Tu día avanza.», 3 diapositivas con `aria-roledescription="diapositiva"`, `data-hero-carousel`, `#promociones`, exactamente 5 `data-parallax-card`, 4 `.home-promo-card` con arte y texto sin solaparse, `home-parallax`, «Tu recorrido por Norte es así de simple», «Operaciones desde tu espacio digital».
- Páginas informativas: `class="info-page"`, `class="info-card" data-reveal`, `class="info-card-action"`, 3 retratos con pie «Fotografía ilustrativa» y ningún `.info-note` en Personas, 5 tarjetas en Herramientas.
- Aviso legal común y ausencia de «SBS» en todas las páginas públicas.

## 6. Registro de avance

- **Fundamentos y portada.** `norte-ds.css` (tokens, botones, tarjetas, fondos ambientales, movimiento), `norte-public.css` (portada y páginas públicas) y `components/mockups.html`. La portada ya usa mockups de teléfono, tarjeta y ventana; los 64 tests y las E2E siguen pasando.
- **Cabecera, mega-menú y pie compartidos.** `partials/site_header.html`, `partials/site_footer.html`, `partials/social_links.html`, `norte-site.css` y `site-menu.js`. Aparecen en portada, informativas, legales, acceso, errores e intranet (pie y redes). Contacto configurable por variables `NORTE_*`. 17 pruebas nuevas en `tests/test_site_chrome.py`.
- **Pendiente del rediseño:** contenido propio de Personas, Herramientas, Seguridad y Acerca de (hoy conservan su hero anterior bajo la cabecera nueva), pantallas de acceso y dashboard (gráficos con la API de movimientos).
