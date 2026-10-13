# Detección, prevención y sanción del fraude

Estado: **implementado en local en la rama `feature/fraude-detector`; no desplegado ni migrado en ninguna base compartida.** Por
defecto funciona en modo de observación (`shadow`): calcula y registra el riesgo, abre incidentes para revisión humana y **no
bloquea ni retiene nada**. Las medidas automáticas solo existen con `FRAUD_MODE=enforce`, que hay que activar expresamente.

## 1. Principios
* Una anomalía **no** es un fraude confirmado. El motor nunca asigna el nivel FRAUDE: solo un administrador, al confirmar un incidente.
* Ninguna puntuación (ni de reglas ni de Machine Learning) impone una sanción por sí sola. **Nada es permanente**: toda restricción
  lleva fecha de expiración (máximo 365 días para una sanción administrativa) y se puede levantar.
* El motor **complementa** las reglas bancarias existentes (autenticación, autorización, validación, saldo, idempotencia, OTP y
  cámara obligatorios). Un resultado del motor nunca autoriza una operación que esas reglas rechazan: solo puede **añadir** una retención.
* La puntuación 0-100 es una herramienta interna de clasificación, **no una probabilidad de fraude calibrada**. Los umbrales son
  iniciales y no están validados con datos reales etiquetados (no existen).

## 2. Las cinco situaciones, en campos distintos
| Situación | Dónde vive |
|---|---|
| Anomalía detectada | `risk_evaluations` (nivel, puntuación, señales, versión de reglas y de modelo, modo, acción recomendada y aplicada) |
| Actividad sospechosa | `risk_evaluations.level = SOSPECHOSO` |
| Incidente investigado | `fraud_incidents.status` (ABIERTO, EN_INVESTIGACION, DESCARTADO) más `incident_events` (historial: quién hizo qué y cuándo) |
| Fraude confirmado | `fraud_incidents.status = CONFIRMADO` con `confirmed_by_id`, `confirmed_at` y `confirmation_reason` |
| Sanción aplicada | `security_restrictions` con `origin = SANCION`, alcance, inicio y expiración |

## 3. Niveles y medidas
| Nivel | Significado | Medidas |
|---|---|---|
| NORMAL | Sin señales relevantes | Se permite y se registra la evaluación |
| INTERMEDIO | Señales de actividad inusual, sin evidencia de fraude | Alerta registrada. La verificación adicional ya existe: el OTP y la cámara son obligatorios en cada operación |
| SOSPECHOSO | Varias señales independientes | `shadow`: registro e incidente para revisión. `enforce`: la operación se **retiene antes de ejecutarse** (sin cambio de saldo) o, en accesos, bloqueo preventivo temporal y revocación de sesiones |
| FRAUDE | Confirmado por un administrador | Sanción temporal con expiración, revocación de sesiones, expediente conservado y auditoría. No existen sanciones permanentes: renovar una sanción exige una nueva decisión humana |
| NO_EVALUADO | La evaluación falló | **No equivale a «sin riesgo».** En `enforce` continúan solo operaciones de hasta `FRAUD_FAILSAFE_MAX_AMOUNT` (S/ 500); el resto se retiene |

## 4. Modos (`FRAUD_MODE`)
* `off`: no evalúa. Las restricciones impuestas por un administrador se siguen aplicando.
* `shadow` (predeterminado): evalúa, registra y abre incidentes; no aplica medidas automáticas.
* `enforce`: aplica las medidas automáticas de la tabla anterior. Un valor desconocido de `FRAUD_MODE` se trata como `shadow`.

**Antes de activar `enforce`** (decisión humana, no automática): revisar en el panel (`/admin/fraude`) y con `flask --app run fraud-report`
la proporción de alertas, los falsos positivos descartados, la tasa de fallos y la latencia durante un periodo de observación suficiente.

## 5. Arquitectura
```
app/fraud/constants.py     vocabulario común (niveles, estados, ámbitos)
app/fraud/features.py      características con consultas acotadas (ventanas y LIMIT, con índices)
app/fraud/rules.py         reglas versionadas, topes por grupo y clasificación en niveles
app/fraud/engine.py        evaluación de operaciones y accesos, política por modo, retención antes de tocar saldos
app/fraud/service.py       incidentes, restricciones, retenciones y acciones administrativas (con comprobación de rol)
app/fraud/anomaly.py       Isolation Forest (solo observación): candidatos, puertas de activación, inferencia
app/fraud/csv_training.py  carga y validación de CSV, división temporal y entrenamiento
app/fraud/synthetic.py     datos sintéticos para pruebas
app/fraud/export.py        exportación CSV con neutralización de fórmulas
app/fraud/metrics.py       métricas de observación y purga de evaluaciones antiguas
app/routes/fraud_admin.py  panel administrativo (/admin/fraude…)
```
Puntos de integración: `banking_service.process_operation` y `process_internal_transfer` (evaluación **después** de validar y **antes**
de modificar saldos), `routes` (fallos de clave y de OTP, acceso correcto, restricción de acceso) y un `before_request` que cierra las sesiones de un
cliente con el acceso restringido.

