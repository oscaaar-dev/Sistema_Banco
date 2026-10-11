"""Configuración segura y aislada para la suite de pruebas."""

import os


# Debe ejecutarse antes de que los módulos de prueba importen ``app``. Así la
# suite nunca toma una URL MySQL que pueda existir en el entorno local.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.pop("MYSQL_URL", None)
os.environ["APP_ENV"] = "testing"
