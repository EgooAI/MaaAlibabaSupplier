import unittest

from app.web.pages.agent import _copy_messages_through_index, _remove_turn_at_index, _undo_last_turn_messages


class AgentPageUtilsTestCase(unittest.TestCase):
    def test_copy_messages_through_index_keeps_prefix(self) -> None:
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]

        copied = _copy_messages_through_index(messages, 1)

        self.assertEqual(
            copied,
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ],
        )
        self.assertIsNot(copied[0], messages[0])

    def test_remove_turn_at_index_removes_user_and_assistant_pair(self) -> None:
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]

        changed = _remove_turn_at_index(messages, 2)

        self.assertTrue(changed)
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ],
        )

    def test_remove_turn_at_index_removes_assistant_and_previous_user_pair(self) -> None:
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]

        changed = _remove_turn_at_index(messages, 3)

        self.assertTrue(changed)
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ],
        )

    def test_remove_turn_at_index_removes_unanswered_user_message(self) -> None:
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]

        changed = _remove_turn_at_index(messages, 2)

        self.assertTrue(changed)
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ],
        )

    def test_undo_last_turn_removes_last_user_and_assistant_pair(self) -> None:
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]

        changed = _undo_last_turn_messages(messages)

        self.assertTrue(changed)
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ],
        )

    def test_undo_last_turn_removes_pending_user_message_without_reply(self) -> None:
        messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]

        changed = _undo_last_turn_messages(messages)

        self.assertTrue(changed)
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
            ],
        )

    def test_undo_last_turn_returns_false_for_empty_messages(self) -> None:
        messages: list[dict[str, str]] = []

        changed = _undo_last_turn_messages(messages)

        self.assertFalse(changed)
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
