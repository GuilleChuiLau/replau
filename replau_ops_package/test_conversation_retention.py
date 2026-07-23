import os
import unittest
from unittest.mock import Mock, patch

import replau_conversation_retention as retention


class ConversationRetentionTest(unittest.TestCase):
    def test_posts_safe_default_retention(self):
        staff_response = Mock()
        staff_response.json.return_value = {"ok": True, "staff_replies_redacted": 0}
        response = Mock()
        response.json.return_value = {"ok": True, "active_redacted": 0, "closed_redacted": 0, "deleted": 0}
        with patch.dict(os.environ, {"POSTGREST_BASE_URL": "http://127.0.0.1:3000"}, clear=True), patch.object(
            retention.requests, "post", side_effect=[staff_response,response]
        ) as post:
            result = retention.run()
        staff_response.raise_for_status.assert_called_once_with()
        response.raise_for_status.assert_called_once_with()
        self.assertTrue(result["ok"])
        self.assertEqual(
            post.call_args_list[1].kwargs["json"],
            {"p_active_redact_days": 30, "p_closed_redact_days": 7, "p_delete_days": 90},
        )
        self.assertEqual(post.call_args_list[0].kwargs["json"],{"p_active_redact_days":30,"p_closed_redact_days":7})

    def test_rejects_unsafe_delete_window_before_request(self):
        with patch.dict(
            os.environ,
            {"WHATSAPP_REQUEST_CLOSED_REDACT_DAYS": "45", "WHATSAPP_REQUEST_DELETE_DAYS": "30"},
            clear=True,
        ), patch.object(retention.requests, "post") as post:
            with self.assertRaises(ValueError):
                retention.run()
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
