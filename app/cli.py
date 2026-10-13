"""Comandos de mantenimiento (``flask --app run <comando>``)."""
import click

from .services.capture_service import purge_expired_captures


def register_cli(app):
    register_fraud_cli(app)
    register_admin_cli(app)
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


def register_fraud_cli(app):
    """Comandos del detector de anomalías. Se ejecutan fuera de la aplicación web y nunca durante una operación bancaria."""
    import json
    import click
    from .fraud import anomaly, csv_training, event_training, metrics, service, synthetic

    def _refuse(error):
        click.echo(f'No se realizó la acción: {error}')
        raise SystemExit(1)

    @app.cli.command('fraud-train')
    @click.option('--csv', 'csv_path', type=click.Path(dir_okay=False), help='CSV de entrenamiento y evaluación (ver docs/fraude.md).')
    @click.option('--from-db', 'from_db', is_flag=True, help='Entrena con las salidas de dinero recientes de la base (sin etiquetas).')
    @click.option('--manifest', 'manifest_path', type=click.Path(dir_okay=False), help='Manifiesto JSON con la procedencia del CSV (solo con --csv).')
    @click.option('--notes', default=None, help='Nota breve que se guarda con la versión (solo con --csv).')
    def fraud_train(csv_path, from_db, manifest_path, notes):
        """Entrena el detector y registra una versión CANDIDATA. No cambia el modelo activo: hay que activarla aparte."""
        if bool(csv_path) == bool(from_db):
            _refuse('indica exactamente una fuente: --csv RUTA o --from-db.')
        try:
            row = (csv_training.train_from_csv(csv_path, manifest_path=manifest_path, notes=notes) if csv_path
                   else anomaly.train_from_database())
        except (csv_training.CsvValidationError, anomaly.InsufficientData) as error:
            _refuse(error)
        click.echo(f'Candidato {row.version} registrado ({row.source}): {row.n_train} filas de entrenamiento y {row.n_eval} de evaluación.')
        click.echo('Para usarlo en observación: flask --app run fraud-model-activate ' + row.version)

    @app.cli.command('fraud-train-events')
    @click.argument('csv_path', type=click.Path(dir_okay=False))
    @click.option('--report-dir', type=click.Path(file_okay=False), default=None, help='Carpeta donde escribir el informe de evaluación (JSON y Markdown).')
    @click.option('--flag-rate', default=0.05, show_default=True, type=click.FloatRange(0.001, 0.5),
                  help='Tasa de alertas a priori para el umbral (percentil de las puntuaciones del periodo de entrenamiento).')
    def fraud_train_events(csv_path, report_dir, flag_rate):
        """Entrena y evalúa candidatos (transacciones, accesos y clásico) desde un CSV combinado. No activa nada."""
        try:
            report = event_training.run_pipeline(csv_path, report_dir=report_dir, flag_rate=flag_rate)
        except (event_training.EventDatasetError, csv_training.CsvValidationError, anomaly.InsufficientData) as error:
            _refuse(error)
        for name in ('transacciones', 'accesos', 'candidato_clasico'):
            part = report['modelos'][name]
            click.echo(f"Candidato {name}: {part['version']} ({part['filas_entrenamiento']} entrenamiento / {part['filas_evaluacion']} evaluación)")
        click.echo('Ninguno está activo. Informe: ' + (str(report_dir) if report_dir else '(no se escribió)'))

    @app.cli.command('fraud-model-activate')
    @click.argument('version')
    def fraud_model_activate(version):
        """Activa una versión (solo observación) si supera las puertas de validación; archiva la activa anterior."""
        try:
            anomaly.activate_version(version)
        except (ValueError, anomaly.ActivationRefused) as error:
            _refuse(error)
        click.echo(f'Versión activa: {version}.')

    @app.cli.command('fraud-model-reject')
    @click.argument('version')
    @click.option('--reason', required=True)
    def fraud_model_reject(version, reason):
        """Rechaza una versión candidata."""
        try:
            anomaly.reject_version(version, reason)
        except ValueError as error:
            _refuse(error)
        click.echo(f'Versión {version} rechazada.')

    @app.cli.command('fraud-model-deactivate')
    def fraud_model_deactivate():
        """Retira el modelo activo: el motor sigue solo con reglas (reversión rápida)."""
        anomaly.deactivate()
        click.echo('Modelo retirado. El motor funciona solo con reglas.')

    @app.cli.command('fraud-model-status')
    def fraud_model_status():
        """Muestra si hay un modelo cargable y las versiones registradas."""
        click.echo(json.dumps(anomaly.status(), indent=2, ensure_ascii=False))

    @app.cli.command('fraud-synthetic-csv')
    @click.argument('destination', type=click.Path(dir_okay=False))
    @click.option('--accounts', default=40, show_default=True, type=int)
    @click.option('--days', default=30, show_default=True, type=int)
    @click.option('--seed', default=7, show_default=True, type=int)
    @click.option('--labelled', is_flag=True, help='Etiqueta las anomalías inyectadas (verdad de referencia SINTÉTICA, solo para pruebas).')
    def fraud_synthetic_csv(destination, accounts, days, seed, labelled):
        """Genera un CSV SINTÉTICO de ejemplo (nunca datos reales y nunca dentro de data/)."""
        try:
            target = synthetic.write_csv(synthetic.generate(n_accounts=accounts, days=days, seed=seed, labelled=labelled), destination)
        except csv_training.CsvValidationError as error:
            _refuse(error)
        click.echo(f'CSV sintético escrito en {target}.')

    @app.cli.command('fraud-report')
    @click.option('--days', default=7, show_default=True, type=int)
    def fraud_report(days):
        """Resumen de observación: alertas por nivel, fallos, latencia, incidentes revisados y reglas frente al modelo."""
        click.echo(json.dumps(metrics.summary(max(1, days)), indent=2, ensure_ascii=False, default=str))

    @app.cli.command('fraud-maintenance')
    @click.option('--dry-run', is_flag=True, help='No borra evaluaciones antiguas.')
    def fraud_maintenance(dry_run):
        """Marca como vencidas las restricciones y retenciones caducadas y purga evaluaciones antiguas sin incidente."""
        from .models import db
        restrictions, holds = service.expire_stale()
        db.session.commit()
        click.echo(f'{restrictions} restricción(es) y {holds} retención(es) marcadas como vencidas.')
        if not dry_run:
            removed = metrics.purge_old_evaluations(app.config['FRAUD_EVALUATION_RETENTION_DAYS'])
            click.echo(f'{removed} evaluación(es) antiguas eliminadas (las vinculadas a un incidente se conservan).')


