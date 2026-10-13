"""Generador de un dataset SINTÉTICO combinado de transacciones y accesos (todo ficticio; nada real ni leído de ``data/``).

Cada evento nace de un **escenario documentado** (episodio) cuyo resultado ``is_fraud`` lo decide el propio escenario, no una
puntuación ni un umbral: la etiqueta se fija al elegir el escenario y antes de calcular ninguna variable observable. Las variables
observables se muestrean con probabilidades por escenario que **se solapan** a propósito (hay fraudes con una sola señal débil y
operaciones legítimas con varias señales), de modo que ninguna variable aislada separa las clases.

Coherencia garantizada por construcción y comprobada por ``dataset_validation``:
* los eventos están ordenados cronológicamente y los saldos se simulan **cuenta por cuenta en orden temporal** (una salida nunca deja el
  saldo en negativo y ``saldo_anterior`` de cada operación es el ``saldo_posterior`` de la anterior de la misma cuenta);
* los contadores de ventana (operaciones recientes, fallos recientes, destinatarios en 24 h…) se calculan **a posteriori** sobre la
  cronología real de cada cuenta (solo miran hacia atrás), no se sortean por separado;
* «beneficiario nuevo» significa que el destinatario no se había usado antes en esa cuenta.

Variables que el sistema actual NO recopila (``dispositivo_nuevo``, ``ubicacion_nueva``, ``viaje_imposible``) se incluyen porque
forman parte del escenario simulado, pero **no se usan como predictores** (``event_training`` las excluye). Los campos
``scenario``, ``puntuacion_riesgo_sintetica``, ``nivel_riesgo_sintetico``, ``is_fraud`` e ``is_synthetic`` tampoco.
"""
import bisect
from collections import defaultdict
from datetime import datetime, timedelta

SCHEMA_VERSION = 'combinado-1.0'
BASE = datetime(2026, 1, 5)
DAYS = 60
N_ACCOUNTS = 700
N_TRANSACTIONS = 8000
N_ACCESSES = 4000
OUT_KINDS = ('TRANSFERENCIA', 'PAGO', 'RECARGA')
ALL_KINDS = OUT_KINDS + ('DEPOSITO',)

COLUMNS = [
    'event_id', 'timestamp', 'event_type', 'account_id', 'scenario', 'hora_del_dia', 'dia_semana',
    # transacciones
    'tipo_operacion', 'importe', 'saldo_anterior', 'saldo_posterior', 'beneficiario_nuevo', 'destinatarios_distintos_24h',
    'operaciones_2m', 'operaciones_10m', 'operaciones_1h', 'n_historial', 'importe_tipico', 'fallos_login_30m',
    # accesos
    'resultado_login', 'intentos_fallidos_recientes', 'solicitudes_otp_1h', 'viaje_imposible',
    # comunes
    'contexto_nuevo', 'dispositivo_nuevo', 'ubicacion_nueva', 'recuperacion_clave_reciente', 'fallos_otp_recientes',
    # descriptivas y etiqueta
    'puntuacion_riesgo_sintetica', 'nivel_riesgo_sintetico', 'is_fraud', 'is_synthetic',
]

