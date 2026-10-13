# Evaluación de modelos candidatos (datos SINTÉTICOS)

> Datos y etiquetas **sintéticos**. Los resultados no demuestran eficacia bancaria real.

## Dataset
- Archivo: `dataset_antifraude_completo_12000.csv`
- SHA-256: `fe2b9fe3611e7f8515554ece85d79170d37f79d8a5d72c38912587e072d78e6b`
- Filas: 12000 ({'TRANSACCION': 8000, 'ACCESO': 4000})
- Validación: superada (29 comprobaciones)
- Entorno: {'python': '3.12.10', 'scikit_learn': '1.7.0', 'pandas': '2.3.0', 'numpy': '2.2.6', 'plataforma': 'Windows-11-10.0.26200-SP0'}

## Integridad temporal y ausencia de fugas
- corte_temporal: 2026-02-19T12:47:55
- fraccion_evaluacion: 0.25
- umbral_calculado_con: puntuaciones del periodo de entrenamiento (percentil fijado a priori)
- etiquetas_usadas_en_entrenamiento: False
- etiquetas_usadas_para_el_umbral: False
- hiperparametros_fijados_a_priori: {'n_estimators': 100, 'contamination': 'auto', 'random_state': 42}
- tasa_de_alertas_a_priori: 0.05
- transacciones_solapamiento_de_ids_entrenamiento_evaluacion: 0
- transacciones_ultimo_evento_de_entrenamiento: 2026-02-19T12:47:31
- transacciones_primer_evento_de_evaluacion: 2026-02-19T12:47:55
- accesos_solapamiento_de_ids_entrenamiento_evaluacion: 0
- accesos_ultimo_evento_de_entrenamiento: 2026-02-19T12:45:01
- accesos_primer_evento_de_evaluacion: 2026-02-19T12:56:46

## Modelo: transacciones
- Versión: `iforest-tx-20261013032658828111`
- Variables: log_importe, proporcion_saldo, ratio_importe_tipico, operaciones_2m, operaciones_10m, operaciones_1h, beneficiario_nuevo, destinatarios_distintos_24h, fallos_login_30m, fallos_otp_recientes, recuperacion_clave_reciente, contexto_nuevo
- Filas de entrenamiento / evaluación: 5668 / 1829
- Periodos: {'entrenamiento': ['2026-01-05T00:07:43', '2026-02-19T12:47:31'], 'evaluacion': ['2026-02-19T12:47:55', '2026-03-05T23:54:10']}
- Umbral de puntuación (del periodo de entrenamiento): -0.077757
- Matriz de confusión: VP=87 FP=25 VN=1644 FN=73
- Precisión 77.7% · Sensibilidad 54.4% · F1 64.0% · Tasa de falsos positivos 1.5%
- Independiente del umbral: {'auc_roc': 0.9488, 'precision_media': 0.7593, 'prevalencia': 0.0875}
- Tiempo de evaluación: 7.89 ms (4.31 µs por evento)
- Puertas de activación: ['Las características del modelo no coinciden con las actuales.'] (NO se activó)

| Escenario | Fraude sintético | Eventos | Marcados | Proporción |
|---|---|---|---|---|
| L_RUTINA_TX | no | 1425 | 1 | 0.1% |
| L_COMPRA_GRANDE | no | 89 | 0 | 0.0% |
| L_BENEFICIARIO_NUEVO | no | 81 | 0 | 0.0% |
| L_RAFAGA_LEGITIMA | no | 37 | 13 | 35.1% |
| L_NUEVO_DISPOSITIVO | no | 18 | 3 | 16.7% |
| L_VIAJERO | no | 6 | 0 | 0.0% |
| L_RECUPERACION_CLAVE | no | 13 | 8 | 61.5% |
| F_VACIADO | sí | 28 | 14 | 50.0% |
| F_INGENIERIA_SOCIAL | sí | 30 | 0 | 0.0% |
| F_SIGILOSO | sí | 24 | 0 | 0.0% |
| F_RAFAGA | sí | 41 | 37 | 90.2% |
| F_TOMA_CUENTA | sí | 37 | 36 | 97.3% |