## 6. Modelo de datos (migración `f7a1c4d9e620`)
Migración **aditiva**: seis tablas nuevas y una columna opcional. No modifica ni borra filas existentes.
| Tabla | Propósito | Notas |
|---|---|---|
| `risk_evaluations` | Cada evaluación (operación o acceso) | Índices por usuario, nivel y fecha. Sin IP, contraseñas, OTP ni texto libre: solo recuentos y códigos de señal |
| `fraud_incidents` | Incidente investigable | Un incidente por cliente y categoría en 24 h (las nuevas evaluaciones se suman al historial) |
| `incident_events` | Historial inmutable del incidente | `actor_id` vacío = el sistema |
| `security_restrictions` | Restricción con alcance (OPERACIONES o ACCESO) | `expires_at` obligatorio |
| `held_operations` | Operación retenida **antes** de ejecutarse | Huella HMAC de los datos; se consume una sola vez |
| `fraud_model_versions` | Versión del detector: origen, datos (SHA-256), características, métricas, estado | Un entrenamiento siempre crea un CANDIDATO |
| `users.sessions_revoked_at` | Marca para revocar sesiones | El sello de sesión no cambia mientras sea nula (las sesiones vigentes no se invalidan al desplegar) |

Conservación: las evaluaciones antiguas sin incidente ni retención asociados se purgan con `fraud-maintenance` (`FRAUD_EVALUATION_RETENTION_DAYS`,
180 por defecto). Las vinculadas a un incidente o retención se conservan como evidencia.

## 7. Reglas y umbrales
Grupos de señales (cada grupo tiene un tope; **SOSPECHOSO exige al menos dos grupos relevantes**, de 10 o más puntos): monto (frente a la mediana
histórica y al saldo, incluido el intento de vaciar la cuenta), frecuencia, automatización (ráfagas), destinatario (nuevo, varios en 24 h, mismo monto
repetido), acceso (fallos de clave y de OTP recientes) y contexto (contraseña cambiada hace poco, conexión no vista antes). Con historial insuficiente
(menos de `FRAUD_MIN_HISTORY` operaciones comparables) las reglas de historial no se aplican y el umbral de SOSPECHOSO sube de 55 a 65.

Umbrales iniciales: INTERMEDIO ≥ 25, SOSPECHOSO ≥ 55 (y ≥ 2 grupos). **No están validados con datos reales etiquetados.** Comprobación sobre datos
sintéticos (`python -m scripts.fraud_threshold_sweep`; 6.391 operaciones, de ellas 834 anomalías inyectadas):

| Configuración (intermedio/sospechoso/baja confianza) | Habituales en INTERMEDIO | Habituales en SOSPECHOSO | Anomalías en INTERMEDIO | Anomalías en SOSPECHOSO |
|---|---|---|---|---|
| A (25/55/65, vigente) | 0,0 % | 0,0 % | 42,6 % | 9,6 % |
| B (20/45/55) | 0,0 % | 0,0 % | 44,5 % | 10,1 % |
| C (35/65/75) | 0,0 % | 0,0 % | 45,4 % | 2,2 % |
| D (45/75/85) | 0,0 % | 0,0 % | 10,1 % | 0,0 % |

Lectura honesta: es un generador fabricado por nosotros, de modo que estas cifras **no miden eficacia contra fraude real**. Solo muestran que los
umbrales vigentes no marcan operaciones habituales sintéticas y que son **conservadores**: una señal aislada (por ejemplo, un vaciado del 92 % sin
otros indicios) suma 20 puntos y no llega a INTERMEDIO. Los umbrales se deben ajustar observando el modo `shadow` con datos reales y revisión humana.

## 8. Retención de operaciones (modo `enforce`)
1. El cliente confirma con OTP y cámara. Todas las validaciones bancarias se ejecutan primero (importe, saldo, destinatario, idempotencia).
2. El motor evalúa. Si el nivel es SOSPECHOSO (o la evaluación falla con un monto superior a `FRAUD_FAILSAFE_MAX_AMOUNT`), **solo se confirman los
   registros de la retención** (evaluación, incidente, retención, auditoría) y se lanza `OperationHeld`. **No se modifica ningún saldo**, no se crea
   ningún movimiento, transferencia, sesión de interacción ni foto.
3. El OTP queda consumido. Un administrador puede **autorizar** o **rechazar** la retención desde `/admin/fraude/retenciones`.
4. Si la autoriza, el cliente repite la operación (nuevo OTP y cámara). La autorización se reconoce por la huella de los datos (monto, destinatario),
   se usa **una sola vez** y la operación pasa por todas las validaciones normales. Una operación distinta no queda cubierta.