# Documentación viva de los escenarios (la usa la documentación y el informe). (nombre, ¿fraude?, descripción)
SCENARIOS = [
    ('L_RUTINA_TX', 0, 'Operación habitual: monto cercano al típico, destinatario habitual, sin señales.'),
    ('L_COMPRA_GRANDE', 0, 'Compra o pago legítimo grande (3-6 veces lo típico), a veces a un destinatario nuevo.'),
    ('L_BENEFICIARIO_NUEVO', 0, 'Primer pago legítimo a un destinatario nuevo con monto normal.'),
    ('L_DEPOSITO', 0, 'Ingreso (sueldo, depósito): no es una salida de dinero y no se modela.'),
    ('L_RAFAGA_LEGITIMA', 0, 'Varios pagos de servicios en pocos minutos (ráfaga legítima).'),
    ('L_NUEVO_DISPOSITIVO', 0, 'Acceso desde un dispositivo nuevo seguido de operaciones normales.'),
    ('L_VIAJERO', 0, 'Acceso desde una ubicación nueva (viaje) seguido de operaciones normales.'),
    ('L_RECUPERACION_CLAVE', 0, 'El cliente recupera su clave, falla algún OTP y opera con normalidad.'),
    ('A_L_RUTINA', 0, 'Acceso habitual y correcto.'),
    ('A_L_OLVIDADIZO', 0, 'Varios fallos de clave y acceso correcto desde el mismo entorno.'),
    ('A_L_OTP_LENTO', 0, 'Acceso correcto tras uno o dos OTP incorrectos.'),
    ('F_VACIADO', 1, 'Intento de vaciar la cuenta: 85-100 % del saldo en una sola operación.'),
    ('F_INGENIERIA_SOCIAL', 1, 'El cliente es engañado: contexto habitual, destinatario nuevo y monto 3-8 veces lo típico.'),
    ('F_SIGILOSO', 1, 'Fraude de baja señal: monto normal, solo indicios débiles de contexto.'),
    ('F_RAFAGA', 1, 'Ráfaga automatizada: 4-8 transferencias en segundos a destinatarios nuevos.'),
    ('F_TOMA_CUENTA', 1, 'Toma de cuenta: fallos de acceso, acceso con contexto nuevo y transferencias grandes después.'),
    ('A_F_FUERZA_BRUTA', 1, 'Muchos fallos de clave en poco tiempo.'),
    ('A_F_RELLENO_CREDENCIALES', 1, 'Pocos fallos por cuenta con entorno nuevo (credenciales filtradas).'),
    ('A_F_SESION_SIGILOSA', 1, 'Acceso correcto con solo indicios débiles de entorno nuevo.'),
]


def _flag(rng, p):
    return int(rng.rand() < p)


def _event(account, t, etype, scenario, fraud, **kw):
    base = {'acc': account, 't': t, 'etype': etype, 'scenario': scenario, 'fraud': fraud, 'kind': None, 'mode': None, 'margs': (),
            'ben': None, 'ctx': 0, 'dev': 0, 'loc': 0, 'rec': 0, 'trav': 0, 'otp': 0, 'outcome': None}
    base.update(kw)
    return base


class _Plan:
    """Cuentas ficticias y utilidades para muestrear instantes y flags."""

    def __init__(self, rng):
        import numpy as np
        self.rng = rng
        self.typical = np.exp(rng.normal(np.log(110), 0.55, N_ACCOUNTS)).clip(15, 900)
        self.income = self.typical * rng.uniform(8, 20, N_ACCOUNTS)
        self.balance0 = self.income * rng.uniform(0.5, 2.5, N_ACCOUNTS)
        self.center = np.clip(rng.normal(13, 2.5, N_ACCOUNTS), 7, 20)
        self.n_known = rng.randint(2, 7, N_ACCOUNTS)

    def account(self):
        return int(self.rng.randint(0, N_ACCOUNTS))

    def time(self, account, night=0.04):
        rng = self.rng
        day = int(rng.randint(0, DAYS))
        hour = int(rng.randint(0, 6)) if rng.rand() < night else int(round(rng.normal(self.center[account], 3.5))) % 24
        return BASE + timedelta(days=day, hours=hour, minutes=int(rng.randint(0, 60)), seconds=int(rng.randint(0, 60)))


def _tx_kind(rng):
    return OUT_KINDS[int(rng.choice(3, p=[0.35, 0.45, 0.20]))]


def _routine(plan, account, t, scenario='L_RUTINA_TX'):
    rng = plan.rng
    kind = _tx_kind(rng)
    return [_event(account, t, 'TRANSACCION', scenario, 0, kind=kind, mode='rutina',
                   ben=('nuevo' if kind == 'TRANSFERENCIA' and rng.rand() < 0.10 else 'habitual'),
                   ctx=_flag(rng, 0.03), dev=_flag(rng, 0.02), loc=_flag(rng, 0.02))]


def _big_legit(plan, account, t):
    rng = plan.rng
    kind = 'TRANSFERENCIA' if rng.rand() < 0.6 else 'PAGO'
    return [_event(account, t, 'TRANSACCION', 'L_COMPRA_GRANDE', 0, kind=kind, mode='multiple', margs=(3.0, 6.0, 0.7),
                   ben=('nuevo' if kind == 'TRANSFERENCIA' and rng.rand() < 0.30 else 'habitual'),
                   ctx=_flag(rng, 0.05), dev=_flag(rng, 0.03), loc=_flag(rng, 0.03))]


