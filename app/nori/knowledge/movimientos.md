---
title: Movimientos y estado de cuenta en PDF
url: /herramientas
status: verificado
sources: app/routes/__init__.py (movements, account_statement); app/services/statement_pdf.py; app/services/statement_service.py
---

## Consultar movimientos
En «Movimientos» ves el historial de tu cuenta, con 20 registros por página. Puedes filtrar por tipo (ingresos, transferencias, pagos o recargas) y por un rango de fechas desde y hasta, y limpiar los filtros cuando quieras.

## Descargar el estado de cuenta
Desde Movimientos o desde Cuentas descargas el estado de cuenta en PDF. El PDF respeta los filtros de tipo y de fechas que elegiste y no se guarda en el servidor: se genera al momento.

## Contenido del PDF
El PDF es de tamaño A4 e incluye el nombre del cliente, el DNI enmascarado, la cuenta terminada en sus cuatro últimos dígitos, el periodo, la fecha de emisión, el saldo inicial, el total de ingresos, el total de egresos y el saldo final. La tabla de movimientos muestra los ingresos en verde y repite el encabezado en cada página. El pie incluye el aviso legal de Norte y la numeración de páginas.