## Modelo: accesos
- Versión: `iforest-acc-20261013032658991023`
- Variables: fallido, intentos_fallidos_recientes, fallos_otp_recientes, solicitudes_otp_1h, contexto_nuevo, recuperacion_clave_reciente
- Filas de entrenamiento / evaluación: 2944 / 1056
- Periodos: {'entrenamiento': ['2026-01-05T05:13:43', '2026-02-19T12:45:01'], 'evaluacion': ['2026-02-19T12:56:46', '2026-03-05T21:22:25']}
- Umbral de puntuación (del periodo de entrenamiento): -0.173339
- Matriz de confusión: VP=26 FP=25 VN=899 FN=106
- Precisión 51.0% · Sensibilidad 19.7% · F1 28.4% · Tasa de falsos positivos 2.7%
- Independiente del umbral: {'auc_roc': 0.9011, 'precision_media': 0.5411, 'prevalencia': 0.125}
- Tiempo de evaluación: 4.43 ms (4.2 µs por evento)
- Puertas de activación: ['Las características del modelo no coinciden con las actuales.'] (NO se activó)

| Escenario | Fraude sintético | Eventos | Marcados | Proporción |
|---|---|---|---|---|
| L_NUEVO_DISPOSITIVO | no | 18 | 0 | 0.0% |
| L_VIAJERO | no | 10 | 0 | 0.0% |
| L_RECUPERACION_CLAVE | no | 39 | 25 | 64.1% |
| A_L_RUTINA | no | 745 | 0 | 0.0% |
| A_L_OLVIDADIZO | no | 100 | 0 | 0.0% |
| A_L_OTP_LENTO | no | 12 | 0 | 0.0% |
| F_TOMA_CUENTA | sí | 47 | 12 | 25.5% |
| A_F_FUERZA_BRUTA | sí | 55 | 14 | 25.4% |
| A_F_RELLENO_CREDENCIALES | sí | 18 | 0 | 0.0% |
| A_F_SESION_SIGILOSA | sí | 12 | 0 | 0.0% |

## Modelo: candidato_clasico
- Versión: `iforest-20261013032659423588`
- Variables: log_monto, proporcion_saldo, salidas_10m, salidas_1h
- Filas de entrenamiento / evaluación: 5668 / 1829
- Umbral de puntuación (del periodo de entrenamiento): -0.138733
- Matriz de confusión: VP=64 FP=27 VN=1642 FN=96
- Precisión 70.3% · Sensibilidad 40.0% · F1 51.0% · Tasa de falsos positivos 1.6%
- Independiente del umbral: {'auc_roc': 0.8579, 'precision_media': 0.5865, 'prevalencia': 0.0875}
- Puertas de activación: superadas (NO se activó)

