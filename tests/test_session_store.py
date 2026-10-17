import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autochat.replay import ReplaySession
from autochat.session_store import SessionStore
from autochat.ui_service import UIService


class SessionStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.keys = patch.multiple("autochat.ui_service", get_api_key=lambda: None, get_typesafe_api_key=lambda: None)
        self.keys.start()

    def tearDown(self):
        self.keys.stop()
        self.tmp.cleanup()

    def service(self):
        return UIService(results_dir=self.root / "results", sessions_dir=self.root / "sessions",
                         personas_dir=self.root / "personas")

    def test_automatic_save_restore_facts_recall_ids_and_no_requests(self):
        service = self.service()
        sid = service.selected_id
        service.dispatch("message.append", {"session_id": sid, "role": "contact", "text": "明天见"})
        service.dispatch("preferences.update", {"confirmed_facts": ["明天下午见面", "地点待定"]})
        session = service._session()
        mid = session.message_ids[0]
        receipt = service.dispatch("message.recall", {"session_id": sid, "message_id": mid,
            "expected_revision": session.revision, "operation_id": "recall-test"})
        service.dispatch("facts.confirm", {"session_id": sid, "expected_revision": session.revision, "index": 0})
        session.cards[0] = {"state": "progress", "output": {"private": "old-analysis"}}
        # Load without close(): mutation events, not exit, must have persisted it.
        restored = self.service()
        snapshot = restored.snapshot()
        self.assertEqual(snapshot["selected_id"], sid)
        self.assertEqual(snapshot["messages"][0]["id"], mid)
        self.assertNotIn("text", snapshot["messages"][0])
        self.assertTrue(snapshot["facts_pending_review"])
        self.assertEqual(snapshot["facts_reviewed_indices"], [0])
        self.assertIsNone(snapshot["active"])
        self.assertEqual(snapshot["cards"], {})
        restored.dispatch("message.restore", {"session_id": sid, "message_id": mid,
            "recall_id": receipt["recall_id"], "expected_revision": snapshot["session_revision"],
            "operation_id": "restore-test"})
        self.assertEqual(restored.snapshot()["messages"][0]["text"], "明天见")

    def test_delete_archives_and_restart_does_not_resurrect(self):
        service = self.service()
        sid = service.selected_id
        service.dispatch("message.append", {"session_id": sid, "role": "contact", "text": "synthetic"})
        service.results_dir.mkdir(parents=True, exist_ok=True)
        result = service.results_dir / "historical.json"
        result.write_text("{}", encoding="utf-8")
        request = {"session_id": sid, "cancelled": False}
        service.active = request
        service.dispatch("session.delete", {"session_id": sid, "expected_revision": service._session().revision})
        self.assertTrue(request["cancelled"])
        self.assertIsNone(service.active)
        self.assertNotIn(sid, service.sessions)
        self.assertTrue(result.exists())
        self.assertFalse((self.root / "sessions" / f"{sid}.json").exists())
        archived = list((self.root / "sessions" / "trash").glob(f"{sid}-*.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text(encoding="utf-8"))["messages"][0]["text"], "synthetic")
        restored = self.service()
        self.assertNotIn(sid, restored.sessions)
        self.assertEqual(len(restored.sessions), 1)

    def test_delete_failure_keeps_session_and_validates_revision(self):
        service = self.service()
        sid = service.selected_id
        with self.assertRaisesRegex(ValueError, "变化"):
            service.dispatch("session.delete", {"session_id": sid, "expected_revision": -1})
        with patch.object(service.session_store, "archive", side_effect=OSError("locked")):
            with self.assertRaises(OSError):
                service.dispatch("session.delete", {"session_id": sid, "expected_revision": 0})
        self.assertIn(sid, service.sessions)

    def test_corrupt_file_is_skipped_without_overwriting(self):
        directory = self.root / "sessions"
        directory.mkdir()
        bad = directory / "abcdef123456.json"
        bad.write_text("{broken", encoding="utf-8")
        service = self.service()
        self.assertTrue(service.snapshot()["session_storage_errors"])
        self.assertEqual(bad.read_text(encoding="utf-8"), "{broken")
        self.assertNotIn("abcdef123456", service.sessions)

    def test_failed_replace_preserves_disk_and_retry_saves_memory(self):
        service = self.service()
        sid = service.selected_id
        path = self.root / "sessions" / f"{sid}.json"
        previous = path.read_bytes()
        with patch("autochat.session_store.os.replace", side_effect=OSError("disk unavailable")):
            service.dispatch("message.append", {"session_id": sid, "role": "contact", "text": "unsaved"})
        self.assertEqual(path.read_bytes(), previous)
        self.assertTrue(service.snapshot()["session_storage_pending"])
        self.assertEqual(service.snapshot()["messages"][0]["text"], "unsaved")
        service.dispatch("sessions.save")
        self.assertFalse(service.snapshot()["session_storage_pending"])
        self.assertEqual(self.service().snapshot()["messages"][0]["text"], "unsaved")

    def test_other_instance_cannot_overwrite_or_resurrect_deleted_file(self):
        first = self.service()
        second = self.service()
        sid = first.selected_id
        first.dispatch("session.delete", {"session_id": sid, "expected_revision": 0})
        second.dispatch("message.append", {"session_id": sid, "role": "contact", "text": "stale edit"})
        self.assertTrue(second.snapshot()["session_storage_pending"])
        self.assertFalse((self.root / "sessions" / f"{sid}.json").exists())

    def test_many_revoked_messages_roundtrip_and_path_traversal_rejected(self):
        store = SessionStore(self.root / "sessions")
        session = ReplaySession()
        for i in range(35):
            session.append("contact", str(i))
            session.recall(session.message_ids[-1])
        store.save("abcdef123456", "long-session", session)
        loaded, errors = SessionStore(store.root).load()
        self.assertFalse(errors)
        self.assertEqual(len(loaded["abcdef123456"][1].messages), 35)
        self.assertEqual(len(loaded["abcdef123456"][1].active_visible_messages), 0)
        with self.assertRaisesRegex(ValueError, "ID 无效"):
            store.archive("../elsewhere")


if __name__ == "__main__":
    unittest.main()
