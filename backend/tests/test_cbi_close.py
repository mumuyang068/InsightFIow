import unittest
from datetime import UTC, date, datetime

from apps.chat.task.cbi_contract import (
    Action,
    ActionRejected,
    Definition,
    Intent,
    ScopeChange,
    Topic,
    accept_publication_action,
    accept_recalculation,
    accept_scope_change,
    evidence_can_reuse,
    fork_run,
    offer_publication_confirmation,
    resolve_request,
)


class CloseContractTests(unittest.TestCase):
    def setUp(self):
        topic = Topic(
            version="definition-1",
            timezone="Asia/Shanghai",
            default_definition="flow",
            definitions=[
                Definition(
                    id="flow", label="Flow", description="Daily", view="test.flow"
                )
            ],
        )
        self.topic = topic
        self.state = resolve_request(
            "original",
            Intent(store="X", current=date(2026, 11, 11), requested=["current"]),
            topic,
        )
        self.state.update(
            task_status="completed", data_version="r1", steps=[{"record_id": "24"}]
        )

    def test_reuse_requires_a_known_matching_publication(self):
        self.assertTrue(evidence_can_reuse(self.state, "r1"))
        self.assertFalse(evidence_can_reuse(self.state, "r2"))
        self.assertFalse(evidence_can_reuse({}, None))

    def test_latest_is_idempotent_and_preserves_source(self):
        action = Action(
            action="recalculate_latest", request_id="request-1", expected_revision=1
        )
        parent, changed = accept_recalculation(self.state, action)
        self.assertTrue(changed)
        child = fork_run(parent, 24)
        self.assertEqual(child["request"]["parent_record_id"], "24")
        self.assertEqual(child["scope"], self.state["scope"])
        self.assertEqual(child["steps"], [])
        self.assertEqual(child["request"]["text"], "original")
        self.assertNotIn("data_version", child)
        self.assertEqual(parent["task_status"], "completed")
        self.assertEqual(accept_recalculation(parent, action), (parent, False))
        with self.assertRaises(ActionRejected):
            accept_recalculation(
                parent, action.model_copy(update={"expected_revision": 444})
            )

    def test_previous_day_moves_and_fixed_day_is_preserved(self):
        for rule, expected in (("previous_day", "2026-11-11"), ("fixed", "2026-10-31")):
            prior = resolve_request(
                "compare",
                Intent(
                    store="X",
                    current=date(2026, 11, 11),
                    baseline=date(2026, 10, 31),
                    comparison_rule=rule,
                    requested=["current", "baseline"],
                ),
                self.topic,
            )
            revised = resolve_request(
                "revise",
                Intent(operation="revise", current=date(2026, 11, 12)),
                self.topic,
                prior,
            )
            self.assertEqual(revised["scope"]["baseline"], expected)
            self.assertEqual(revised["scope"]["comparison_rule"], rule)

    def test_relative_question_uses_server_receipt_in_topic_timezone(self):
        received = datetime(2026, 11, 10, 15, 59, tzinfo=UTC)
        state = resolve_request(
            "yesterday",
            Intent(store="X", current_day_offset=-1, requested=["current"]),
            self.topic,
            received_at=received,
        )
        self.assertEqual(state["scope"]["current"], "2026-11-09")
        self.assertEqual(state["request"]["received_at"], received.isoformat())
        self.assertEqual(
            fork_run(state, 24)["request"]["received_at"], received.isoformat()
        )

    def test_legacy_period_change_recovers_by_explicit_comparison(self):
        prior = resolve_request(
            "compare",
            Intent(
                store="X",
                current=date(2026, 11, 1),
                baseline=date(2026, 10, 31),
                requested=["current", "baseline"],
            ),
            self.topic,
        )
        del prior["scope"]["comparison_rule"]
        blocked = resolve_request(
            "new date",
            Intent(operation="revise", current=date(2026, 11, 2)),
            self.topic,
            prior,
        )
        self.assertEqual(blocked["decision"]["reason"], "comparison_required")
        self.assertEqual(prior["scope"]["baseline"], "2026-10-31")

    def test_publication_confirmation_preserves_old_scope_and_checks_token(self):
        pending = offer_publication_confirmation(self.state, "r2")
        self.assertEqual(pending["scope"], self.state["scope"])
        self.assertEqual(pending["data_version"], "r1")
        confirm = Action(
            action="confirm_publication",
            request_id="confirm",
            expected_revision=pending["revision"],
            decision_id=pending["publication_confirmation"]["id"],
        )
        running, changed = accept_publication_action(pending, confirm)
        self.assertTrue(changed)
        self.assertEqual(running["task_status"], "running")
        self.assertEqual(accept_publication_action(running, confirm), (running, False))
        replaced = offer_publication_confirmation(running, "r3")
        with self.assertRaises(ActionRejected):
            accept_publication_action(
                replaced,
                confirm.model_copy(
                    update={
                        "request_id": "retry",
                        "expected_revision": replaced["revision"],
                    }
                ),
            )
        cancel = confirm.model_copy(
            update={"action": "cancel_publication", "request_id": "cancel"}
        )
        cancelled, changed = accept_publication_action(pending, cancel)
        self.assertTrue(changed)
        self.assertNotIn("publication_confirmation", cancelled)
        self.assertEqual(cancelled["task_status"], "partial")
        self.assertEqual(cancelled["data_version"], "r1")

    def test_category_requires_a_registered_source_and_trend_requires_a_valid_range(
        self,
    ):
        trend = Intent(
            store="X",
            current=date(2026, 11, 2),
            range_start=date(2026, 10, 30),
            shape="trend",
        )
        state = resolve_request("series", trend, self.topic)
        self.assertEqual(state["request"]["shape"], "trend")
        self.assertEqual(state["scope"]["range_start"], "2026-10-30")
        self.assertEqual(state["task_status"], "running")
        too_long = trend.model_copy(update={"range_start": date(2026, 1, 1)})
        self.assertEqual(
            resolve_request("series", too_long, self.topic)["decision"]["reason"],
            "series_scope",
        )
        group = Intent(store="X", current=date(2026, 11, 2), shape="category")
        self.assertEqual(
            resolve_request("categories", group, self.topic)["task_status"], "blocked"
        )
        approved = self.topic.model_copy(update={"category_enabled": True})
        self.assertEqual(
            resolve_request("categories", group, approved)["task_status"], "running"
        )

    def test_scope_revision_payload_is_serializable_and_replay_checks_dates(self):
        change = ScopeChange(current=date(2026, 11, 12), comparison_rule="previous_day")
        action = Action(
            action="revise_scope",
            request_id="dates",
            expected_revision=1,
            scope_change=change,
        )
        parent, accepted = accept_scope_change(self.state, action)
        self.assertTrue(accepted)
        self.assertEqual(
            parent["events"][0]["payload"]["scope_change"]["current"], "2026-11-12"
        )
        self.assertEqual(parent["scope"], self.state["scope"])
        self.assertEqual(accept_scope_change(parent, action), (parent, False))
        changed = action.model_copy(
            update={"scope_change": ScopeChange(current=date(2026, 11, 13))}
        )
        with self.assertRaisesRegex(ActionRejected, "request_id_conflict"):
            accept_scope_change(parent, changed)


if __name__ == "__main__":
    unittest.main()
