# Roles administrativos, permisos y cuentas privilegiadas

Este documento no contiene secretos ni credenciales. Describe el modelo de roles, cómo se aprovisiona de forma segura el primer Administrador General, cómo se gestionan las cuentas administrativas y qué está probado.

## 1. Roles (columna `users.role`; no se duplican roles)

| Rol | Qué es | Puede | No puede |
|---|---|---|---|
| `ADMIN_GENERAL` | Responsable de las cuentas administrativas | Todo lo que permite un permiso. Crear cuentas `ADMINISTRADOR`, activarlas, suspenderlas, reactivarlas y desactivarlas; conceder y retirar permisos; revocar sesiones; ver el historial y la actividad de los demás administradores; entrar al panel antifraude y a los demás paneles | Alterar o borrar la auditoría desde la interfaz (no existe ninguna ruta que lo haga); actuar sobre su propia cuenta (permisos, estado, sesiones); suspender o desactivar al último Administrador General activo |
| `ADMINISTRADOR` | El «ADMIN» del encargo (se conserva el nombre que ya existía) | Solo lo que sus **permisos concretos** autorizan. **Denegar por defecto**: sin permisos únicamente ve la vista general `/admin` | Crear administradores, cambiar roles o permisos (propios o ajenos), cambiar estados, revocar sesiones, ver `/admin/cuentas` |
| `CLIENTE` | Cliente bancario | Sus funciones bancarias actuales, solo con sus propios datos | Cualquier ruta, API, auditoría o exportación administrativa (403) |
| `SOPORTE` (preexistente) | Sin permisos administrativos | Su vista de soporte | Nada administrativo |

Una cuenta administrativa solo es efectiva si su estado es `ACTIVA`. Una cuenta `PENDIENTE`, `SUSPENDIDA` o `DESACTIVADA` no puede iniciar sesión, pierde todos sus permisos al instante y sus sesiones abiertas se cierran en la siguiente petición.

## 2. Permisos (asignables a un `ADMINISTRADOR`)

| Permiso | Autoriza |
|---|---|
| `fraude.consultar` | Ver el panel antifraude: incidentes, evaluaciones, restricciones, retenciones y versiones del modelo |
| `fraude.investigar` | Iniciar la investigación de un incidente y comentar |
| `fraude.decidir` | Descartar o confirmar un incidente (confirmar impone una sanción **temporal**) |
| `fraude.resolver_retenciones` | Autorizar o rechazar operaciones retenidas |
| `fraude.gestionar_restricciones` | Levantar restricciones temporales |
| `fraude.exportar` | Descargar los informes CSV antifraude |
| `auditoria.consultar` | Ver y descargar la auditoría antifraude (la descarga exige además `fraude.exportar`) |
| `capturas.consultar` | Ver las capturas de seguridad de las operaciones (datos sensibles de privacidad; cada vista queda auditada) |

Los permisos de acción implican `fraude.consultar`. `ADMIN_GENERAL` los tiene todos por definición del rol. La autorización se comprueba **en el servidor, en cada ruta, API, exportación y acción sensible** (decorador `admins.requires(...)` y, por segunda vez, dentro del servicio antifraude); ocultar botones en la interfaz es solo comodidad.

## 3. Estados de una cuenta administrativa

`PENDIENTE` (creada, aún sin acceso) → `ACTIVA` ⇄ `SUSPENDIDA`; y `DESACTIVADA` (definitiva: para volver a dar acceso se crea una cuenta nueva).
Todas las cuentas existentes (clientes incluidos) quedan `ACTIVA` al aplicar la migración.

## 4. Aprovisionamiento del primer Administrador General

Restringido, documentado y sin registro público:

