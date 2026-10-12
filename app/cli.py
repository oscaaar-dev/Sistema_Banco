"""Comandos de mantenimiento (``flask --app run <comando>``)."""
import click

from .services.capture_service import purge_expired_captures


def register_cli(app):
    @app.cli.command('purge-captures')
    @click.option('--days', type=click.IntRange(min=1), default=None,
                  help='Días de conservación. Por defecto, CAPTURE_RETENTION_DAYS (90).')
    @click.option('--dry-run', is_flag=True, help='Solo cuenta cuántas capturas vencidas hay, sin borrarlas.')
    def purge_captures(days, dry_run):
        """Borra las fotos de operaciones que superaron el plazo de conservación del aviso de privacidad."""
        from datetime import timedelta
        from .models import OperationCapture, now_utc
        retention = days or app.config['CAPTURE_RETENTION_DAYS']
        if dry_run:
            cutoff = now_utc() - timedelta(days=retention)
            count = OperationCapture.query.filter(OperationCapture.created_at < cutoff).count()
            click.echo(f'{count} captura(s) superan los {retention} días (no se borró nada).')
            return
        click.echo(f'{purge_expired_captures(retention)} captura(s) eliminadas por superar los {retention} días.')