5. Los traslados entre cuentas propias se evalúan y se registran, pero nunca se retienen.

Atomicidad e idempotencia: la evaluación se guarda en un *savepoint*; si falla, la operación continúa intacta. Si la operación se revierte, ninguna
evaluación queda vinculada a un movimiento inexistente. *Nota de SQLite (solo pruebas):* el controlador `sqlite3` puede confirmar un savepoint antes que
la transacción exterior, de modo que en pruebas puede quedar una evaluación sin movimiento; en PostgreSQL se revierte con la transacción.

## 9. Accesos
* Los fallos de clave se atribuyen ahora al usuario cuando el identificador existe (antes se anotaban sin usuario), y los fallos del OTP de acceso se
  registran (antes solo se contaban). Los fallos contra una cuenta inexistente siguen siendo anónimos y **nunca** generan una restricción.
* SOSPECHOSO en un acceso (fallos reiterados de clave y de OTP **más** una conexión no vista antes) abre un incidente. En `enforce` aplica un bloqueo
  preventivo de `FRAUD_PREVENTIVE_ACCESS_MINUTES` (30) y revoca las sesiones. Compromiso reconocido: un tercero que conozca un identificador podría
  provocar fallos; por eso el contexto nuevo es obligatorio y el bloqueo es breve.
* El cliente restringido, con la contraseña correcta, recibe un aviso y **no se le envía ningún código**. Con una contraseña incorrecta no se revela la
  restricción. **La recuperación de contraseña sigue disponible.**
* La identificación de conexión nueva usa el hash de IP que el sistema ya calcula. Mientras la confianza en `X-Real-IP` siga pendiente de verificar en
  Railway (ver `docs/seguridad-arquitectura.md`, apartado 9), es una señal **débil** y nunca basta por sí sola.

## 10. Machine Learning gratuito (Isolation Forest, scikit-learn)
Es un detector **no supervisado y de solo observación**: su puntuación se guarda junto a cada evaluación (`model_score`, `model_version`) pero **no
cambia el nivel ni dispara medidas**. No hay etiquetas fiables de fraude; por eso no se calcula ninguna precisión ni sensibilidad salvo que el CSV traiga
etiquetas confirmadas suficientes. No se usa Deep Learning: no hay datos ni etiquetas que lo justifiquen y no existe una línea base que mejorar.

### Arquitectura híbrida: CSV, SQL y exportación CSV
* **CSV para entrenar y evaluar** (`flask --app run fraud-train --csv RUTA`). Columnas obligatorias: `timestamp` (ISO 8601), `account_id`, `amount`
  (> 0), `balance_before` (≥ 0) y `kind` (TRANSFERENCIA, PAGO o RECARGA). Opcionales: `transaction_id` y `label`.
* **SQL para lo operativo**: evaluaciones, incidentes, historial, restricciones, retenciones y el registro de versiones del modelo.
* **Exportación CSV** de resultados desde el panel (apartado 11).

### Validación del CSV
Se rechaza o se descarta y se **cuenta** (nada se repara en silencio): columnas obligatorias ausentes o repetidas, valores faltantes, fechas inválidas o
futuras, montos no numéricos, no finitos, ≤ 0 o desmesurados, saldos negativos, tipos desconocidos, cuentas de más de 64 caracteres y etiquetas no permitidas.
Se eliminan duplicados (por `transaction_id` y por contenido). Un archivo con más del 50 % de filas inválidas se rechaza por no ser fiable. Límites: 25 MB
y 200.000 filas; solo UTF-8 (con o sin BOM) y extensión `.csv`. **La carpeta `data/` del proyecto está vedada**: no se lee ni se escribe.

### Etiquetas
`label` admite `FRAUDE_CONFIRMADO`, `LEGITIMA_VERIFICADA` y `SIN_VERIFICAR` (vacío o ausente = `SIN_VERIFICAR`). **Nunca se inventan etiquetas**: una anomalía
sin verificar no es fraude ni una operación legítima. Las métricas supervisadas (AUC-ROC y precisión media) solo se calculan si la evaluación contiene al
menos 20 fraudes confirmados y 100 operaciones legítimas verificadas, y únicamente sobre esas filas.

### Separación y fugas de información
División **temporal**: se entrena con lo anterior a un instante de corte y se evalúa con lo posterior (20 %). Las características usan solo ventanas hacia atrás
dentro de cada cuenta, así que ninguna fila ve el futuro. Los fraudes confirmados no se usan para entrenar: el detector aprende lo habitual.