1. **Quién lo hace:** una persona con acceso al servidor y a la base del entorno, no un visitante. No existe ninguna ruta pública que cree administradores y el registro de clientes ignora cualquier campo de rol.
2. **Requisitos previos:** la migración `b6e2a9c15d37` aplicada (ver `docs/despliegue-antifraude.md`), correo funcionando (`EMAIL_TRANSPORT`, `SMTP_FROM`; con Brevo, `BREVO_API_KEY`) y `PUBLIC_BASE_URL` definida en producción.
3. **Verificación de identidad:** quien aprovisiona declara haber verificado la identidad de la persona por un canal independiente (`--confirmo-identidad`, obligatorio) y confirma de forma interactiva. La persona demuestra además que controla el correo: solo puede definir su contraseña con el enlace recibido.
4. **Orden** (desde la raíz del repositorio, con las variables del entorno de destino):

   ```bash
   flask --app run admin-bootstrap --email <correo-de-la-persona> --nombre "<Nombre Apellido>" --confirmo-identidad
   ```

   Crea una cuenta `ADMIN_GENERAL` en estado `PENDIENTE` y envía un enlace de un solo uso (vigente 24 horas) al correo indicado. **No imprime ni guarda** contraseñas ni enlaces. Si el correo no se puede enviar, no se crea nada.
5. **Reglas:** se niega si ya existe un `ADMIN_GENERAL` activo o pendiente (el aprovisionamiento inicial no se repite) y si el correo ya pertenece a una cuenta (**nunca promociona** cuentas existentes, de clientes u otras).
6. **Activación:** la persona abre el enlace, define su contraseña (política de 8 a 10 caracteres con mayúscula, minúscula, número y punto) y su cuenta queda `ACTIVA`. Para entrar necesita su contraseña **y** el código de un solo uso que se envía a su correo (el mismo segundo factor de toda la aplicación).
7. **Si se perdiera el acceso:** el aprovisionamiento solo se niega mientras exista un Administrador General `ACTIVA` o `PENDIENTE`; si no queda ninguno (por ejemplo, todos desactivados) se puede repetir con la misma orden. Un general `SUSPENDIDA` solo lo reactiva otro general: por eso no se puede suspender al último activo.

## 5. Gestión de cuentas `ADMINISTRADOR` (panel `/admin/cuentas`, solo `ADMIN_GENERAL`)

* **Crear:** nombre, correo y permisos iniciales. La cuenta queda `PENDIENTE` y la propia persona define su contraseña desde un enlace por correo (nadie más la conoce). Si el correo no puede enviarse, la cuenta no se crea. El correo debe ser único (no se reutiliza el de un cliente).
* **Control de dos personas:** definir la contraseña no activa la cuenta; un `ADMIN_GENERAL` debe pulsar «Activar», y solo es posible si la persona ya definió su contraseña.
* **Permisos, estados y sesiones:** cada cambio exige **confirmación reforzada** (la contraseña del propio actor y escribir el identificador de la cuenta afectada). Los fallos se registran en la auditoría y se limitan (5 en 15 minutos por actor).
* **Suspender o desactivar** revoca las sesiones abiertas de inmediato (marca `sessions_revoked_at`, comprobada en cada petición) y cualquier código de acceso pendiente. También existe «Revocar todas las sesiones» sin cambiar el estado.
* **Restablecer acceso** (contraseña olvidada o cuenta comprometida): las cuentas administrativas **no** usan la recuperación pública de contraseña (es solo para clientes). Un `ADMIN_GENERAL` pulsa «Restablecer acceso»: la cuenta vuelve a `PENDIENTE`,
  la contraseña anterior deja de servir, se revocan las sesiones y la persona define una nueva desde un enlace por correo; después hay que activarla de nuevo. Si el correo no se puede enviar no cambia nada. Así no hace falta crear otra cuenta (el correo es único).
* **Historial:** `/admin/cuentas/historial` muestra los cambios administrativos (`admin_change_log`, que solo se inserta) y la actividad auditada de las cuentas administrativas, para revisar lo que hacen otros administradores. Ninguna ruta lo modifica ni lo borra.
* Un identificador de cliente en estas rutas devuelve 404, igual que uno inexistente.

## 6. Acceso al panel (pasos)