def _new_beneficiary_legit(plan, account, t):
    rng = plan.rng
    return [_event(account, t, 'TRANSACCION', 'L_BENEFICIARIO_NUEVO', 0, kind='TRANSFERENCIA', mode='rutina', ben='nuevo',
                   ctx=_flag(rng, 0.05), dev=_flag(rng, 0.03), loc=_flag(rng, 0.03))]


def _deposit(plan, account, t):
    return [_event(account, t, 'TRANSACCION', 'L_DEPOSITO', 0, kind='DEPOSITO', mode='deposito')]


def _legit_burst(plan, account, t):
    rng = plan.rng
    events, clock = [], t
    for _ in range(int(rng.randint(3, 6))):
        events.append(_event(account, clock, 'TRANSACCION', 'L_RAFAGA_LEGITIMA', 0, kind='PAGO', mode='rutina', ben='habitual',
                             ctx=_flag(rng, 0.02)))
        clock += timedelta(seconds=int(rng.randint(40, 160)))
    return events


def _after(plan, account, start, count, scenario, fraud, *, kind_choice=None, mode='rutina', margs=(), ben_new=0.1, window=(60, 1200), **flags):
    """``count`` operaciones posteriores a un acceso, separadas por segundos o minutos."""
    rng = plan.rng
    events, clock = [], start
    for _ in range(count):
        clock += timedelta(seconds=int(rng.randint(*window)))
        kind = kind_choice or _tx_kind(rng)
        events.append(_event(account, clock, 'TRANSACCION', scenario, fraud, kind=kind, mode=mode, margs=margs,
                             ben=('nuevo' if kind == 'TRANSFERENCIA' and rng.rand() < ben_new else 'habitual'),
                             **{k: (_flag(rng, v) if isinstance(v, float) else v) for k, v in flags.items()}))
    return events


def _legit_new_device(plan, account, t):
    rng = plan.rng
    access = _event(account, t, 'ACCESO', 'L_NUEVO_DISPOSITIVO', 0, outcome='EXITOSO', dev=1, ctx=_flag(rng, 0.7), loc=_flag(rng, 0.3),
                    otp=int(rng.randint(0, 2)))
    return [access] + _after(plan, account, t, int(rng.randint(0, 3)), 'L_NUEVO_DISPOSITIVO', 0, ben_new=0.12, ctx=0.4, dev=0.7)


def _legit_traveller(plan, account, t):
    rng = plan.rng
    access = _event(account, t, 'ACCESO', 'L_VIAJERO', 0, outcome='EXITOSO', loc=1, ctx=_flag(rng, 0.8), dev=_flag(rng, 0.2))
    return [access] + _after(plan, account, t, int(rng.randint(0, 3)), 'L_VIAJERO', 0, ben_new=0.1, ctx=0.6, loc=1)


def _legit_recovery(plan, account, t):
    rng = plan.rng
    events, clock = [], t
    for _ in range(int(rng.randint(0, 2))):
        events.append(_event(account, clock, 'ACCESO', 'L_RECUPERACION_CLAVE', 0, outcome='FALLIDO', rec=1, ctx=_flag(rng, 0.3)))
        clock += timedelta(seconds=int(rng.randint(20, 120)))
    events.append(_event(account, clock, 'ACCESO', 'L_RECUPERACION_CLAVE', 0, outcome='EXITOSO', rec=1, otp=int(rng.randint(0, 3)),
                         dev=_flag(rng, 0.4), ctx=_flag(rng, 0.4)))
    return events + _after(plan, account, clock, int(rng.randint(0, 2)), 'L_RECUPERACION_CLAVE', 0, ben_new=0.3, rec=1, ctx=0.4)


def _access_routine(plan, account, t):
    rng = plan.rng
    return [_event(account, t, 'ACCESO', 'A_L_RUTINA', 0, outcome='EXITOSO', ctx=_flag(rng, 0.03), dev=_flag(rng, 0.02), loc=_flag(rng, 0.02))]