### Ciclo de vida: candidato → activación explícita
1. `fraud-train` registra un **CANDIDATO** (`fraud_model_versions`): versión, fecha, características, filas de entrenamiento y evaluación, informe de validación,
   métricas y huella SHA-256 del CSV (el CSV no se guarda). **No cambia el modelo activo ni se entrena con cada transacción.**
2. `fraud-model-activate VERSION` solo procede si se cumplen las puertas de validación (comprobaciones de cordura, **no** de eficacia): estado permitido,
   artefacto íntegro (SHA-256), mismo scikit-learn y Python, mismas características, al menos 200 filas de entrenamiento y 20 de evaluación, no más del 20 % de
   filas inválidas y una tasa de alertas en la evaluación entre 0 % y 35 %. Archiva la versión anterior y deja constancia en la auditoría.
3. Reversión: `fraud-model-activate` sobre una versión archivada, o `fraud-model-deactivate` (solo reglas). `fraud-model-reject` descarta un candidato.
4. Inferencia (`anomaly.score`): tolera ausencia de modelo, versión de entorno distinta, artefacto alterado y errores, sin afectar a la operación.

### Limitaciones
* Sin etiquetas confirmadas, el modelo no tiene precisión ni sensibilidad demostradas; mide qué tan atípica es una operación.
* Los datos sintéticos (`flask --app run fraud-synthetic-csv RUTA`, opción `--labelled` para una verdad de referencia fabricada) solo prueban el código.
* El artefacto es un archivo `joblib` (pickle) que solo escribe el propio comando en `FRAUD_MODEL_DIR` (`instance/fraud_models`, fuera de Git); antes de
  cargarlo se verifica su SHA-256 y las versiones. Dependiente de la versión de scikit-learn y de Python: Railway usa Python 3.13 y las pruebas locales 3.12,
  por lo que **un modelo entrenado en un entorno no se activa en el otro** (la puerta lo impide).

### CSV combinado de transacciones y accesos (`fraud-train-events`)
Fuente: `datasets_sinteticos/dataset_antifraude_completo_12000.csv` (descrito en `docs/dataset-antifraude-sintetico.md`). Es la **fuente original única**:
no se crean datasets independientes; `app/fraud/event_training.py` lo separa por `event_type` al procesarlo y exige un esquema **exacto** (columnas, tipos, fechas,
importes, etiquetas, ausencia de datos personales). Se rechazan archivos vacíos, sin ambos tipos de evento, no UTF-8, de más de 25 MB o 200.000 filas, y cualquier
ruta (de lectura o de informe) dentro de `data/`.

`flask --app run fraud-train-events RUTA [--report-dir CARPETA] [--flag-rate 0.05]` registra **tres candidatos y no activa ninguno**:

| Candidato | Variables (solo observables y calculables por el sistema) | Por qué |
|---|---|---|
| `iforest-tx-…` (transacciones) | `log_importe`, `proporcion_saldo`, `ratio_importe_tipico`, `operaciones_2m/10m/1h`, `beneficiario_nuevo`, `destinatarios_distintos_24h`, `fallos_login_30m`, `fallos_otp_recientes`, `recuperacion_clave_reciente`, `contexto_nuevo` | Solo salidas de dinero; los depósitos no se modelan |
| `iforest-acc-…` (accesos) | `fallido`, `intentos_fallidos_recientes`, `fallos_otp_recientes`, `solicitudes_otp_1h`, `contexto_nuevo`, `recuperacion_clave_reciente` | Las transacciones y los accesos **no comparten variables**: un único modelo mezclaría columnas vacías |
| `iforest-…` (clásico) | `log_monto`, `proporcion_saldo`, `salidas_10m`, `salidas_1h` | Flujo existente (`anomaly.train_candidate`) como línea base comparable |

**Excluidos de los predictores** (con su motivo en el informe y en el panel): `event_id` y `account_id` (identificadores), `timestamp` (solo ordena y divide),
`scenario` (procedencia: revela la etiqueta), `is_fraud` (etiqueta), `is_synthetic`, `puntuacion_riesgo_sintetica` y `nivel_riesgo_sintetico` (atajo derivado de las
demás), `dispositivo_nuevo`, `ubicacion_nueva` y `viaje_imposible` (el sistema actual no los recopila), `hora_del_dia` y `dia_semana` (el motor no usa la hora),
`saldo_posterior` (resultado de la operación).

**División y evaluación.** Cronológica, sin mezcla aleatoria: entrenan los eventos anteriores al corte (`2026-02-19T12:47:55`, el 75 % más antiguo) y se evalúa con el
25 % posterior (transacciones: 5.668 / 1.829; accesos: 2.944 / 1.056). Isolation Forest es **no supervisado**: las etiquetas no intervienen en el ajuste ni en
el umbral. El umbral es el percentil 5 de las puntuaciones del **periodo de entrenamiento** (tasa de alertas fijada a priori, igual que los hiperparámetros);
la evaluación no se usa para ajustar nada y sus etiquetas solo sirven para medir. Las pruebas lo verifican (el ajuste no recibe `y`; invertir las etiquetas
no cambia modelo ni umbral; sin solapamiento de identificadores; último evento de entrenamiento anterior al primero de evaluación).

