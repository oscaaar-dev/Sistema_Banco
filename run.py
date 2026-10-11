"""Punto de entrada único: `python run.py` en desarrollo y `gunicorn run:app` en producción."""
import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(
        host=os.getenv('HOST', '127.0.0.1'),
        port=int(os.getenv('PORT', '5000')),
        debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true',
        # SQLite en memoria comparte una única conexión durante las pruebas
        # E2E. El servidor con hilos puede usarla desde dos requests simultáneas.
        threaded=os.getenv('APP_ENV', '').strip().lower() != 'testing',
    )