def _forgetful(plan, account, t):
    rng = plan.rng
    events, clock = [], t
    for _ in range(int(rng.randint(1, 4))):
        events.append(_event(account, clock, 'ACCESO', 'A_L_OLVIDADIZO', 0, outcome='FALLIDO', ctx=_flag(rng, 0.03)))
        clock += timedelta(seconds=int(rng.randint(10, 90)))
    events.append(_event(account, clock, 'ACCESO', 'A_L_OLVIDADIZO', 0, outcome='EXITOSO', ctx=_flag(rng, 0.03)))
    return events


def _slow_otp(plan, account, t):
    rng = plan.rng
    return [_event(account, t, 'ACCESO', 'A_L_OTP_LENTO', 0, outcome='EXITOSO', otp=int(rng.randint(1, 3)), ctx=_flag(rng, 0.05))]


def _drain(plan, account, t):
    rng = plan.rng
    t = plan.time(account, night=0.4)
    return [_event(account, t, 'TRANSACCION', 'F_VACIADO', 1, kind='TRANSFERENCIA', mode='share', margs=(0.85, 1.0),
                   ben=('nuevo' if rng.rand() < 0.7 else 'habitual'), ctx=_flag(rng, 0.35), dev=_flag(rng, 0.25), loc=_flag(rng, 0.2))]


def _social_engineering(plan, account, t):
    rng = plan.rng
    return [_event(account, t, 'TRANSACCION', 'F_INGENIERIA_SOCIAL', 1, kind='TRANSFERENCIA', mode='multiple', margs=(3.0, 8.0, 0.9),
                   ben=('nuevo' if rng.rand() < 0.9 else 'habitual'), ctx=_flag(rng, 0.08), dev=_flag(rng, 0.05), loc=_flag(rng, 0.05))]


def _stealthy(plan, account, t):
    rng = plan.rng
    t = plan.time(account, night=0.2)
    kind = 'TRANSFERENCIA' if rng.rand() < 0.7 else 'PAGO'
    return [_event(account, t, 'TRANSACCION', 'F_SIGILOSO', 1, kind=kind, mode='rutina',
                   ben=('nuevo' if kind == 'TRANSFERENCIA' and rng.rand() < 0.5 else 'habitual'),
                   ctx=_flag(rng, 0.5), dev=_flag(rng, 0.4), loc=_flag(rng, 0.5))]


def _burst(plan, account, t):
    rng = plan.rng
    events, clock = [], t
    for _ in range(int(rng.randint(4, 9))):
        events.append(_event(account, clock, 'TRANSACCION', 'F_RAFAGA', 1, kind='TRANSFERENCIA', mode='share', margs=(0.10, 0.30),
                             ben=('nuevo' if rng.rand() < 0.8 else 'habitual'), ctx=_flag(rng, 0.3), dev=_flag(rng, 0.2)))
        clock += timedelta(seconds=int(rng.randint(8, 35)))
    return events


def _takeover(plan, account, t):
    rng = plan.rng
    t = plan.time(account, night=0.35)
    events, clock = [], t
    for _ in range(int(rng.randint(0, 4))):
        events.append(_event(account, clock, 'ACCESO', 'F_TOMA_CUENTA', 1, outcome='FALLIDO', ctx=_flag(rng, 0.85), dev=_flag(rng, 0.85),
                             otp=_flag(rng, 0.3)))
        clock += timedelta(seconds=int(rng.randint(5, 60)))
    events.append(_event(account, clock, 'ACCESO', 'F_TOMA_CUENTA', 1, outcome='EXITOSO', ctx=_flag(rng, 0.85), dev=_flag(rng, 0.85),
                         loc=_flag(rng, 0.6), rec=_flag(rng, 0.45), trav=_flag(rng, 0.3), otp=int(rng.randint(1, 4)) if rng.rand() < 0.6 else 0))
    events += _after(plan, account, clock, int(rng.randint(1, 4)), 'F_TOMA_CUENTA', 1, kind_choice='TRANSFERENCIA', mode='share',
                     margs=(0.5, 0.98), ben_new=0.85, window=(120, 1500), ctx=0.85, dev=0.85, loc=0.6, rec=0.4)
    return events