**Definiciones.** Positivo: `is_fraud = 1` (fraude *sintético*). Marcado: puntuación menor que el umbral. Precisión = VP / (VP + FP) sobre los eventos marcados;
sensibilidad = VP / (VP + FN) sobre los fraudes sintéticos; tasa de falsos positivos = FP / (FP + VN) sobre los eventos normales; F1 = media armónica. **Si el
denominador es cero la métrica es «indefinida» (`null`), nunca 0 ni un valor inventado.** AUC-ROC y precisión media son independientes del umbral; la precisión media
debe leerse frente a la prevalencia.

**Resultados** (`datasets_sinteticos/informe_evaluacion_modelos_candidatos.md/.json`, reproducibles: misma huella `resultados_sha256` en ejecuciones repetidas):

| Evaluación | VP | FP | VN | FN | Precisión | Sensibilidad | FPR | AUC-ROC |
|---|---|---|---|---|---|---|---|---|
| Modelo de transacciones | 87 | 25 | 1.644 | 73 | 77,7 % | 54,4 % | 1,5 % | 0,949 |
| Modelo de accesos | 26 | 25 | 899 | 106 | 51,0 % | 19,7 % | 2,7 % | 0,901 |
| Candidato clásico (4 variables) | 64 | 27 | 1.642 | 96 | 70,3 % | 40,0 % | 1,6 % | 0,858 |
| Reglas del motor, alerta amplia (INTERMEDIO o más), transacciones | 101 | 23 | 1.646 | 59 | 81,5 % | 63,1 % | 1,4 % | — |
| Reglas del motor, alerta amplia, accesos | 59 | 7 | 917 | 73 | 89,4 % | 44,7 % | 0,8 % | — |
| Reglas del motor, solo SOSPECHOSO, transacciones / accesos | 35 / 1 | 0 / 0 | — | 125 / 131 | 100 % / 100 % | 21,9 % / 0,8 % | 0 % | — |

Lectura honesta: el modelo de transacciones ordena bien (AUC 0,95) pero a un 5 % de alertas deja sin detectar la mitad de los fraudes sintéticos, sobre todo los
sigilosos y de ingeniería social (0 de 54 marcados), y marca ráfagas legítimas y recuperaciones de clave; el de accesos es mucho más débil (sensibilidad 19,7 %).
Las reglas actuales con la alerta amplia rinden igual o mejor, y como nivel SOSPECHOSO son muy conservadoras (casi sin falsos positivos, pero casi sin
detecciones). **Ninguna conclusión es extrapolable a fraude real** (etiquetas y prevalencia sintéticas, un solo corte y una sola semilla).

**Activación.** Los candidatos por tipo de evento **no se pueden activar**: el inferidor actual usa 4 variables y la puerta «las características coinciden con las
actuales» lo impide (el panel muestra el motivo). El candidato clásico supera las puertas de cordura, pero no se ha activado ni se activa automáticamente: la
decisión es humana, por línea de órdenes, y las puertas no miden eficacia. Antes de activar cualquier modelo hay que reentrenarlo en el entorno de destino (Python
3.13 en Railway) y revisar el informe.

### Dataset público evaluado: MoMTSim (dinero móvil sintético)
Se busca un dataset público compatible con el esquema (cuenta, monto, saldo previo, tipo, fecha y etiqueta). Decisión y descartes:

| Opción | Estado |
|---|---|
| **MoMTSim**, «Synthetic Mobile Money Transaction Dataset» (Mendeley Data) | **Elegido**: descarga abierta sin cuenta (API pública), licencia CC BY 4.0, secuencias por cuenta, saldo previo y etiqueta |
| PaySim (Kaggle `ealaxi/paysim1`) | Esquema muy parecido, pero Kaggle exige iniciar sesión (no se usan credenciales de nadie) y su propia documentación desaconseja usar las columnas de saldo |
| AMLNet (Zenodo 16482144) | Descartado: registro obsoleto y con archivos embargados hasta 2036 |
| Tarjetas ULB (Kaggle/OpenML) | Descartado: variables anonimizadas (PCA), sin cuenta ni saldo |

