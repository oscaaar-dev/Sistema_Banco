---
title: Cuentas, saldo y datos para recibir dinero
url: /herramientas
status: verificado
sources: app/templates/accounts.html; app/services/account_service.py; app/services/account_identifiers.py; app/routes/__init__.py (accounts)
---

## Pantalla Cuentas
En «Cuentas» ves tu cuenta de ahorros con su número enmascarado, la moneda, el estado, la fecha de apertura y el saldo disponible. Puedes ocultar o mostrar el saldo con un botón. Si tienes más de una cuenta, eliges cuál ver.

## Datos para recibir dinero
La pantalla muestra el número de cuenta completo y un CCI de 20 dígitos, ambos con botón para copiarlos. El CCI es ficticio y solo sirve para la demostración. Otro cliente de Norte puede agregarte como contacto con tu DNI, tu número de cuenta o tu CCI.

## Resumen del mes
«Cuentas» muestra el total de ingresos y de gastos del mes y un gráfico sencillo por día. Puedes cambiar de mes con las flechas. Los datos salen de tus movimientos registrados.

## Últimos movimientos y tarjeta
Ves tus 5 últimos movimientos con un enlace para ver todos, y la tarjeta de débito de muestra asociada a la cuenta, si existe. Desde esta pantalla también puedes descargar el estado de cuenta en PDF.