def _brute_force(plan, account, t):
    rng = plan.rng
    t = plan.time(account, night=0.3)
    events, clock = [], t
    attempts = int(rng.randint(4, 13))
    for index in range(attempts):
        last = index == attempts - 1
        events.append(_event(account, clock, 'ACCESO', 'A_F_FUERZA_BRUTA', 1, outcome=('EXITOSO' if last and rng.rand() < 0.15 else 'FALLIDO'),
                             ctx=_flag(rng, 0.7), otp=(int(rng.randint(0, 3)) if last else 0)))
        clock += timedelta(seconds=int(rng.randint(5, 40)))
    return events


def _stuffing(plan, account, t):
    rng = plan.rng
    t = plan.time(account, night=0.25)
    events, clock = [], t
    attempts = int(rng.randint(2, 7))
    for index in range(attempts):
        last = index == attempts - 1
        events.append(_event(account, clock, 'ACCESO', 'A_F_RELLENO_CREDENCIALES', 1, outcome=('EXITOSO' if last and rng.rand() < 0.2 else 'FALLIDO'),
                             ctx=_flag(rng, 0.9), dev=_flag(rng, 0.9), loc=_flag(rng, 0.7), trav=_flag(rng, 0.3)))
        clock += timedelta(seconds=int(rng.randint(20, 120)))
    return events


def _stealth_access(plan, account, t):
    rng = plan.rng
    t = plan.time(account, night=0.2)
    return [_event(account, t, 'ACCESO', 'A_F_SESION_SIGILOSA', 1, outcome='EXITOSO', ctx=_flag(rng, 0.45), dev=_flag(rng, 0.5),
                   loc=_flag(rng, 0.5), trav=_flag(rng, 0.15))]


# Cuota de EPISODIOS por escenario (la composición exacta del dataset). El resto de eventos hasta los totales pedidos son operaciones y
# accesos habituales sueltos (L_RUTINA_TX y A_L_RUTINA). Los eventos por episodio varían (p. ej. una ráfaga son 4-8 operaciones).
EPISODE_COUNTS = [
    (_big_legit, 320), (_new_beneficiary_legit, 320), (_deposit, 480), (_legit_burst, 45),
    (_legit_new_device, 80), (_legit_traveller, 60), (_legit_recovery, 100), (_forgetful, 150), (_slow_otp, 60),
    (_drain, 130), (_social_engineering, 130), (_stealthy, 90), (_burst, 28),
    (_takeover, 65), (_brute_force, 15), (_stuffing, 17), (_stealth_access, 45),
]


def _collect(plan):
    """Episodios según la cuota y relleno con eventos habituales sueltos hasta los totales exactos."""
    events, counts = [], {'TRANSACCION': 0, 'ACCESO': 0}
    plan_order = [builder for builder, quota in EPISODE_COUNTS for _ in range(quota)]
    for builder in plan_order:
        account = plan.account()
        made = builder(plan, account, plan.time(account))
        events += made
        for event in made:
            counts[event['etype']] += 1
    if counts['TRANSACCION'] > N_TRANSACTIONS or counts['ACCESO'] > N_ACCESSES:
        raise ValueError('Las cuotas de escenarios superan los totales pedidos.')
    for etype, target, builder in (('TRANSACCION', N_TRANSACTIONS, _routine), ('ACCESO', N_ACCESSES, _access_routine)):
        while counts[etype] < target:
            account = plan.account()
            events += builder(plan, account, plan.time(account))
            counts[etype] += 1
    return events