def register_admin_cli(app):
    @app.cli.command('admin-bootstrap')
    @click.option('--email', required=True, help='Correo de la persona que será Administradora General (recibirá el enlace de activación).')
    @click.option('--nombre', 'full_name', required=True, help='Nombre completo.')
    @click.option('--confirmo-identidad', is_flag=True,
                  help='Declaras que verificaste la identidad de esta persona por un canal independiente (obligatorio).')
    def admin_bootstrap(email, full_name, confirmo_identidad):
        """Aprovisionamiento INICIAL y restringido del primer Administrador General.

        Solo funciona desde un servidor con acceso a la base, se niega si ya hay un Administrador General activo o pendiente y nunca promociona una cuenta existente.
        No imprime ni guarda contraseñas ni enlaces: envía un enlace de un solo uso al correo y la propia persona define su contraseña.
        """
        from .models import db
        from .services import admin_service as admins
        from .services.email_service import EmailDeliveryError, send_admin_activation_link
        if not confirmo_identidad:
            raise click.ClickException('Falta --confirmo-identidad: confirma que verificaste la identidad de la persona por un canal independiente.')
        click.echo(f'Se creará un Administrador General PENDIENTE para {email.strip().lower()} en la base configurada ({app.config.get("APP_ENV")}).')
        click.confirm('¿Continuar?', abort=True)
        try:
            user, token = admins.bootstrap_general(full_name, email)
            db.session.flush()
            send_admin_activation_link(user.email, admins.activation_link(token), app.config['ADMIN_ACTIVATION_TTL_SECONDS'] // 3600)
        except admins.AdminError as error:
            db.session.rollback()
            raise click.ClickException(str(error))
        except EmailDeliveryError:
            db.session.rollback()
            raise click.ClickException('No se pudo enviar el correo de activación; no se creó la cuenta. Revisa la configuración de correo.')
        db.session.commit()
        click.echo('Cuenta creada en estado PENDIENTE y enlace de activación enviado al correo indicado (válido por '
                   f'{app.config["ADMIN_ACTIVATION_TTL_SECONDS"] // 3600} horas). La persona define su contraseña desde el enlace; al hacerlo la cuenta queda activa.')
