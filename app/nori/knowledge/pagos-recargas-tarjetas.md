---
title: Pagos, recargas, traslados y tarjetas
url: /herramientas
status: verificado
sources: app/routes/__init__.py (operation, internal_transfer, cards, update_card_status); app/services/banking_service.py
---

## Pago de servicios
Puedes registrar el pago de cuatro servicios de demostración: Electricidad, Agua, Internet y Tarjeta académica. Se confirma con el código enviado a tu correo y con la cámara. No se envía ningún pago a empresas reales.

## Recarga de celular
Para una recarga escribes un número de celular de 7 a 15 dígitos y el monto, y confirmas con el código del correo y la cámara. La recarga es de demostración y no tiene efectos fuera de Norte.

## Traslado entre tus cuentas
El traslado interno mueve saldo entre dos cuentas activas que están a tu nombre y tienen la misma moneda. Necesitas al menos dos cuentas activas; Norte no abre cuentas automáticamente desde esa pantalla. Se confirma con el código del correo y con la cámara.

## Tarjetas de muestra
En «Tarjetas» ves las tarjetas de muestra asociadas a tu cuenta, su estado y un límite referencial. Puedes bloquear y desbloquear una tarjeta. No sirven para compras ni retiros: el cambio de estado solo afecta a Norte.
