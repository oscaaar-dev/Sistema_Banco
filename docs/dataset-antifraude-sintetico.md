# Dataset sintético combinado del sistema antifraude

Archivo: `datasets_sinteticos/dataset_antifraude_completo_12000.csv` (+ `…manifest.json` con huella, semilla y validación).
**Todo es ficticio**: ningún dato personal ni bancario real, ninguna lectura de la carpeta `data/`, ningún servicio externo.
Las etiquetas `is_fraud` son **sintéticas**: salen de escenarios definidos por nosotros, no de fraudes confirmados por analistas. Un buen resultado
sobre este dataset demuestra que el código funciona y cómo se comporta frente a esos escenarios, **no** eficacia contra fraude bancario real.

## Contenido

| | |
|---|---|
| Filas de datos | **12.000** (más la cabecera) en un único archivo |
| `TRANSACCION` / `ACCESO` | 8.000 / 4.000 |
| `is_fraud = 1` | 653 transacciones (8,16 %) y 411 accesos (10,27 %); 1.064 en total |
| Cuentas seudónimas | 700 (`ACC-####`) |
| Periodo | 2026-01-05 00:07:43 → 2026-03-05 23:54:10 (60 días, orden cronológico) |
| Esquema | `combinado-1.0`, 32 columnas, UTF-8, saltos de línea `\n` |
| Generación | determinista: semilla 2026 → mismo archivo bit a bit (SHA-256 en el manifiesto) |

Tipos de operación (`tipo_operacion`): TRANSFERENCIA 3.278, PAGO 3.078, RECARGA 1.141, DEPOSITO 503; vacío en los 4.000 accesos.

## Columnas

| Columna | Tipo | Aplica a | Significado |
|---|---|---|---|
| `event_id` | texto `EVT-######` | todos | Identificador único (solo trazabilidad; no es predictor) |
| `timestamp` | ISO 8601 `AAAA-MM-DDThh:mm:ss` | todos | Instante del evento; el archivo está ordenado por él |
| `event_type` | `TRANSACCION` \| `ACCESO` | todos | Tipo de evento |
| `account_id` | texto `ACC-####` | todos | Cuenta seudónima |
| `scenario` | texto | todos | Escenario de generación (**procedencia**: revela la etiqueta; nunca es predictor) |
| `hora_del_dia`, `dia_semana` | entero 0-23 / 0-6 | todos | Derivados de `timestamp` |
| `tipo_operacion` | texto | TRANSACCION | TRANSFERENCIA, PAGO, RECARGA o DEPOSITO |
| `importe` | decimal > 0 (≤ 1.000.000) | TRANSACCION | Monto de la operación |
| `saldo_anterior`, `saldo_posterior` | decimal ≥ 0 | TRANSACCION | Saldo antes y después; el saldo encadena de una operación a la siguiente en cada cuenta |
| `beneficiario_nuevo` | 0/1 | TRANSACCION | Primer pago a ese destinatario (solo TRANSFERENCIA) |
| `destinatarios_distintos_24h` | entero ≥ 0 | TRANSACCION | Destinatarios distintos en las 24 h previas |
| `operaciones_2m`, `operaciones_10m`, `operaciones_1h` | entero ≥ 0 | TRANSACCION | Salidas previas de la cuenta en la ventana (solo mira hacia atrás; 2 m ≤ 10 m ≤ 1 h) |
| `n_historial`, `importe_tipico` | entero / decimal | TRANSACCION | Historial previo y mediana de importes previos |
| `fallos_login_30m` | entero ≥ 0 | TRANSACCION | Fallos de acceso de la cuenta en los 30 min previos |
| `resultado_login` | `EXITOSO` \| `FALLIDO` | ACCESO | Resultado del intento |
| `intentos_fallidos_recientes`, `solicitudes_otp_1h` | entero ≥ 0 | ACCESO | Fallos previos y OTP solicitados en la última hora |
| `viaje_imposible` | 0/1 | ACCESO | Desplazamiento físicamente imposible |
| `contexto_nuevo` | 0/1 | todos | Conexión desde un entorno no visto (lo que el sistema puede inferir) |
| `dispositivo_nuevo`, `ubicacion_nueva` | 0/1 | todos | Detalle del entorno nuevo (**el sistema actual no lo recopila**) |
| `recuperacion_clave_reciente` | 0/1 | todos | Recuperación de clave en las últimas horas |
| `fallos_otp_recientes` | entero ≥ 0 | todos | OTP incorrectos recientes |
| `puntuacion_riesgo_sintetica` | decimal 0-100 | todos | Puntuación **descriptiva** (observables + ruido); no es la etiqueta ni se usa para entrenar |
| `nivel_riesgo_sintetico` | NORMAL \| INTERMEDIO \| SOSPECHOSO | todos | Nivel derivado de esa puntuación (umbrales 25 / 55) |
| `is_fraud` | 0/1 | todos | Etiqueta sintética del **escenario**; solo para evaluar |
| `is_synthetic` | 1 | todos | Marca de datos sintéticos (siempre 1) |

Las columnas de un tipo de evento que no le corresponden van vacías (una transacción no trae `resultado_login`; un acceso no trae `importe`).

## Cómo se generó (sin triviales ni fugas)

1. **Escenarios con cuotas** (`app/fraud/synthetic_events.py`, `SCENARIOS`): cada episodio pertenece a un escenario documentado; la etiqueta la fija el
   escenario, nunca la puntuación ni el nivel. Hay casos normales con aspecto sospechoso (ráfagas legítimas, compras grandes, viajeros, recuperación de
   clave) y fraudes con aspecto discreto (sigiloso, sesión sigilosa, ingeniería social), de modo que las clases **se solapan**.
