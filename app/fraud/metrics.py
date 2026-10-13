"""Métricas de observación del sistema de fraude (para el panel y el CLI ``fraud-report``).

Solo cuentan lo que se registró: no hay etiquetas fiables de fraude, así que **no se calcula ninguna precisión, sensibilidad ni tasa
de aciertos**. Lo que sí se mide: volumen de alertas por nivel, porcentaje de operaciones que piden más verificación, fallos de
evaluación, latencia, distribución de puntuaciones, incidentes revisados (confirmados y descartados) y diferencias entre las
reglas y el detector de anomalías.
"""
from datetime import timedelta

from sqlalchemy import func

from ..models import FraudIncident, HeldOperation, RiskEvaluation, SecurityRestriction, db, now_utc
from . import constants as c

MAX_ROWS = 5000


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def summary(days=7):
    now = now_utc()
    since = now - timedelta(days=days)
    base = db.session.query(RiskEvaluation).filter(RiskEvaluation.created_at >= since)
    by_level = dict(db.session.query(RiskEvaluation.level, func.count(RiskEvaluation.id))
                    .filter(RiskEvaluation.created_at >= since).group_by(RiskEvaluation.level).all())
    total = sum(by_level.values())
    evaluated = total - by_level.get(c.LEVEL_NO_EVALUADO, 0)
    needing_verification = by_level.get(c.LEVEL_INTERMEDIO, 0) + by_level.get(c.LEVEL_SOSPECHOSO, 0)

    rows = (db.session.query(RiskEvaluation.duration_ms, RiskEvaluation.score, RiskEvaluation.level, RiskEvaluation.model_score)
            .filter(RiskEvaluation.created_at >= since).order_by(RiskEvaluation.created_at.desc()).limit(MAX_ROWS).all())
    durations = [row[0] for row in rows if row[0] is not None]
    scores = [row[1] for row in rows]
    buckets = {'0-24': 0, '25-54': 0, '55-100': 0}
    for score in scores:
        buckets['0-24' if score < 25 else '25-54' if score < 55 else '55-100'] += 1

    with_model = [(row[3], row[2]) for row in rows if row[3] is not None]
    disagreement = None
    if len(with_model) >= 10:
        cutoff = _percentile([value for value, _ in with_model], 0.10)
        low_model = [level for value, level in with_model if value <= cutoff]
        disagreement = {'compared': len(with_model), 'model_lowest_decile': len(low_model),
                        'of_those_rules_normal': sum(1 for level in low_model if level == c.LEVEL_NORMAL),
                        'rules_suspicious_total': sum(1 for _, level in with_model if level == c.LEVEL_SOSPECHOSO)}

    incidents = dict(db.session.query(FraudIncident.status, func.count(FraudIncident.id))
                     .filter(FraudIncident.created_at >= since).group_by(FraudIncident.status).all())
    reviewed = incidents.get(c.INCIDENT_CONFIRMED, 0) + incidents.get(c.INCIDENT_DISCARDED, 0)
    return {
        'days': days, 'total': total, 'by_level': by_level,
        'error_rate': (by_level.get(c.LEVEL_NO_EVALUADO, 0) / total) if total else None,
        'pct_needing_verification': (needing_verification / evaluated) if evaluated else None,
        'latency_ms': {'mean': round(sum(durations) / len(durations), 2) if durations else None,
                       'p95': _percentile(durations, 0.95), 'max': max(durations) if durations else None},
        'score_buckets': buckets, 'model_vs_rules': disagreement,
        'incidents_by_status': incidents,
        'false_positives_reviewed': incidents.get(c.INCIDENT_DISCARDED, 0), 'confirmed': incidents.get(c.INCIDENT_CONFIRMED, 0),
        'review_ratio': (reviewed / sum(incidents.values())) if incidents else None,
        'rules_versions': sorted({row[0] for row in db.session.query(RiskEvaluation.rules_version).filter(RiskEvaluation.created_at >= since).distinct()}),
        'model_versions': sorted({row[0] for row in db.session.query(RiskEvaluation.model_version)
                                  .filter(RiskEvaluation.created_at >= since, RiskEvaluation.model_version.isnot(None)).distinct()}),
        'modes': sorted({row[0] for row in db.session.query(RiskEvaluation.mode).filter(RiskEvaluation.created_at >= since).distinct()}),
        'active_restrictions': SecurityRestriction.query.filter(SecurityRestriction.status == c.RESTRICTION_ACTIVE,
                                                                SecurityRestriction.expires_at > now).count(),
        'held_operations': HeldOperation.query.filter(HeldOperation.status == c.HOLD_HELD, HeldOperation.expires_at > now).count(),
    }


def purge_old_evaluations(retention_days):
    """Elimina evaluaciones antiguas que no estén vinculadas a un incidente (las vinculadas se conservan como evidencia)."""
    cutoff = now_utc() - timedelta(days=retention_days)
    linked = db.session.query(FraudIncident.evaluation_id).filter(FraudIncident.evaluation_id.isnot(None))
    held = db.session.query(HeldOperation.evaluation_id).filter(HeldOperation.evaluation_id.isnot(None))
    removed = (RiskEvaluation.query
               .filter(RiskEvaluation.created_at < cutoff, ~RiskEvaluation.id.in_(linked), ~RiskEvaluation.id.in_(held))
               .delete(synchronize_session=False))
    db.session.commit()
    return removed
