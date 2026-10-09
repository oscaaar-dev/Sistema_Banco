import csv
from decimal import Decimal
from pathlib import Path

import pytest
from app.ai.operation_risk import assess_operation_risk

SCENARIOS_PATH = Path(__file__).parent / 'fixtures' / 'operation_risk_scenarios.csv'


def load_scenarios():
    with SCENARIOS_PATH.open(encoding='utf-8', newline='') as scenarios_file:
        return list(csv.DictReader(scenarios_file))


@pytest.mark.parametrize('scenario', load_scenarios(), ids=lambda row: row['name'])
def test_synthetic_scenarios_match_expected_risk_levels(scenario):
    typical_amount = Decimal(scenario['typical_amount'])
    comparable_amounts = [
        typical_amount
        for _ in range(int(scenario['comparable_count']))
    ]
    assessment = assess_operation_risk(
        Decimal(scenario['amount']),
        Decimal(scenario['balance']),
        comparable_amounts,
        int(scenario['recent_operation_count']),
        int(scenario['duration_seconds']),
    )

    assert assessment['score'] == int(scenario['expected_score'])
    assert assessment['level'] == scenario['expected_level']
    assert assessment['enough_history'] is (
        scenario['enough_history'].lower() == 'true'
    )

    if not assessment['enough_history']:
        assert any('Historial limitado' in reason for reason in assessment['reasons'])
