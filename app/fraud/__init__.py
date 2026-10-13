"""Sistema de detección, prevención y sanción del fraude. Ver ``docs/fraude.md``."""
from .constants import *  # noqa: F401,F403  (vocabulario compartido)
from .engine import Decision, HELD_MESSAGE, evaluate_access, evaluate_operation, finalize_operation  # noqa: F401
from .service import (FraudAdminError, FraudPermissionError, OperationHeld, OperationRestricted, access_restriction,  # noqa: F401
                      active_restriction, guard_operations)
