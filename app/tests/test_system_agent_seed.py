import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.shared.crm.system_agents_seed import ensure_system_agents_seeded, restore_system_agent_default


class SystemAgentSeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "crm.sqlite"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _count_system_agents(self) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                """
                SELECT COUNT(*)
                FROM agentpreset
                WHERE apid IN (
                    'agent-1bad27aabaac439da678f31d53855b5d',
                    'agent-5a43bda9e1304108a1a78a3575a44e27',
                    'agent-c9b80fdfad234392b55d84de93a186ae',
                    'agent-f6fb1e0ddff44d27bb3e19e243a70584'
                )
                """
            ).fetchone()[0]
        finally:
            connection.close()

    def _get_agent_prompt(self, apid: str) -> str:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute("SELECT prompt FROM agentpreset WHERE apid = ?", (apid,)).fetchone()
            if row is None:
                raise AssertionError(f"Missing agentpreset row for {apid}")
            return row[0]
        finally:
            connection.close()

    def test_seed_writes_all_missing_system_agents(self) -> None:
        missing = ensure_system_agents_seeded(self.db_path)

        self.assertEqual(len(missing), 4)
        self.assertEqual(self._count_system_agents(), 4)

    def test_seed_is_idempotent_after_initial_insert(self) -> None:
        first = ensure_system_agents_seeded(self.db_path)
        second = ensure_system_agents_seeded(self.db_path)

        self.assertEqual(len(first), 4)
        self.assertEqual(second, [])
        self.assertEqual(self._count_system_agents(), 4)

    def test_restore_default_overwrites_existing_system_agent(self) -> None:
        ensure_system_agents_seeded(self.db_path)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE agentpreset SET prompt = ? WHERE apid = ?",
                ("custom prompt", "agent-1bad27aabaac439da678f31d53855b5d"),
            )
            connection.commit()
        finally:
            connection.close()

        restore_system_agent_default("agent-1bad27aabaac439da678f31d53855b5d", self.db_path)

        self.assertIn("你是聊天消息翻译助手", self._get_agent_prompt("agent-1bad27aabaac439da678f31d53855b5d"))


if __name__ == "__main__":
    unittest.main()