def _simulate_ledger(plan, events):
    """Importes y saldos cuenta por cuenta en orden cronológico. Nunca deja un saldo negativo."""
    import numpy as np
    rng = plan.rng
    by_account = defaultdict(list)
    for event in events:
        by_account[event['acc']].append(event)
    new_beneficiary = [10_000_000]
    for account, items in by_account.items():
        items.sort(key=lambda e: (e['t'], e['seq']))
        balance = float(plan.balance0[account])
        typical, income = float(plan.typical[account]), float(plan.income[account])
        known = [account * 1000 + k for k in range(int(plan.n_known[account]))]
        used = set()
        for event in items:
            if event['etype'] != 'TRANSACCION':
                continue
            mode, margs = event['mode'], event['margs']
            if event['kind'] != 'DEPOSITO':
                if mode == 'rutina':
                    amount = typical * float(np.exp(rng.normal(0, 0.35)))
                    cap = 0.35
                elif mode == 'multiple':
                    amount, cap = typical * float(rng.uniform(margs[0], margs[1])), margs[2]
                else:                                           # 'share'
                    amount, cap = float(rng.uniform(margs[0], margs[1])) * balance, 1.0
                amount = min(amount, cap * balance)
                if balance < 0.5 or amount < 0.01:               # sin fondos: la operación pasa a ser un ingreso (nunca un saldo negativo)
                    event['kind'], event['mode'] = 'DEPOSITO', 'deposito'
            if event['kind'] == 'DEPOSITO':
                amount = income * float(rng.uniform(0.9, 1.1))
                event['ben'] = None
            amount = round(max(amount, 0.01), 2)
            if event['kind'] != 'DEPOSITO':
                amount = min(amount, round(balance, 2))
            event['amount'] = amount
            event['balance_before'] = round(balance, 2)
            balance = round(balance + amount if event['kind'] == 'DEPOSITO' else balance - amount, 2)
            event['balance_after'] = balance
            if event['kind'] == 'TRANSFERENCIA':
                if event['ben'] == 'nuevo':
                    new_beneficiary[0] += 1
                    event['ben_id'] = new_beneficiary[0]
                else:
                    event['ben_id'] = known[int(rng.randint(0, len(known)))]
                event['ben_new'] = int(event['ben_id'] not in used and event['ben_id'] not in known)
                used.add(event['ben_id'])
            else:
                event['ben_id'], event['ben_new'] = None, None
    return by_account


