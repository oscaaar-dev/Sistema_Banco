# Fase 7 — muestra para aprobación

Estado: propuesta de revisión. No se ejecutó `seed_demo`, no se insertaron registros ni se cambió ninguna base de datos.

## Datos sintéticos propuestos

Los cinco perfiles y movimientos de esta muestra son inventados para revisión. `is_seed=true` se propone para cada usuario, cuenta, tarjeta, beneficiario y movimiento; todavía no existe esa columna en los modelos actuales. Los correos usan el dominio reservado `.test`. No se muestran DNI, teléfonos ni números completos de cuenta o tarjeta.

| ID interno | Cliente sintético | Correo de muestra | Cuenta | Saldo inicial | Saldo al cierre | is_seed |
| --- | --- | --- | --- | ---: | ---: | --- |
| SEED-001 | Lucía Torres Rojas | seed-001@example.test | Cuenta Norte ···· 0101 | S/ 6,234.70 | S/ 14,456.14 | true |
| SEED-002 | Mateo Quispe Salazar | seed-002@example.test | Cuenta Norte ···· 0202 | S/ 4,878.90 | S/ 11,768.70 | true |
| SEED-003 | Valeria Paredes Huamán | seed-003@example.test | Cuenta Norte ···· 0303 | S/ 8,021.35 | S/ 15,501.75 | true |
| SEED-004 | Diego Salazar Ríos | seed-004@example.test | Cuenta Norte ···· 0404 | S/ 3,592.65 | S/ 9,885.05 | true |
| SEED-005 | Camila Huamán Quispe | seed-005@example.test | Cuenta Norte ···· 0505 | S/ 9,314.20 | S/ 16,767.75 | true |

Cada fila de movimiento muestra el saldo posterior. Al sumar los importes al saldo inicial se obtiene exactamente el saldo de cierre indicado arriba. Los comercios y servicios son genéricos; no representan cargos reales.

| Cliente | Fecha | Movimiento | Importe | Saldo posterior |
| --- | --- | --- | ---: | ---: |
| SEED-001 | 03/03/2026 | Abono de ingresos | + S/ 4,725.35 | S/ 10,960.05 |
| SEED-001 | 12/03/2026 | Servicio de electricidad | − S/ 212.68 | S/ 10,747.37 |
| SEED-001 | 05/04/2026 | Mercado local | − S/ 286.43 | S/ 10,460.94 |
| SEED-001 | 02/05/2026 | Abono de ingresos | + S/ 4,725.35 | S/ 15,186.29 |
| SEED-001 | 16/06/2026 | Telefonía e internet | − S/ 89.90 | S/ 15,096.39 |
| SEED-001 | 25/07/2026 | Transferencia a beneficiario de muestra | − S/ 640.25 | S/ 14,456.14 |
| SEED-002 | 06/03/2026 | Abono de ingresos | + S/ 3,884.60 | S/ 8,763.50 |
| SEED-002 | 09/03/2026 | Servicio de agua | − S/ 54.70 | S/ 8,708.80 |
| SEED-002 | 19/04/2026 | Supermercado | − S/ 318.45 | S/ 8,390.35 |
| SEED-002 | 06/05/2026 | Abono de ingresos | + S/ 3,884.60 | S/ 12,274.95 |
| SEED-002 | 11/06/2026 | Telefonía | − S/ 79.90 | S/ 12,195.05 |
| SEED-002 | 21/07/2026 | Compras del hogar | − S/ 426.35 | S/ 11,768.70 |
| SEED-003 | 02/03/2026 | Abono de ingresos | + S/ 5,210.25 | S/ 13,231.60 |
| SEED-003 | 04/03/2026 | Pago de vivienda | − S/ 1,650.75 | S/ 11,580.85 |
| SEED-003 | 12/04/2026 | Servicio de electricidad | − S/ 181.30 | S/ 11,399.55 |
| SEED-003 | 04/05/2026 | Abono de ingresos | + S/ 5,210.25 | S/ 16,609.80 |
| SEED-003 | 16/06/2026 | Mercado local | − S/ 387.60 | S/ 16,222.20 |
| SEED-003 | 28/07/2026 | Transferencia a beneficiario de muestra | − S/ 720.45 | S/ 15,501.75 |
| SEED-004 | 05/03/2026 | Abono de ingresos | + S/ 3,428.90 | S/ 7,021.55 |
| SEED-004 | 16/03/2026 | Servicios del hogar | − S/ 156.20 | S/ 6,865.35 |
| SEED-004 | 18/04/2026 | Mercado local | − S/ 244.80 | S/ 6,620.55 |
| SEED-004 | 05/05/2026 | Abono de ingresos | + S/ 3,428.90 | S/ 10,049.45 |
| SEED-004 | 07/06/2026 | Telefonía | − S/ 69.90 | S/ 9,979.55 |
| SEED-004 | 20/07/2026 | Internet | − S/ 94.50 | S/ 9,885.05 |
| SEED-005 | 08/03/2026 | Abono de ingresos | + S/ 4,682.15 | S/ 13,996.35 |
| SEED-005 | 10/03/2026 | Telefonía | − S/ 84.90 | S/ 13,911.45 |
| SEED-005 | 23/04/2026 | Supermercado | − S/ 535.70 | S/ 13,375.75 |
| SEED-005 | 08/05/2026 | Abono de ingresos | + S/ 4,682.15 | S/ 18,057.90 |
| SEED-005 | 15/06/2026 | Compras del hogar | − S/ 129.35 | S/ 17,928.55 |
| SEED-005 | 19/07/2026 | Transferencia a beneficiario de muestra | − S/ 1,160.80 | S/ 16,767.75 |

La generación completa propuesta cubriría de 6 a 12 meses por perfil, con depósitos y pagos recurrentes separados por movimiento, fechas no repetidas, beneficiarios y cuentas enlazados, y saldos calculados desde cada movimiento. Esta muestra abrevia esos historiales y no se ha cargado al sistema.

## Tres textos institucionales propuestos

1. **Portada**

   “Conoce Norte, una experiencia digital clara.”

   “Explora un resumen de cuenta, movimientos de ejemplo y recorridos por las funciones disponibles. La información se registra dentro de Norte; las operaciones no producen efectos financieros.”

2. **Herramientas**

   “Herramientas para conocer la experiencia.”

   “Consulta saldos de ejemplo, revisa movimientos y tarjetas asociadas al perfil, y recorre formularios de transferencias, pagos y recargas. No hay conexión con cuentas ni comercios externos.”

3. **Privacidad y cámara**

   “Conoce qué ocurre cuando activas la cámara.”

   “Con tu permiso del navegador, Norte envía fotogramas al servidor para analizar encuadre y visibilidad. El código actual no los guarda como archivos. Antes de usar esta función en operaciones se debe definir consentimiento informado, retención y una alternativa sin cámara.”

La tercera propuesta describe el comportamiento actual y deja explícito el trabajo de privacidad pendiente. No se modifica aquí el flujo de cámara.

## Hallazgos de implementación pendientes

- Los modelos actuales no tienen `is_seed`; `Account.number` acepta hasta 24 caracteres, pero no existe un campo CCI. La muestra no cambia el esquema.
- No se ejecutó ningún seed ni se aplicó migración. Cualquier migración de una base distinta a SQLite de pruebas requiere la parada y autorización indicadas.
- La operación de cámara transmite fotogramas al servidor sin una pantalla de consentimiento informado propia. El diseño de privacidad permanece bloqueado para la Fase 4.
