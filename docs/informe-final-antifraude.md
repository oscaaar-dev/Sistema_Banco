# Informe final: sistema antifraude (dataset sintético, candidatos y verificación)

Fecha de cierre: 2026-10-12. Rama local `feature/fraude-detector` (worktree `fraud-worktree`), `HEAD` = `origin/main` (`a6d3dd1`), **sin commits**.
Todos los datos y etiquetas son **sintéticos**: ninguna cifra de este informe demuestra eficacia contra fraude bancario real.

> **Actualización del 2026-10-13.** Este informe describe el cierre del 2026-10-12, previo a la publicación. Desde entonces: el trabajo se publicó en la rama `feature/fraude-detector` (commit `18519ea`) y el PR #7 se fusionó en
> `main` (`3e47b6c`); Railway desplegó staging automáticamente y `web` quedó caído porque faltaba la migración; se aplicó `f7a1c4d9e620` en staging con copia de seguridad previa y el servicio volvió a responder.
> **Producción no se ha tocado.** El estado vigente por entorno está en `docs/despliegue-antifraude.md` y en el apartado 14 de `docs/fraude.md`. Donde este informe diga «nada desplegado» o
> «migración sin aplicar», se refiere al 2026-10-12.

## 1. Estado en una línea
Dataset, tres modelos candidatos, informes, panel, exportaciones y documentación entregados y verificados. **No existe ningún modelo activo y el sistema permanece
en modo `shadow`.** A fecha 2026-10-12: nada publicado, nada desplegado, ninguna migración aplicada (ver la actualización anterior).

## 2. Archivos generados (rutas exactas)
Base: la raíz del worktree `fraud-worktree`. Todas las rutas de este informe son relativas a ella, salvo las evidencias E2E, que están fuera del repositorio.

| Archivo (relativo a la base) | Bytes | SHA-256 (inicio) |
|---|---|---|
| `datasets_sinteticos/dataset_antifraude_completo_12000.csv` | 1.653.779 | `fe2b9fe3611e7f85…` (completo en el manifiesto) |
| `datasets_sinteticos/dataset_antifraude_completo_12000.manifest.json` | 7.889 | `2fa40e2a7f6c0472…` |
| `datasets_sinteticos/informe_evaluacion_modelos_candidatos.json` | 20.867 | `f93b9e71ae57dc23…` (ruta personal redactada; métricas idénticas) |
| `datasets_sinteticos/informe_evaluacion_modelos_candidatos.md` | 9.329 | `f33ab4090eeb2320…` (ruta personal redactada; métricas idénticas) |
| `datasets_sinteticos/modelos_candidatos/iforest-tx-20261013032658828111.joblib` (+ `.json`) | 710.749 (+1.208) | `e309aef36cb2280c…` |
| `datasets_sinteticos/modelos_candidatos/iforest-acc-20261013032658991023.joblib` (+ `.json`) | 578.173 (+1.050) | `c60c6cbe56e7b5d2…` |
| `datasets_sinteticos/modelos_candidatos/iforest-20261013032659423588.joblib` (+ `.json`) | 837.757 (+503) | `621cf32643c88945…` |
| `docs/dataset-antifraude-sintetico.md`, `docs/fraude.md`, `docs/informe-final-antifraude.md` (este) | — | — |

