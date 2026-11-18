from datetime import date

import pytest

from apps.chat.task.cbi_semantic import (
    FactReference, PlanRejected, SemanticIntent, requested_missing_fact, resolve_comparison_direction,
)


@pytest.mark.parametrize(
    ('facts', 'question', 'expected'),
    [
        (['refund'], '1997 年 8 月订单退款率是多少？', 'refund'),
        (['refund'], '不查退款率，只看折后金额', None),
        (['refund'], '退款率不用查，只看 1997 年 8 月折后金额。', None),
        (['refund'], '1997 年 8 月折后金额是多少？退款率先不查。', None),
        (['refund'], '退款率不用查但还要算退款金额。', 'refund'),
        (['refund'], '不查折后金额，只看退款率', 'refund'),
        (['payment'], '退款率如何？', None),
        (['payment'], '查净支付金额', 'payment'),
        (['payment'], '查看支付转化率', 'payment'),
    ],
)
def test_detect_missing_fact_from_current_request(facts, question, expected):
    assert requested_missing_fact(facts, question) == expected


def test_explicit_month_comparison_preserves_requested_order():
    intent = SemanticIntent(operation='new', start='1997-07-01', end='1997-08-01',
                            compare_start='1997-08-01', compare_end='1997-09-01')
    ordered, basis = resolve_comparison_direction(
        '比较 1997 年 8 月与 7 月的订单行折后金额，给出两期金额。', intent, {'订单行折后金额'}
    )
    assert ordered.start == date(1997, 8, 1)
    assert ordered.compare_start == date(1997, 7, 1)
    assert ordered.end == date(1997, 9, 1)
    assert ordered.compare_end == date(1997, 8, 1)
    assert basis == 'unqualified_month_pair'
    assert resolve_comparison_direction('以 8 月为基期，对比 7 月。', intent) == (intent, 'explicit_month_roles')


@pytest.mark.parametrize('suffix', [
    '以 8 月为基准，计算 7 月相对它的变化。',
    '请给出 7 月相对 8 月的增幅。',
    '以 8 月为基期。',
    '具体只比较 8 月 1 日至 10 日与 7 月同期。',
])
def test_preserve_direction_when_request_has_additional_conditions(suffix):
    supplied = SemanticIntent(operation='new', start='1997-07-01', end='1997-08-01',
                              compare_start='1997-08-01', compare_end='1997-09-01')
    request = '比较 1997 年 8 月与 7 月，' + suffix
    assert resolve_comparison_direction(request, supplied)[0] == supplied


def test_normalize_an_inverted_model_plan_to_the_requested_relative_direction():
    supplied = SemanticIntent(operation='new', start='1997-08-01', end='1997-09-01',
                              compare_start='1997-07-01', compare_end='1997-08-01')
    corrected, basis = resolve_comparison_direction(
        '比较 1997 年 8 月与 7 月，请给出 7 月相对 8 月的增幅。', supplied
    )
    assert corrected.start == date(1997, 7, 1)
    assert corrected.compare_start == date(1997, 8, 1)
    assert basis == 'explicit_month_roles'
    with pytest.raises(PlanRejected, match='semantic_scope'):
        resolve_comparison_direction('比较 1997 年 8 月与 7 月，以 8 月为基准，以 7 月为基期。', supplied)


def test_month_guard_respects_cross_year_partial_windows_and_revision():
    whole = SemanticIntent(operation='new', start='1998-01-01', end='1998-02-01',
                           compare_start='1997-12-01', compare_end='1998-01-01')
    assert resolve_comparison_direction('比较 1997 年 12 月与 1998 年 1 月。', whole)[0].start == date(1997, 12, 1)
    partial = whole.model_copy(update={'end': date(1998, 1, 11), 'compare_end': date(1997, 12, 11)})
    assert resolve_comparison_direction('比较 1997 年 12 月与 1998 年 1 月的前十天。', partial) == (partial, 'model')
    revision = whole.model_copy(update={'operation': 'revise'})
    assert resolve_comparison_direction('本期改为 9 月，仍和 7 月比较。', revision) == (revision, 'model')


def test_missing_fact_context_quote_must_apply_to_the_current_occurrence():
    question = '上次有人问“退款率是多少”，这次只看折后金额。'
    assert requested_missing_fact(['refund'], question, [
        FactReference(fact='refund', role='context', quote='上次有人问“退款率是多少”'),
    ]) is None
    with pytest.raises(PlanRejected, match='semantic_metric_clarify'):
        requested_missing_fact(['refund'], question, [
            FactReference(fact='refund', role='context', quote='退款率'),
        ])
    assert requested_missing_fact(['refund'], '退款率不用查，但退款金额是多少。', [
        FactReference(fact='refund', role='context', quote='退款金额是多少'),
    ]) == 'refund'
    with pytest.raises(PlanRejected, match='semantic_metric_clarify'):
        requested_missing_fact(['refund'], '看看它怎么影响金额。', requires_refund_evidence=True)
