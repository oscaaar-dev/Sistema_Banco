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

    @app.cli.command('purge-nori-audit')
    @click.option('--dry-run', is_flag=True, help='Solo cuenta cuántos registros vencidos hay, sin borrarlos.')
    def purge_nori_audit(dry_run):
        """Borra los registros de auditoría de Nori (IP con hash y hora, sin texto) con más de 24 horas."""
        from .models import AuditLog, now_utc
        from .nori.safety import AUDIT_ACTION, AUDIT_RETENTION, purge_expired_audit
        if dry_run:
            count = AuditLog.query.filter(AuditLog.action == AUDIT_ACTION, AuditLog.created_at < now_utc() - AUDIT_RETENTION).count()
            click.echo(f'{count} registro(s) de Nori superan las 24 horas (no se borró nada).')
            return
        click.echo(f'{purge_expired_audit(force=True)} registro(s) de Nori eliminados por superar las 24 horas.')

    @app.cli.command('nori-index')
    @click.option('--check', is_flag=True, help='Termina con error si algún documento no se pudo indexar o no hay fragmentos.')
    def nori_index(check):
        """Reconstruye el índice documental local de Nori y muestra qué se indexó (sin enviar nada a Internet)."""
        from collections import Counter
        from .nori import knowledge, rag
        rag.reset_index()
        index = rag.get_index()
        if index is None:
            click.echo('No se pudo construir el índice documental (revisa los avisos anteriores).')
            raise SystemExit(1)
        per_source = Counter(f'{chunk.title}  [{chunk.origin}]' for chunk in index.chunks)
        click.echo(f'{len(index.chunks)} fragmentos de {len(per_source)} fuentes, construido en {index.built_in:.2f} s.')
        for source, count in sorted(per_source.items()):
            click.echo(f'  {count:>3}  {source}')
        expected = {path.stem for path in knowledge.KNOWLEDGE_DIR.glob('*.md') if path.name.lower() != 'readme.md'}
        indexed = {chunk.id.split('#')[0] for chunk in index.chunks if chunk.origin == 'documento'}
        skipped = sorted(expected - indexed)
        if skipped:
            click.echo('Documentos NO indexados (sin status: verificado, con secretos o con url inválida): ' + ', '.join(skipped))
        if check and (skipped or not index.chunks):
            raise SystemExit(1)
