---
title: Contactos y transferencias
url: /herramientas
status: verificado
sources: app/services/contact_service.py; app/routes/__init__.py (contacts, operation, send_transfer_code); app/services/validation.py; app/services/banking_service.py
---

## Agregar un contacto
Para transferir a otro cliente de Norte primero lo agregas como contacto desde «Agregar contacto», en la pantalla de transferencia. Escribes su DNI, su número de cuenta o su CCI, Norte muestra su nombre parcialmente oculto (por ejemplo, Ricardo O*** V***) y confirmas antes de guardar.

## Reglas de los contactos
El cliente debe existir y estar activo, no puedes agregarte a ti mismo y no puedes agregar dos veces al mismo contacto. Por seguridad, las búsquedas están limitadas a pocas por periodo y el mensaje de error no revela si una cuenta existe. Puedes eliminar un contacto cuando quieras.

## Hacer una transferencia
Eliges un contacto, escribes el monto y confirmas con un código enviado a tu correo y con la cámara. El monto mínimo es S/ 0.01 y el máximo por operación es S/ 10,000.00. Si el destinatario es cliente de Norte, el movimiento queda registrado en su cuenta como ingreso.

## Código y cámara de las operaciones
El código de confirmación llega al correo registrado, vence a los 5 minutos y admite hasta 5 intentos; puedes pedir otro después de 60 segundos. La cámara es obligatoria: debes aceptar el aviso y se toma una foto al confirmar. Toda operación es de demostración y no mueve dinero real.
