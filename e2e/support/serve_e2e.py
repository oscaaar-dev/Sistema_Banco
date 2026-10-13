"""Servidor de las pruebas E2E: igual que ``run.py`` pero con cuentas administrativas de PRUEBA en la base **en memoria**.

Solo existe para Playwright. Se niega a arrancar si no es el entorno de pruebas con SQLite en memoria, de modo que estas cuentas
(con una contraseña conocida y pública) no pueden crearse nunca en una base real. La aplicación no tiene ningún usuario administrador
sembrado de este modo: ver ``docs/roles-administrativos.md`` para el aprovisionamiento real (``flask admin-bootstrap``).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if os.getenv('APP_ENV', '').strip().lower() != 'testing' or os.getenv('DATABASE_URL', '').strip() != 'sqlite://':
    raise SystemExit('serve_e2e.py solo se ejecuta con APP_ENV=testing y DATABASE_URL=sqlite:// (base en memoria de las pruebas E2E).')

from app import create_app  # noqa: E402
from app.models import AdminPermission, User, db  # noqa: E402
from app.services.admin_service import PERMISSIONS  # noqa: E402

E2E_PASSWORD = 'E2E-solo-pruebas.1'                    # solo para la base en memoria de Playwright


def _account(username, role, permissions=()):
    user = User(username=username, full_name=f'Prueba {username}', email=f'{username}@example.test', role=role)
    user.set_password(E2E_PASSWORD)
    db.session.add(user)
    db.session.flush()
    db.session.add_all([AdminPermission(user_id=user.id, permission=code) for code in permissions])


app = create_app()
with app.app_context():
    _account('e2e_general', 'ADMIN_GENERAL')                                  # id 3
    _account('e2e_sinpermisos', 'ADMINISTRADOR')                              # id 4
    _account('e2e_admin', 'ADMINISTRADOR', PERMISSIONS)                       # id 5 (se suspende en la prueba)
    db.session.commit()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', '5010')), debug=False, threaded=False)   # SQLite en memoria: una sola conexión