## Reglas del motor sobre la misma evaluación
### transacciones
- Distribución por nivel: {'NORMAL': {'fraude_sintetico': 59, 'normal': 1646}, 'INTERMEDIO': {'fraude_sintetico': 66, 'normal': 23}, 'SOSPECHOSO': {'fraude_sintetico': 35, 'normal': 0}}
- Alerta estricta (SOSPECHOSO): {'matriz_confusion': {'VP': 35, 'FP': 0, 'VN': 1669, 'FN': 125}, 'precision': 1.0, 'sensibilidad_recall': 0.2188, 'f1': 0.359, 'tasa_falsos_positivos': 0.0, 'eventos_marcados': 35, 'normales_marcadas_como_sospechosas': 0, 'fraudes_sinteticos_detectados': 35, 'fraudes_sinteticos_no_detectados': 125, 'fraudes_sinteticos_en_evaluacion': 160, 'eventos_normales_en_evaluacion': 1669}
- Alerta amplia (INTERMEDIO o más): {'matriz_confusion': {'VP': 101, 'FP': 23, 'VN': 1646, 'FN': 59}, 'precision': 0.8145, 'sensibilidad_recall': 0.6312, 'f1': 0.7112, 'tasa_falsos_positivos': 0.0138, 'eventos_marcados': 124, 'normales_marcadas_como_sospechosas': 23, 'fraudes_sinteticos_detectados': 101, 'fraudes_sinteticos_no_detectados': 59, 'fraudes_sinteticos_en_evaluacion': 160, 'eventos_normales_en_evaluacion': 1669}
### accesos
- Distribución por nivel: {'NORMAL': {'fraude_sintetico': 73, 'normal': 917}, 'INTERMEDIO': {'fraude_sintetico': 58, 'normal': 7}, 'SOSPECHOSO': {'fraude_sintetico': 1, 'normal': 0}}
- Alerta estricta (SOSPECHOSO): {'matriz_confusion': {'VP': 1, 'FP': 0, 'VN': 924, 'FN': 131}, 'precision': 1.0, 'sensibilidad_recall': 0.0076, 'f1': 0.0151, 'tasa_falsos_positivos': 0.0, 'eventos_marcados': 1, 'normales_marcadas_como_sospechosas': 0, 'fraudes_sinteticos_detectados': 1, 'fraudes_sinteticos_no_detectados': 131, 'fraudes_sinteticos_en_evaluacion': 132, 'eventos_normales_en_evaluacion': 924}
- Alerta amplia (INTERMEDIO o más): {'matriz_confusion': {'VP': 59, 'FP': 7, 'VN': 917, 'FN': 73}, 'precision': 0.8939, 'sensibilidad_recall': 0.447, 'f1': 0.596, 'tasa_falsos_positivos': 0.0076, 'eventos_marcados': 66, 'normales_marcadas_como_sospechosas': 7, 'fraudes_sinteticos_detectados': 59, 'fraudes_sinteticos_no_detectados': 73, 'fraudes_sinteticos_en_evaluacion': 132, 'eventos_normales_en_evaluacion': 924}

## Definiciones
- **positivo**: Evento con is_fraud=1 (fraude SINTÉTICO del escenario de prueba, no un fraude confirmado por analistas).
- **marcado**: Evento cuya puntuación del modelo es menor que el umbral. El umbral es el percentil (tasa de alertas a priori) de las puntuaciones del periodo de entrenamiento.
- **precision**: VP / (VP + FP). Denominador: eventos marcados. Indefinida si no hay eventos marcados.
- **sensibilidad_recall**: VP / (VP + FN). Denominador: fraudes sintéticos del conjunto de evaluación. Indefinida si no hay positivos.
- **f1**: 2·precisión·sensibilidad / (precisión + sensibilidad). Indefinida si falta alguna.
- **tasa_falsos_positivos**: FP / (FP + VN). Denominador: eventos normales (is_fraud=0) del conjunto de evaluación. Indefinida si no hay negativos.
- **auc_roc**: Probabilidad de que un fraude sintético reciba una puntuación más atípica que un evento normal (independiente del umbral).
- **precision_media**: Área bajo la curva precisión-sensibilidad; debe leerse frente a la prevalencia (un clasificador al azar obtiene la prevalencia).
- **reglas_del_motor**: Se aplican a la misma evaluación las reglas versionadas del motor: «estricta» = nivel SOSPECHOSO; «amplia» = INTERMEDIO o SOSPECHOSO.

## Limitaciones
- Las etiquetas y los eventos son sintéticos y salen de escenarios definidos por nosotros: un buen resultado solo demuestra el comportamiento frente a ese escenario, no eficacia bancaria real.
- La prevalencia de fraude (8 % de transacciones y 10 % de accesos) es mucho mayor que la real; las precisiones no son extrapolables.
- Las variables dispositivo, ubicación y viaje imposible participan en los escenarios pero el sistema actual no las recopila: quedan fuera de los modelos y de las reglas.
- Los candidatos usan variables que el motor actual no calcula todas (p. ej. ratio frente al importe típico en el modelo): no se pueden activar hasta ampliar la inferencia; las puertas de activación lo impiden.
- Aprendizaje no supervisado: la puntuación mide qué tan atípico es un evento, no la probabilidad de fraude. El umbral procede de una tasa de alertas fijada a priori.
- Un único corte cronológico (60 días) y una sola semilla: no se estima la variabilidad entre muestras ni la deriva a largo plazo.

Resultados reproducibles (huella): `a563e7f7dff458f213702fca4ed2de468bad247ffe4253aaaa6be3f8c9ed8348`
