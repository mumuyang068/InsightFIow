import unittest
from datetime import date

from apps.chat.task.cbi_contract import (
    Action,
    ActionRejected,
    Definition,
    Intent,
    Topic,
    accept_confirmation,
    explicitly_chosen,
    resolve_request,
)
from apps.chat.task.cbi_outputs import comparison, outputs, presentation


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.topic = Topic(
            version="test-run",
            timezone="Asia/Shanghai",
            definitions=[
                Definition(
                    id="event:v1",
                    label="经营流水",
                    description="事件日",
                    aliases=["经营流水"],
                    view="desk.event",
                ),
                Definition(
                    id="cohort:v1",
                    label="固定期",
                    description="支付日",
                    aliases=["固定期"],
                    view="desk.cohort",
                ),
            ],
        )
        self.issue = Intent(
            store="X",
            current=date(2028, 2, 3),
            baseline=date(2028, 2, 2),
            requested=["current", "baseline", "delta"],
        )

    def test_ambiguity_does_not_plan_a_business_query(self):
        result = resolve_request("本期与上期差多少？", self.issue, self.topic)
        self.assertEqual(result["task_status"], "awaiting_confirmation")
        self.assertEqual(result["steps"], [])
        self.assertEqual(
            {item["id"] for item in result["decision"]["options"]},
            {"event:v1", "cohort:v1"},
        )

    def test_confirmation_keeps_scope_and_is_idempotent(self):
        state = resolve_request("原问题", self.issue, self.topic)
        action = Action(
            action="confirm_definition",
            request_id="first-click",
            expected_revision=1,
            decision_id=state["decision"]["id"],
            choice_id="event:v1",
        )
        active, changed = accept_confirmation(state, action, "button")
        self.assertTrue(changed)
        self.assertEqual(active["scope"]["store"], "X")
        self.assertEqual(active["scope"]["current"], "2028-02-03")
        self.assertEqual(active["request"]["text"], "原问题")
        self.assertEqual(accept_confirmation(active, action, "button"), (active, False))

    def test_stale_or_unlisted_choice_is_rejected(self):
        state = resolve_request("原问题", self.issue, self.topic)
        for rev, choice in ((0, "event:v1"), (1, "other:v1")):
            with self.assertRaises(ActionRejected):
                accept_confirmation(
                    state,
                    Action(
                        action="confirm_definition",
                        request_id="new-click",
                        expected_revision=rev,
                        decision_id=state["decision"]["id"],
                        choice_id=choice,
                    ),
                    "button",
                )

    def test_date_followup_inherits_only_scope_and_preserves_previous_result(self):
        selected = self.issue.model_copy(update={"definition": "event:v1"})
        prior = resolve_request("第一次", selected, self.topic)
        follow = Intent(
            operation="revise",
            current=date(2028, 2, 4),
            requested=["current", "target"],
        )
        result = resolve_request("看次日目标", follow, self.topic, prior, 312)
        self.assertEqual(result["scope"]["store"], "X")
        self.assertEqual(result["scope"]["definition"], "event:v1")
        self.assertIsNone(result["scope"]["baseline"])
        self.assertEqual(result["scope"]["current"], "2028-02-04")
        self.assertEqual(result["request"]["parent_record_id"], "312")
        self.assertEqual(prior["scope"]["current"], "2028-02-03")

    def test_requested_definition_is_distinguished_from_reference(self):
        self.assertEqual(
            explicitly_chosen("改按固定期再算，核查与刚才经营流水的差异", self.topic),
            "cohort:v1",
        )
        self.assertIsNone(explicitly_chosen("经营流水与固定期还有哪些差异", self.topic))

    def test_date_only_followup_keeps_the_parent_outputs_and_comparison(self):
        topic = self.topic.model_copy(update={"default_definition": "event:v1"})
        prior = resolve_request("原问题", self.issue, topic)
        follow = Intent(operation="revise", current=date(2028, 2, 4))
        state = resolve_request("改看下一天", follow, topic, prior, 312)
        self.assertEqual(state["task_status"], "running")
        self.assertEqual(state["request"]["outputs"], ["current", "baseline", "delta"])
        self.assertEqual(state["request"]["parent_record_id"], "312")
        self.assertEqual(state["scope"]["baseline"], "2028-02-02")

    def test_explicit_fields_win_and_missing_parent_does_not_inherit(self):
        topic = self.topic.model_copy(update={"default_definition": "event:v1"})
        prior = resolve_request("原问题", self.issue, topic)
        follow = Intent(operation="revise", store="Y", current=date(2028, 2, 4),
                        definition="cohort:v1", requested=["target"])
        state = resolve_request("换店并按固定期查目标", follow, topic, prior, 312)
        self.assertEqual(state["scope"]["store"], "Y")
        self.assertEqual(state["scope"]["definition"], "cohort:v1")
        self.assertEqual(state["request"]["outputs"], ["target"])
        unavailable = resolve_request("改看下一天", Intent(operation="revise", current=date(2028, 2, 4)), topic)
        self.assertEqual(unavailable["decision"]["reason"], "scope_required")
        self.assertEqual(unavailable["steps"], [])

    def test_missing_is_distinct_from_zero_and_negative_baseline(self):
        self.assertEqual(comparison(0, -3), (3, None))
        self.assertEqual(comparison(12, 0), (12, None))
        self.assertEqual(comparison(None, 3), (None, None))

    def test_receipt_scope_and_definition_must_match(self):
        topic = self.topic.model_copy(update={"default_definition": "event:v1"})
        state = resolve_request(
            "金额", self.issue.model_copy(update={"requested": ["current"]}), topic
        )
        common = {"record_id": "31", "store": "X", "date": "2028-02-03", "amount": 99}
        wrong = [
            dict(common, definition="cohort:v1"),
            dict(common, definition="event:v1", date="2028-02-02"),
        ]
        self.assertEqual(outputs(state, wrong)[0]["status"], "missing")
        correct = [dict(common, definition="event:v1", amount=0)]
        self.assertEqual(outputs(state, correct)[0]["value"], 0)

    def test_chart_series_never_add_alternatives_or_units(self):
        self.assertEqual(
            presentation("alternative_definitions", ["CNY"])["series_mode"], "grouped"
        )
        self.assertEqual(
            presentation("additive_partition", ["CNY"])["series_mode"], "stacked"
        )
        self.assertEqual(
            presentation("additive_partition", ["CNY", "%"])["series_mode"], "none"
        )


if __name__ == "__main__":
    unittest.main()