**Fuente y licencia.** Azamuke et al., *Synthetic Mobile Money Transaction Dataset*, Mendeley Data, V2, DOI [10.17632/zhj366m53p.2](https://doi.org/10.17632/zhj366m53p.2)
(artículo asociado: DOI 10.1109/ACCESS.2024.3439012), Makerere University, generado con el simulador MoMTSim. **CC BY 4.0**: el script cita la fuente y declara la transformación
(esquema, muestreo y seudonimización). Archivo usado: `synthetic_mobile_money_transaction_dataset.csv`, 156.564.413 bytes, SHA-256
`da951eb95735da96271740a3e66b676b342d3831ce3111cd19dbfa020d3bd0a7` (verificado tras la descarga). Se guarda **fuera del repositorio y de `data/`**; no se publica.

**Columnas originales:** `step, transactionType, amount, initiator, oldBalInitiator, newBalInitiator, recipient, oldBalRecipient, newBalRecipient, isFraud`.
Perfil del archivo completo: 1.720.181 filas, 13.462 iniciadores, `step` de 0 a 143, tipos DEPOSIT, PAYMENT, TRANSFER, WITHDRAWAL y DEBIT, 175.518 filas con `isFraud=1`
(10,2 %), **todas de tipo TRANSFER** (el 30,8 % de las transferencias), 44.402 con saldo inicial negativo.

**Transformación (`python -m scripts.prepare_momtsim_csv`, reproducible):**
| Esquema del sistema | Origen y regla |
|---|---|
| `timestamp` | `2026-01-01 00:00 + step` horas. **El origen es arbitrario y se asume 1 hora por paso** (no verificado en la documentación accesible); la resolución es de 1 paso |
| `account_id` | `initiator` seudonimizado (SHA-256 truncado a 12 caracteres); los números originales de 16 dígitos tienen aspecto de tarjeta y no se conservan |
| `amount` | `amount` (se descartan importes ≤ 0 o > 10.000.000) |
| `balance_before` | `oldBalInitiator` (se descartan los saldos negativos: 39.831 filas) |
| `kind` | TRANSFER → TRANSFERENCIA y PAYMENT → PAGO; los demás tipos se **excluyen y se cuentan** (483.608) en lugar de forzar un significado |
| `label` | `isFraud=1` → `FRAUDE_CONFIRMADO` e `isFraud=0` → `LEGITIMA_VERIFICADA`, **ambas SIMULADAS** (ver abajo) |
| (ignoradas) | `newBalInitiator`, `recipient`, `oldBalRecipient`, `newBalRecipient` |
Muestreo por **cuenta completa** (todas las operaciones de cada cuenta elegida, con semilla) hasta 180.000 filas: conserva las secuencias que usan las características de frecuencia.
Cada ejecución escribe un **manifiesto JSON** (fuente, licencia, SHA-256, conteos de cada descarte, parámetros y advertencias) que `fraud-train --manifest` guarda junto a la versión del modelo.

**Procedimiento completo** (todo en una carpeta externa; ninguna base existente ni migración):
```
python -m scripts.prepare_momtsim_csv download --workdir RUTA_EXTERNA
python -m scripts.prepare_momtsim_csv profile  --workdir RUTA_EXTERNA
python -m scripts.prepare_momtsim_csv prepare  --workdir RUTA_EXTERNA --max-rows 180000 --seed 7
flask --app run fraud-train --csv RUTA_EXTERNA/prepared/momtsim_prepared.csv --manifest RUTA_EXTERNA/prepared/momtsim_prepared.manifest.json
```

**Honestidad de las etiquetas.** `isFraud` lo produce un simulador: es una verdad de referencia *simulada*, no fraude real confirmado por personas. Se mapea al vocabulario del
sistema solo para poder calcular métricas supervisadas, y la procedencia queda registrada en la versión del modelo (`validation.manifiesto_de_preparacion` y `notes`).
Además, la prevalencia (10-14 %) es irreal (en la práctica ronda el 0,1 %), así que la precisión media solo se interpreta frente a esa prevalencia.

**Resultado del candidato** (`iforest-20261013024445544862`, entrenado en una base SQLite nueva y aislada; **no activado**): 179.496 filas leídas, 49 duplicados eliminados, 0 inválidas;
entrenamiento con 122.651 filas (sin fraudes) y evaluación temporal con 36.533 posteriores a `2026-01-05T20:00` (5.062 fraudes simulados y 31.471 legítimas simuladas). Tasa de alertas
en la evaluación 14,0 %; deriva máxima estandarizada 0,18. Las puertas de cordura de activación se superan, **pero no miden eficacia**:

| Evaluación (solo filas etiquetadas) | AUC-ROC | Precisión media | Prevalencia de fraude |
|---|---|---|---|
| Global (pagos y transferencias) | 0,5835 | 0,1684 | 0,1386 |
| **Solo TRANSFERENCIA** (única con fraude) | **0,4853** | 0,2849 | 0,2986 |
| Reglas del motor (umbrales vigentes), transferencias | 0,4915 | 0,2971 | 0,2986 |

El AUC global (0,58) se infla por mezclar pagos pequeños con transferencias grandes; **dentro de las transferencias el modelo y las reglas se comportan como el azar**. Análisis univariante
sobre el archivo completo (AUC dentro de TRANSFER): monto 0,47, saldo previo 0,45, vaciado ≥ 99 % 0,52, saldo previo del destinatario 0,52, destinatario con saldo < 100 0,51.
**Conclusión:** con las cuatro características actuales (monto, proporción del saldo, salidas en 10 min y en 1 h) no se puede distinguir este fraude simulado de lo legítimo; el problema no es
el algoritmo sino la información disponible (el patrón del simulador no está en el monto ni en el saldo), y las pruebas univariantes no descartan combinaciones o secuencias que no se
han explorado. **El candidato no demuestra capacidad de detección y no se recomienda activarlo para decidir nada**; el modo de observación se mantiene.
Siguientes pasos razonables: ampliar las características con información del destinatario y de la secuencia de operaciones (en el sistema real, p. ej. antigüedad y saldo del destinatario cuando es
cliente de Norte), y repetir la evaluación; y, sobre todo, esperar a disponer de incidentes revisados reales para etiquetar de verdad.

## 11. Panel administrativo y exportación (`/admin/fraude`)
Solo el rol `ADMINISTRADOR` (el servidor comprueba el rol en cada ruta, GET o POST; `SOPORTE` y `CLIENTE` reciben 403). Todos los POST llevan CSRF, se auditan y el
servicio vuelve a comprobar el rol y que el administrador no actúe sobre su propio usuario. Permite: consultar y filtrar incidentes (estado, nivel, tipo, fechas),
ver por qué se marcó cada uno (señales, puntuación, versión de reglas y de modelo), iniciar la investigación, comentar, **descartar** (levanta las restricciones
preventivas y autoriza repetir lo retenido), **confirmar fraude** (sanción temporal con alcance y duración, máximo 365 días; la duración sugerida considera solo
incidentes **confirmados** previos), levantar restricciones y autorizar o rechazar operaciones retenidas.

Exportación CSV (`/admin/fraude/exportar/<conjunto>.csv`): evaluaciones, incidentes, operaciones sospechosas retenidas, restricciones, estadísticas y versiones del
modelo y la **auditoría antifraude** (`auditoria`), con filtros por fechas, nivel, estado, tipo y modo. UTF-8 con BOM, cabeceras en español, fechas UTC `AAAA-MM-DD HH:MM:SS`, importes con dos decimales,
máximo 10.000 filas (las cabeceras `X-Export-Rows` y `X-Export-Truncated` lo indican). **Neutraliza la inyección de fórmulas**: toda celda de texto que empiece por
`=`, `+`, `-`, `@`, tabulador o retorno de carro se antepone con un apóstrofo. No incluye nombres, correos, DNI, IP, OTP ni los motivos escritos a mano por administradores.
Cada descarga queda en la auditoría (`FRAUD_EXPORT_<CONJUNTO>`).

Otras pantallas de solo lectura: `/admin/fraude/auditoria` (registro de decisiones, retenciones, cambios de modelo y descargas; filtros por acción y fechas; sin textos libres ni datos personales) y `/admin/fraude/modelos/<versión>` (estado, variables, periodos, matriz de confusión, métricas y su definición, resultados por escenario, variables excluidas y motivos por los que no se puede activar). **La activación no se hace desde el panel**: es una orden explícita por línea de comandos.

## 12. Operación
Comandos (`flask --app run …`): `fraud-synthetic-csv`, `fraud-train`, `fraud-model-activate`, `fraud-model-reject`, `fraud-model-deactivate`, `fraud-model-status`,
`fraud-report`, `fraud-maintenance`, `fraud-train-events`.

| Variable / ajuste | Predeterminado | Uso |
|---|---|---|
| `FRAUD_MODE` (entorno) | `shadow` | `off`, `shadow` o `enforce` |
| `FRAUD_LEVEL_INTERMEDIO` / `FRAUD_LEVEL_SOSPECHOSO` | 25 / 55 | Umbrales iniciales (no validados) |
| `FRAUD_FAILSAFE_MAX_AMOUNT` | 500 | Continuidad si la evaluación falla en `enforce` |
| `FRAUD_PREVENTIVE_ACCESS_MINUTES` | 30 | Duración del bloqueo preventivo de acceso |
| `FRAUD_HOLD_TTL_HOURS` | 72 | Vigencia de una retención y de su autorización |
| `FRAUD_MAX_SANCTION_DAYS` | 365 | Tope de una sanción administrativa |
| `FRAUD_MODEL_DIR` | `instance/fraud_models` | Artefactos del detector |
| `INTERACTION_CLUSTER_MAX_SESSIONS` | 300 | K-Means exploratorio acotado (antes reprocesaba todas las sesiones en cada operación) |

## 13. Pruebas
`tests/test_fraud_engine.py` (niveles, shadow y enforce, retención antes de modificar saldos sin movimientos parciales, idempotencia, concurrencia con dos hilos,
fallos del motor y del modelo), `test_fraud_access.py` (fallos de clave y OTP, bloqueo temporal, revocación de sesiones, recuperación), `test_fraud_admin.py`
(permisos en servidor, CSRF, decisiones, historial, reincidencia, autoprotección, auditoría), `test_fraud_export.py` (permisos, filtros, formato, fórmulas, datos
mínimos), `test_fraud_model.py` (validación del CSV, división temporal, etiquetas, candidatos y activación, integridad, reversión, CLI), `test_fraud_http_flow.py`
(flujo real con OTP y cámara) `test_fraud_schema_and_performance.py` (migración frente a modelos y coste acotado), `test_fraud_events_dataset.py` (generación, esquema, cada validación del dataset, no sobrescritura), `test_fraud_event_training.py` (esquema estricto, división cronológica, ausencia de fugas, métricas indefinidas, reproducibilidad, carga de candidatos, CLI), `test_fraud_panel_models_audit.py` (detalle de modelo y auditoría: permisos, filtros, escape) y `test_fraud_robustness.py` (puntuaciones no finitas, modelo roto, datos extremos o ausentes, el modelo nunca decide solo, conciliación contable en `off`, `shadow` y `enforce`). Todas con SQLite en memoria o archivos
temporales y datos sintéticos.

## 14. Pasos manuales pendientes antes de un despliegue (nada de esto se ha hecho)
**Estado actual, verificado solo en local:**
* **Ningún modelo está activo.** Los tres candidatos (`iforest-tx-…`, `iforest-acc-…` e `iforest-…`) están registrados como candidatos y sin activar; el detector responde
  `sin_modelo_activo` y no existe ningún puntero `current.txt`. Los dos candidatos por tipo de evento ni siquiera superan la puerta de características; el clásico las supera,
  pero la activación solo se hace por orden expresa de una persona.
* **El modo por defecto en local es `shadow`** (`config.py`: cualquier valor ausente o desconocido de `FRAUD_MODE` se trata como `shadow`).
* **La variable `FRAUD_MODE` de Railway NO se ha verificado**: no se accedió a Railway ni a ninguna variable remota. Hasta comprobarla, no debe asumirse el modo de staging.
* La migración `f7a1c4d9e620` no se ha aplicado en ningún entorno.

1. **Revisar y aplicar la migración `f7a1c4d9e620` a la base de Railway staging con autorización expresa**, *antes* de desplegar el código: la aplicación se
   detiene al arrancar si el esquema no coincide. Fusionar a `main` despliega solo en staging, así que la migración debe ir primero. Es aditiva y reversible.
2. Verificar en Railway: `APP_ENV`, `SECRET_KEY` estable y compartida entre workers, y la confianza en `X-Real-IP` (apartado 9 de `docs/seguridad-arquitectura.md`).
3. Desplegar con `FRAUD_MODE=shadow` (el valor por defecto) y observar. El sistema de archivos de Railway es efímero: `instance/fraud_models` se pierde al
   redeplegar; el modelo se vuelve a entrenar y activar con los comandos (sin modelo, el motor funciona solo con reglas). Entrenar en el mismo entorno (Python 3.13).
4. Probar con Gunicorn y dos workers: cada proceso carga su copia del modelo y mantiene su propia caché; el estado de restricciones y retenciones vive en la base.
5. Decidir con evidencia la salida del modo de observación antes de activar `enforce`. Mantener la reversión: `FRAUD_MODE=shadow` y `fraud-model-deactivate`.
6. Sin notificaciones al cliente: no hay aviso por correo cuando se retiene una operación o se restringe el acceso (el mensaje solo se muestra en pantalla).
7. Decidir si `datasets_sinteticos/` (dataset, informe y artefactos `joblib` de los candidatos) se versiona o se conserva fuera de Git; hoy son archivos sin seguimiento
   y no se ha tocado `.gitignore`. Los artefactos no se reutilizan en otro entorno: se reentrenan allí.
8. Para poder activar un modelo por tipo de evento hay que ampliar antes la inferencia (`anomaly.vector_from_features`) con las mismas variables y volver a evaluar.

## 15. Limitaciones conocidas
Umbrales no validados con datos reales; sin etiquetas fiables de fraude; la identificación de conexión depende de una cabecera pendiente de verificar; la
duración declarada por el formulario del cliente no se usa (no es fiable); el modelo es solo de observación; las sesiones de Flask son cookies firmadas, así que la
revocación es por usuario (no por sesión concreta); un tercero que conozca un identificador puede provocar fallos de acceso (mitigado exigiendo contexto nuevo y
un bloqueo breve).