1. Estar en un entorno con la migración aplicada y el código desplegado (ver `docs/despliegue-antifraude.md`).
2. El primer Administrador General recibe el enlace por correo, define su contraseña y queda activo.
3. Iniciar sesión en `/login` con el correo (o el usuario) y la contraseña; escribir el código de seis dígitos que llega al correo.
4. Se llega a `/admin`. Desde ahí:
   * **Administrador General:** «Cuentas administrativas» (`/admin/cuentas`), el panel antifraude (`/admin/fraude`) y el resto.
   * **Administrador:** solo los enlaces de los permisos concedidos; sin permisos verá un aviso para pedirlos a un Administrador General.
5. Un cliente, soporte o una persona sin sesión reciben 403 o son enviados al inicio de sesión.

## 7. Migración `b6e2a9c15d37` (aditiva)

Añade `users.account_status` (`NOT NULL`, valor por defecto `ACTIVA`) y las tablas `admin_permissions`, `admin_change_log` y `admin_activation_tokens`. No modifica ni borra filas, no promociona a nadie y es reversible (`alembic downgrade f7a1c4d9e620`, que solo elimina lo anterior: se pierden permisos, historial de cambios y enlaces pendientes, no el antifraude ni los datos bancarios).
**Debe aplicarse antes de desplegar este código**: la aplicación se detiene al arrancar si falta el esquema, y en staging `web` se despliega automáticamente al fusionar a `main`.
Verificada en SQLite (pruebas) y **ensayada en PostgreSQL real** sobre una base desechable partiendo del estado previo al antifraude: la cadena `e5b2c7d91a40 → f7a1c4d9e620 → b6e2a9c15d37` se aplica, las tablas originales conservan sus filas, la aplicación arranca en modo producción contra ese esquema, la bajada y la nueva subida funcionan. Ese ensayo descubrió y permitió corregir un fallo que SQLite no mostraba (`SELECT count(*) … FOR UPDATE` no es válido en PostgreSQL).

## 8. Qué está probado y cómo

* **Pruebas automatizadas** (`tests/test_admin_roles.py`, SQLite en memoria y correo simulado): permisos por ruta y método con un barrido de **todas** las rutas `/admin` (cualquier ruta nueva sin protección hace fallar la prueba); `ADMINISTRADOR` sin posibilidad de crear administradores ni elevarse (por HTTP y en el servicio); cliente y soporte denegados en todo `/admin`; solicitudes manipuladas (sin CSRF, contraseña o confirmación incorrectas, acciones y permisos desconocidos); acceso cruzado entre clientes; registro de cambios sin secretos; revocación de sesiones al suspender o desactivar; último Administrador General; permisos finos del panel antifraude; activación, expiración y un solo uso de los enlaces; aprovisionamiento inicial; y que no hay alta pública. Se comprobó por mutación que las pruebas fallan si se desactivan las comprobaciones.
* **Pruebas con cuentas de prueba** (cuentas sintéticas en bases en memoria o desechables): E2E en navegador real (`e2e/zz-admin-roles.spec.cjs`) y el ensayo en PostgreSQL descrito arriba.
* **No realizadas:** el inicio de sesión real con el correo de un Administrador General en **staging** (requiere aplicar la migración, desplegar y aprovisionar la cuenta con el correo de la persona responsable, que debe proporcionarlo ella) y, por tanto, el panel autenticado en staging. Tampoco se han probado el envío real de correos de activación ni la entrega a bandejas de entrada.

## 9. Limitaciones

* Los permisos son por acción, no por conjunto de datos: un administrador con `fraude.consultar` ve todos los incidentes.
* La confirmación reforzada usa la contraseña del actor; no hay un segundo factor adicional distinto del código por correo que ya protege todos los inicios de sesión.
* La recuperación de contraseña de un administrador no es autoservicio: depende de «Restablecer acceso» por otro Administrador General (por eso conviene tener al menos dos).
* Con una sola cuenta de Administrador General activa no hay quien la reactive si se bloquea: por eso se recomienda tener al menos dos y guardar el procedimiento de la sección 4.
* No hay notificación al propietario de una cuenta cuando se cambian sus permisos o su estado.
