from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = str(Path(cls.tempdir.name) / "test.db")
        import response_core

        cls.store = importlib.reload(response_core)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        with self.store.connect() as db:
            for table in (
                "giveaway_entries",
                "giveaways",
                "scheduled_messages",
                "audit_events",
                "reaction_roles",
                "members",
                "guilds",
            ):
                db.execute(f"DELETE FROM {table}")

    def test_default_config_is_populated_and_preserved(self) -> None:
        config = self.store.ensure_guild(1, "Test server")
        self.assertTrue(config["leveling"]["enabled"])
        config["economy"]["currency_name"] = "stars"
        config["custom_section"] = {"value": 7}

        saved = self.store.save_config(1, config)
        loaded = self.store.get_config(1)

        self.assertEqual(saved, loaded)
        self.assertEqual(loaded["economy"]["currency_name"], "stars")
        self.assertEqual(loaded["custom_section"]["value"], 7)

    def test_activity_cooldown_and_level_calculation(self) -> None:
        first = self.store.add_activity(
            1, 20, "Member", xp=100, money=5, activity="message", cooldown=60
        )
        second = self.store.add_activity(
            1, 20, "Member", xp=100, money=5, activity="message", cooldown=60
        )

        self.assertEqual(first["level"], 1)
        self.assertEqual(second["awarded_xp"], 0)
        member = self.store.get_member(1, 20)
        self.assertEqual(member["xp"], 100)
        self.assertEqual(member["balance"], 5)
        first_award_time = member["last_message_xp"]
        self.store.add_activity(
            1, 20, "Member", xp=100, money=5, activity="message", cooldown=60
        )
        member = self.store.get_member(1, 20)
        self.assertEqual(member["last_message_xp"], first_award_time)

    def test_reward_claim_enforces_cooldown(self) -> None:
        claimed, amount, remaining = self.store.claim_reward(1, 20, "Member", "daily", 250, 86400)
        again, second_amount, second_remaining = self.store.claim_reward(
            1, 20, "Member", "daily", 250, 86400
        )

        self.assertTrue(claimed)
        self.assertEqual(amount, 250)
        self.assertEqual(remaining, 0)
        self.assertFalse(again)
        self.assertEqual(second_amount, 0)
        self.assertGreater(second_remaining, 0)

    def test_dashboard_and_leaderboard(self) -> None:
        self.store.ensure_guild(1, "Test server")
        self.store.add_activity(1, 10, "Alpha", xp=400, money=20)
        self.store.add_activity(1, 11, "Beta", xp=100, money=10)
        self.store.add_audit(1, "test_event", "Test detail")

        dashboard = self.store.dashboard_data(1)

        self.assertEqual(dashboard["tracked_members"], 2)
        self.assertEqual(dashboard["total_xp"], 500)
        self.assertEqual(dashboard["leaderboard"][0]["username"], "Alpha")
        self.assertEqual(dashboard["events"][0]["event_type"], "test_event")

    def test_giveaway_schema_keeps_weighted_entries(self) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO giveaways(message_id,guild_id,channel_id,prize,winner_count,ends_at,created_by) "
                "VALUES (1,2,3,'Prize',1,9999999999,4)"
            )
            db.execute(
                "INSERT INTO giveaway_entries(message_id,user_id,username,entries) VALUES (1,5,'User',4)"
            )
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM giveaway_entries WHERE message_id=1").fetchone()
        self.assertEqual(json.loads(json.dumps(dict(row)))["entries"], 4)


if __name__ == "__main__":
    unittest.main()