2. **Libro mayor por cuenta**: los saldos se simulan cronológicamente, de modo que `saldo_posterior = saldo_anterior ± importe` y ninguna salida supera el saldo.
3. **Ventanas hacia atrás**: los contadores de 2 min / 10 min / 1 h / 24 h solo usan eventos anteriores.
4. **Puntuación descriptiva**: se calcula solo con variables observables más ruido; el código ni siquiera lee la etiqueta ni el escenario (hay una prueba que lo comprueba).

| Escenario | Evento | ¿Fraude? | Filas | Qué representa |
|---|---|---|---|---|
| L_RUTINA_TX | TRANSACCION | no | 5.864 | Operación habitual, sin señales |
| L_COMPRA_GRANDE | TRANSACCION | no | 320 | Pago legítimo grande (3-6 × lo típico) |
| L_BENEFICIARIO_NUEVO | TRANSACCION | no | 320 | Primer pago legítimo a un destinatario nuevo |
| L_DEPOSITO | TRANSACCION | no | 480 | Ingreso (no es salida de dinero; no se modela) |
| L_RAFAGA_LEGITIMA | TRANSACCION | no | 179 | Varios pagos de servicios en pocos minutos |
| L_NUEVO_DISPOSITIVO | ACCESO, TRANSACCION | no | 169 | Dispositivo nuevo y operaciones normales |
| L_VIAJERO | ACCESO, TRANSACCION | no | 109 | Ubicación nueva (viaje) y operaciones normales |
| L_RECUPERACION_CLAVE | ACCESO, TRANSACCION | no | 200 | Recupera la clave, falla algún OTP y opera con normalidad |
| A_L_RUTINA | ACCESO | no | 2.790 | Acceso habitual y correcto |
| A_L_OLVIDADIZO | ACCESO | no | 445 | Varios fallos de clave y acceso correcto desde el mismo entorno |
| A_L_OTP_LENTO | ACCESO | no | 60 | Acceso correcto tras uno o dos OTP incorrectos |
| F_VACIADO | TRANSACCION | sí | 130 | 85-100 % del saldo en una sola operación |
| F_INGENIERIA_SOCIAL | TRANSACCION | sí | 130 | Contexto habitual, destinatario nuevo, monto 3-8 × lo típico |
| F_SIGILOSO | TRANSACCION | sí | 90 | Monto normal, solo indicios débiles |
| F_RAFAGA | TRANSACCION | sí | 165 | 4-8 transferencias en segundos a destinatarios nuevos |
| F_TOMA_CUENTA | ACCESO, TRANSACCION | sí | 303 | Fallos de acceso, contexto nuevo y transferencias grandes después |
| A_F_FUERZA_BRUTA | ACCESO | sí | 132 | Muchos fallos de clave en poco tiempo |
| A_F_RELLENO_CREDENCIALES | ACCESO | sí | 69 | Pocos fallos por cuenta con entorno nuevo |
| A_F_SESION_SIGILOSA | ACCESO | sí | 45 | Acceso correcto con solo indicios débiles |

## Validación automática (`app/fraud/dataset_validation.py`)

Se ejecuta antes de escribir el archivo y puede repetirse en cualquier momento (`validate_combined`). Las **32 comprobaciones** pasan:
12.000 filas exactas (8.000 y 4.000); columnas exactamente las del esquema y sin duplicadas; identificadores únicos y con formato; fechas válidas y en orden
no decreciente; `hora_del_dia` y `dia_semana` coherentes con la fecha; etiqueta binaria; `is_synthetic = 1`; puntuación en [0, 100] y niveles válidos; variables
binarias; esquema coherente por tipo de evento; tipos de operación válidos; importes y saldos finitos, positivos o no negativos y razonables; `saldo_posterior`
coherente con el importe; ninguna salida mayor que el saldo; continuidad de saldos por cuenta; ventanas coherentes (2 m ≤ 10 m ≤ 1 h); resultado y contadores de
acceso válidos; cuentas seudónimas, sin correos, teléfonos ni números con formato de tarjeta (Luhn); y **ninguna variable observable por sí sola separa las
clases** (AUC univariante ≤ 0,90; la mayor es 0,85: `destinatarios_distintos_24h`).

Se informa aparte (no como predictor) que la puntuación descriptiva sí es informativa (AUC univariante 0,94 en transacciones y 0,92 en accesos) y que sigue
sin ser perfecta. Las pruebas `tests/test_fraud_events_dataset.py` comprueban además que cada validación **detecta** la corrupción que dice detectar.

## Regenerar y verificar

```bash
python scripts/generate_antifraud_dataset.py --output-dir datasets_sinteticos
```

El script **nunca sobrescribe**: si el archivo existe y es idéntico no escribe nada; si es distinto, crea `…_v2.csv`. Rechaza la carpeta `data/`. Al terminar
imprime el informe de validación y deja el manifiesto junto al CSV.

## Uso en el entrenamiento

El CSV combinado es la **fuente original**: no se crean datasets independientes; `app/fraud/event_training.py` lo separa internamente por tipo de evento.
Véase `docs/fraude.md`, apartado 10 («CSV combinado»), y el informe `datasets_sinteticos/informe_evaluacion_modelos_candidatos.md`.

## Limitaciones

* Es sintético: refleja los supuestos de quien lo generó. La prevalencia (8 % y 10 %) es mucho mayor que la de un banco real.
* `dispositivo_nuevo`, `ubicacion_nueva` y `viaje_imposible` participan en los escenarios pero el sistema actual no los recopila: se conservan en el CSV y quedan
  fuera de los predictores.
* Un único periodo de 60 días y una sola semilla: no mide variabilidad ni deriva en el tiempo.