Evidencia de pruebas E2E guardada **fuera del repositorio** (no borrar mientras se necesite):
`fraud-e2e-full-run3.log` (624.474 bytes, ejecución completa 81/81) y la carpeta `fraud-e2e-results\`, ambos en la carpeta que contiene el worktree.
No se versionan: `.gitignore` los excluye con `fraud-e2e-*`. Los modelos candidatos (`*.joblib`) tampoco se versionan hasta validar su compatibilidad con Railway.

## 3. Dataset (verificado releyendo el archivo en disco)
12.000 filas de datos: 8.000 `TRANSACCION` y 4.000 `ACCESO`; 12.000 identificadores únicos; `is_fraud = 1`: 653 en transacciones (8,16 %) y 411 en accesos
(10,27 %); 32 de 32 validaciones automáticas; el SHA-256 coincide con el manifiesto; se regenera idéntico con la semilla 2026.

## 4. Candidatos y evaluación (tramo temporal reservado, 25 % posterior al corte 2026-02-19T12:47:55)
Los tres cargan correctamente (huella, scikit-learn 1.7.0 y Python 3.12.10 verificados) y puntúan con valores finitos. Resultados reproducibles (misma
`resultados_sha256` en ejecuciones repetidas: `a563e7f7dff458f2…`).

| Candidato | Variables | VP / FP / VN / FN | Precisión | Sensibilidad | FPR | AUC-ROC |
|---|---|---|---|---|---|---|
| Transacciones (`iforest-tx-…`) | 12 | 87 / 25 / 1.644 / 73 | 77,7 % | 54,4 % | 1,5 % | 0,949 |
| Accesos (`iforest-acc-…`) | 6 | 26 / 25 / 899 / 106 | 51,0 % | 19,7 % | 2,7 % | 0,901 |
| Clásico (`iforest-…`) | 4 | 64 / 27 / 1.642 / 96 | 70,3 % | 40,0 % | 1,6 % | 0,858 |

Las reglas actuales del motor, con la alerta amplia (INTERMEDIO o más), rinden igual o mejor (transacciones: precisión 81,5 %, sensibilidad 63,1 %). El modelo de
accesos es débil y ningún modelo detecta los fraudes sigilosos ni los de ingeniería social. Detalle y definiciones en el informe de evaluación.

## 5. Modelo activo y modo de funcionamiento
* **No existe ningún modelo activo.** `anomaly.status()` devuelve `available: false`, `active_version: null`, `reason: sin_modelo_activo`; no hay `current.txt`
  en `datasets_sinteticos/modelos_candidatos/` ni en `instance/fraud_models/` (esta última ni siquiera existe).
* El valor predeterminado de `FRAUD_MODE` es **`shadow`** (comprobado al crear la aplicación). No se activó `enforce`.
* Los candidatos por tipo de evento **no pueden activarse** (el inferidor actual usa otras 4 variables; la puerta de activación lo impide). El clásico supera las puertas de
  cordura pero **no se activó**. La activación es solo por línea de órdenes y es una decisión humana.

## 6. Pruebas (resultados reales)
| Prueba | Resultado |
|---|---|
| Grupos antifraude en orden (dataset; cargador, entrenamiento y evaluación; motor y accesos; integración; panel y exportaciones; robustez) | 41 + 90 + 56 + 12 + 149 + 26 = **374 pasan**, 0 fallos |
| pytest completo | **1.045 pasan, 1 omitida** (enlaces simbólicos de Nori en Windows), 0 fallos (18 min 32 s) |
| E2E, 1.ª pasada completa | 80 pasan, 1 falla: contador desactualizado en `zz-fraude-admin` (mi prueba; corregido de 6 a 7 descargas) |
| E2E, 2.ª pasada completa | 80 pasan, 1 falla: `nori-rag.spec.cjs:82` (ver pendiente 1) |
| `nori-rag.spec.cjs` ejecutado solo | 9 de 9 pasan |
| E2E, 3.ª pasada completa (log guardado) | **81 de 81 pasan** (9,0 min) |

Cambios en pruebas ajenas al antifraude: únicamente un tiempo límite explícito de 120 s en `e2e/nori.spec.cjs` y `e2e/nori-perf.spec.cjs` (sin tocar aserciones).

## 7. Restricciones cumplidas
* `data/` no inspeccionada ni modificada (ningún comando se ejecutó contra ella; el código rechaza esa ruta y hay pruebas que lo verifican).
* Sin commits, push, merge, publicación ni despliegues. Única consulta externa: `git ls-remote` de solo lectura para comprobar que no existe rama remota de fraude.
* Sin migraciones sobre bases existentes o compartidas (SQLite en memoria o bases temporales nuevas). A fecha 2026-10-12 la migración `f7a1c4d9e620` no estaba aplicada en ningún entorno (ya lo está en staging; ver la actualización del 2026-10-13).
* Sin datos bancarios reales. Sin sanciones permanentes automáticas. `.gitignore` y `.railwayignore` sin tocar.
* Cambios existentes conservados: 13 archivos modificados y 29 elementos sin seguimiento; `git diff --check` limpio.
* Nota: el reflog del worktree contiene una entrada `reset: moving to HEAD` cuyo origen no he podido atribuir; no es un commit y todos los cambios siguen presentes.

## 8. Pendientes reales
1. **Fallo intermitente de `nori-rag.spec.cjs:82`** («Nori solo contacta al propio servidor: ninguna petición a servicios externos o de IA»). Falló en 1 de 3 ejecuciones
   completas, pasó sola (9/9) y pasó en la 3.ª completa. **Causa no identificada**: el mensaje de error se perdió en esa ejecución. Hipótesis sin verificar: carrera de tiempos
   bajo carga (la prueba cuenta peticiones tras enviar dos mensajes). No se modificó código de Nori. Si reaparece, conservar el mensaje y los artefactos completos
   (`--output` aparte) antes de reintentar.
2. Se versionan el CSV, su manifiesto y los informes de evaluación (con la ruta redactada). Los modelos candidatos (`*.joblib` y sus `.json`) se conservan en local pero
   quedan fuera de Git (`.gitignore`) mientras no se valide su compatibilidad con Railway (Python 3.13); allí hay que reentrenarlos.
3. Antes de cualquier uso real: autorizar y aplicar la migración `f7a1c4d9e620` en staging *antes* de desplegar; reentrenar en Python 3.13 (Railway); ampliar la inferencia
   (`anomaly.vector_from_features`) si se quieren usar los modelos por tipo de evento; observar en `shadow` con datos reales.
4. Decidir cuándo borrar `fraud-e2e-full-run3.log` y `fraud-e2e-results\` (conservados a petición; no se publican).
