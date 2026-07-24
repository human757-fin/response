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
        for name in (
            "DATABASE_URL",
            "DATABASE_ENGINE",
            "DB_HOST",
            "DB_PORT",
            "DB_NAME",
            "DB_USER",
            "DB_PASSWORD",
        ):
            os.environ.pop(name, None)
        os.environ["DATABASE_PATH"] = str(Path(cls.tempdir.name) / "test.db")
        import response_core

        cls.store = importlib.reload(response_core)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def setUp(self) -> None:
        with self.store.connect() as db:
            for table in (
                "sound_effects",
                "moderation_cases",
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
        self.assertTrue(config["logs"]["voice_events"])
        self.assertTrue(config["logs"]["audit_log_events"])
        self.assertTrue(config["logs"]["interaction_events"])
        self.assertTrue(config["logs"]["message_create"])
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

    def test_moderation_cases_can_be_filtered_and_warnings_cleared(self) -> None:
        first = self.store.add_moderation_case(1, 20, 99, "warn", "First warning")
        self.store.add_moderation_case(1, 20, 99, "timeout", "Cooling off", 123456)
        self.store.add_moderation_case(1, 21, 99, "warn", "Other member")

        cases = self.store.moderation_cases(1, user_id=20)
        self.assertEqual({case["action"] for case in cases}, {"warn", "timeout"})
        self.assertEqual(first, min(case["id"] for case in cases))
        self.assertEqual(self.store.clear_warnings(1, 20), 1)
        self.assertEqual(
            [case["action"] for case in self.store.moderation_cases(1, user_id=20)],
            ["timeout"],
        )

    def test_sound_effects_can_be_saved_replaced_and_deleted(self) -> None:
        self.store.save_sound_effect(1, "AirHorn", "url", "https://old.test/a.mp3", 20, 0.8)
        self.store.save_sound_effect(1, "airhorn", "url", "https://new.test/a.mp3", 21, 1.2)

        sounds = self.store.list_sound_effects(1)
        self.assertEqual(len(sounds), 1)
        self.assertEqual(sounds[0]["source"], "https://new.test/a.mp3")
        self.assertEqual(sounds[0]["volume"], 1.2)
        deleted = self.store.delete_sound_effect(1, sounds[0]["id"])
        self.assertEqual(deleted["source_type"], "url")
        self.assertEqual(self.store.list_sound_effects(1), [])

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

    def test_mysql_adapter_translates_placeholders(self) -> None:
        calls = []

        class FakeCursor:
            def execute(self, query, parameters):
                calls.append((query, parameters))

        class FakeConnection:
            def cursor(self):
                return FakeCursor()

        database = self.store.Database(FakeConnection(), mysql=True)
        database.execute("SELECT * FROM members WHERE guild_id=? AND user_id=?", (1, 2))

        self.assertEqual(
            calls,
            [("SELECT * FROM members WHERE guild_id=%s AND user_id=%s", (1, 2))],
        )

    def test_mysql_endpoint_and_schema_configuration(self) -> None:
        old_url = self.store.DATABASE_URL
        try:
            self.store.DATABASE_URL = ""
            os.environ.update(
                {
                    "DB_HOST": "database.internal:3307",
                    "DB_NAME": "s12_response",
                    "DB_USER": "u12_response",
                    "DB_PASSWORD": "secret",
                }
            )
            os.environ.pop("DB_PORT", None)
            settings = self.store._mysql_settings()
        finally:
            self.store.DATABASE_URL = old_url
            for name in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
                os.environ.pop(name, None)

        self.assertEqual(settings["host"], "database.internal")
        self.assertEqual(settings["port"], 3307)
        self.assertEqual(settings["database"], "s12_response")
        schema = "\n".join(self.store.MYSQL_SCHEMA)
        self.assertIn("AUTO_INCREMENT", schema)
        self.assertIn("ENGINE=InnoDB", schema)
        self.assertNotIn("AUTOINCREMENT", schema)


if __name__ == "__main__":
    unittest.main()
