# Fase 6 — auditoría de lenguaje de demostración

Alcance: texto visible en plantillas, mensajes de rutas y servicios, JavaScript, comprobantes, metadatos y README. No se cambian selectores, estructuras, animaciones ni comportamiento. Las referencias técnicas de pruebas y los identificadores internos se excluyen cuando no se muestran en la interfaz.

| Archivo / líneas | Texto actual representativo | Texto propuesto, veraz para el estado actual |
| --- | --- | --- |
| `app/templates/home.html:8-227` | “práctica”, “demo”, “ficticio”, “simulado”, “de prueba”, “sin dinero real” | “en desarrollo”, “de ejemplo”, “ilustrativo” y “sin efectos financieros”; se mantienen los límites reales en la declaración legal. |
| `app/templates/home.html:252-268` | “Esta demo…”, “perfil demo”, “operaciones de prueba” | “Este sitio…”, “perfil” y “operaciones disponibles”; las cookies siguen descritas sin analítica ni publicidad. |
| `app/templates/auth_header.html:4-27` | “Datos ficticios”, “experiencia de práctica”, “Banca demo” | “Datos de ejemplo”, “experiencia en desarrollo”, “Banca digital”. |
| `app/templates/base.html:31-64` | “datos ficticios, sin dinero real”, “cliente demo”, “actividad de práctica” | “datos de ejemplo, sin efectos financieros”, “cliente”, “actividad reciente”. |
| `app/templates/login*.html:15-44` y `register.html:10-43` | “Diseñado para practicar”, “cuenta demo”, “saldo ficticio” | “Diseñado para explorar”, “cuenta”, “saldo de ejemplo”. |
| `app/templates/dashboard.html`, `movements.html`, `cards.html`, `admin.html` | “operación de práctica”, “saldo demo”, “tarjetas ficticias”, “usuarios demo” | Texto directo de producto, con aviso legal global que limita el alcance. |
| `app/templates/operation.html`, `camera.html`, `receipt.html` | “operación simulada/ficticia”, “comprobante demo”, “cámara demo” | “operación registrada en Norte”, “comprobante Norte”, “cámara”; se conserva que no tiene validez financiera. |
| `app/templates/public_info.html` y `app/routes/__init__.py:75-140` | “fines demostrativos”, “practicar”, “simulación”, “datos ficticios” | Descripciones de funciones disponibles y proyecto en desarrollo, sin afirmar servicios financieros reales. |
| `app/static/js/app.js:60` | “Rostro verificado para la demo” | “Rostro verificado para esta operación”. |
| `app/services/banking_service.py:40`, `seed_service.py:40`, `routes/__init__.py:279,434-438` | Mensajes o descripciones “simulados/ficticios/demo” | “de ejemplo”, “registrada” y “sin efectos financieros”. |
| `app/models.py:55` | “Banco Simulado” | “Norte”. |
| `README.md:1-115` | “simulación académica”, “datos demo”, “actividad ficticia” | “proyecto en desarrollo”, “datos de ejemplo” y límites técnicos reales. |

Las palabras que permanecerán en nombres de pruebas, datos de seed internos o identificadores de migración no son texto de interfaz. La declaración legal obligatoria conserva el estado real del proyecto y tiene prioridad sobre cualquier copy de producto.
