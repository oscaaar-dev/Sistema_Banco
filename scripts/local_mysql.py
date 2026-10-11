"""Rutas de la instancia MySQL local dedicada de Norte, compartidas por los scripts de esta carpeta.

Nada depende del usuario ni del equipo: cada valor se puede sustituir con una variable de entorno.

- ``NORTE_MYSQL_ROOT``: carpeta de datos, logs y archivos privados de la instancia.
- ``MYSQL_BIN``: carpeta ``bin`` de MySQL Server (donde está ``mysqld``).
"""
import os
import shutil
from pathlib import Path

# Carpeta estándar del instalador de MySQL 8.4 en Windows; solo se usa si no hay MYSQL_BIN ni mysqld en el PATH.
_WINDOWS_INSTALLER_BIN = Path(r"C:\Program Files\MySQL\MySQL Server 8.4\bin")


def instance_root() -> Path:
    override = os.environ.get("NORTE_MYSQL_ROOT")
    if override:
        return Path(override)
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or Path.home() / ".local" / "share"
    )
    return Path(base) / "NorteBcpMySQL"


def mysql_bin_dir() -> Path:
    override = os.environ.get("MYSQL_BIN")
    if override:
        return Path(override)
    found = shutil.which("mysqld")
    if found:
        return Path(found).resolve().parent
    return _WINDOWS_INSTALLER_BIN


def mysqld_path() -> Path:
    return mysql_bin_dir() / ("mysqld.exe" if os.name == "nt" else "mysqld")