def _window_features(by_account):
    """Contadores de ventana calculados sobre la cronología real de cada cuenta (solo hacia atrás)."""
    for items in by_account.values():
        debit_times, transfer_log, failed_times, otp_log, access_times = [], [], [], [], []
        history = defaultdict(list)
        for event in items:
            t = event['t']
            if event['etype'] == 'TRANSACCION':
                if event['kind'] in OUT_KINDS:
                    event['ops_2m'] = bisect.bisect_left(debit_times, t) - bisect.bisect_left(debit_times, t - timedelta(minutes=2))
                    event['ops_10m'] = bisect.bisect_left(debit_times, t) - bisect.bisect_left(debit_times, t - timedelta(minutes=10))
                    event['ops_1h'] = bisect.bisect_left(debit_times, t) - bisect.bisect_left(debit_times, t - timedelta(hours=1))
                    prior = [a for when, a in history[event['kind']] if when >= t - timedelta(days=90)][-100:]
                    event['n_hist'] = len(prior)
                    event['typical_prior'] = round(float(sorted(prior)[len(prior) // 2]), 2) if prior else None
                    debit_times.append(t)
                    history[event['kind']].append((t, event['amount']))
                else:
                    event['ops_2m'] = event['ops_10m'] = event['ops_1h'] = event['n_hist'] = 0
                    event['typical_prior'] = None
                if event['kind'] == 'TRANSFERENCIA':
                    day = {b for when, b in transfer_log if when >= t - timedelta(hours=24)} | {event['ben_id']}
                    event['distinct_24h'] = len(day)
                    transfer_log.append((t, event['ben_id']))
                else:
                    event['distinct_24h'] = 0
                event['login_fails_30m'] = sum(1 for when in failed_times if t - timedelta(minutes=30) <= when < t)
            else:
                event['prior_failures'] = sum(1 for when in failed_times if t - timedelta(minutes=30) <= when < t)
                event['otp_requests_1h'] = sum(1 for when in access_times if t - timedelta(hours=1) <= when < t)
                if event['outcome'] == 'FALLIDO':
                    failed_times.append(t)
                else:
                    access_times.append(t)                     # solo los accesos que llegan a la etapa del OTP (contraseña correcta)
            if event['otp']:
                otp_log.append((t, event['otp']))
            event['otp_window'] = sum(n for when, n in otp_log if t - timedelta(minutes=30) <= when <= t)


def _describe(event, rng):
    """Puntuación y nivel DESCRIPTIVOS calculados solo con variables observables y ruido (nunca con la etiqueta ni el escenario)."""
    if event['etype'] == 'TRANSACCION':
        if event['kind'] == 'DEPOSITO':
            score = 3.0
        else:
            share = min(event['amount'] / event['balance_before'], 1.0) if event['balance_before'] > 0 else 0.0
            ratio = (event['amount'] / event['typical_prior']) if event['typical_prior'] else 1.0
            score = (30 * share ** 2 + 10 * (event['ben_new'] or 0) + 12 * event['ctx'] + 10 * event['rec'] + 8 * min(event['otp_window'], 3)
                     + 6 * min(event['login_fails_30m'], 3) + 4 * min(event['ops_10m'], 5) + 3 * min(max(event['distinct_24h'] - 1, 0), 5)
                     + 8 * (ratio >= 3))
    else:
        score = (12 * min(event['prior_failures'] + (event['outcome'] == 'FALLIDO'), 6) + 10 * min(event['otp_window'], 3) + 15 * event['ctx']
                 + 8 * event['rec'] + 6 * min(event['otp_requests_1h'], 3))
    score = float(min(max(score + rng.normal(0, 5), 0), 100))
    return round(score, 1), ('SOSPECHOSO' if score >= 55 else 'INTERMEDIO' if score >= 25 else 'NORMAL')


def generate_combined(seed=2026):
    """DataFrame con exactamente ``N_TRANSACTIONS`` transacciones y ``N_ACCESSES`` accesos. Determinista para una misma semilla."""
    import numpy as np
    import pandas as pd
    rng = np.random.RandomState(seed)
    plan = _Plan(rng)
    events = _collect(plan)
    for seq, event in enumerate(events):
        event['seq'] = seq
    events.sort(key=lambda e: (e['t'], e['seq']))
    by_account = _simulate_ledger(plan, events)
    _window_features(by_account)

    rows = []
    for number, event in enumerate(events, start=1):
        t = event['t']
        tx = event['etype'] == 'TRANSACCION'
        score, level = _describe(event, rng)
        rows.append({
            'event_id': f'EVT-{number:06d}', 'timestamp': t.strftime('%Y-%m-%dT%H:%M:%S'), 'event_type': event['etype'],
            'account_id': f'ACC-{event["acc"]:04d}', 'scenario': event['scenario'], 'hora_del_dia': t.hour, 'dia_semana': t.weekday(),
            'tipo_operacion': event['kind'] if tx else '', 'importe': event['amount'] if tx else '',
            'saldo_anterior': event['balance_before'] if tx else '', 'saldo_posterior': event['balance_after'] if tx else '',
            'beneficiario_nuevo': (event['ben_new'] if event['kind'] == 'TRANSFERENCIA' else 0) if tx else '',
            'destinatarios_distintos_24h': event['distinct_24h'] if tx else '', 'operaciones_2m': event['ops_2m'] if tx else '',
            'operaciones_10m': event['ops_10m'] if tx else '', 'operaciones_1h': event['ops_1h'] if tx else '',
            'n_historial': event['n_hist'] if tx else '', 'importe_tipico': (event['typical_prior'] if event['typical_prior'] is not None else '') if tx else '',
            'fallos_login_30m': event['login_fails_30m'] if tx else '',
            'resultado_login': '' if tx else event['outcome'], 'intentos_fallidos_recientes': '' if tx else event['prior_failures'],
            'solicitudes_otp_1h': '' if tx else event['otp_requests_1h'], 'viaje_imposible': '' if tx else event['trav'],
            'contexto_nuevo': event['ctx'], 'dispositivo_nuevo': event['dev'], 'ubicacion_nueva': event['loc'],
            'recuperacion_clave_reciente': event['rec'], 'fallos_otp_recientes': event['otp_window'],
            'puntuacion_riesgo_sintetica': score, 'nivel_riesgo_sintetico': level, 'is_fraud': event['fraud'], 'is_synthetic': 1,
        })
    return pd.DataFrame(rows, columns=COLUMNS)
