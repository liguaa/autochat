"""Identity and safety checks shared by both new Qt views."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autochat.cli import context_version
from autochat.ui_service import UIService


class UIServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = patch.multiple('autochat.ui_service', get_api_key=lambda: 'configured',
                                   get_typesafe_api_key=lambda: 'configured')
        self.keys.start()
        self.service = UIService(results_dir=Path(self.tmp.name))
        self.sid = self.service.selected_id
        self.service.dispatch('message.append', {'session_id': self.sid, 'role': 'contact', 'text': '先不聊工作了'})
        self.session = self.service._session()
        self.data = self.service._data(self.session)
        self.version = context_version(self.data)
        self.output = {'context_version': self.version, 'pipeline_mode': 'jev-dynamic',
                       'request_id': 'model-request', 'result': {'candidates': [{'text': '好，聊点别的。'}]}}
        self.session.cards[0] = {'state': 'complete', 'context_version': self.version, 'output': self.output}

    def tearDown(self):
        self.keys.stop()
        self.tmp.cleanup()

    def test_copy_checks_context_and_never_appends_message(self):
        payload = {'session_id': self.sid, 'index': 0, 'candidate_index': 0, 'text': '好，先放一放。',
                   'request_id': 'model-request', 'context_version': self.version, 'mode': 'jev-dynamic'}
        result = self.service.dispatch('candidate.copy', payload)
        self.assertEqual(result['text'], payload['text'])
        self.assertEqual(len(self.session.visible_messages), 1)
        self.service.dispatch('preferences.update', {'style': 'warm'})
        with self.assertRaisesRegex(ValueError, '重新分析'):
            self.service.dispatch('candidate.copy', payload)

    def test_me_message_requires_explicit_sent_confirmation(self):
        with self.assertRaisesRegex(ValueError, '确认'):
            self.service.dispatch('message.append', {'session_id': self.sid, 'role': 'me', 'text': '好的'})
        self.assertEqual(len(self.session.visible_messages), 1)

    def test_cancelled_request_rejects_late_progress(self):
        request = {'session_id': self.sid, 'index': 0, 'request_id': 'task-a',
                   'context_version': self.version, 'mode': 'jev-dynamic',
                   'config_fingerprint': 'fingerprint', 'data': self.data, 'cancelled': False}
        self.session.begin_request(0, self.data, 'task-a')
        self.service.active = request
        self.service.dispatch('analysis.cancel', {'request_id': 'task-a'})
        self.assertNotIn(0, self.session.cards)
        self.service._accept_progress(request, {**self.output, 'config_fingerprint': 'fingerprint'})
        self.assertNotIn(0, self.session.cards)

    def test_other_session_cannot_copy(self):
        other = self.service.dispatch('session.create', {'name': '项目讨论'})['selected_id']
        self.assertNotEqual(other, self.sid)
        with self.assertRaisesRegex(ValueError, '切换'):
            self.service.dispatch('candidate.copy', {'session_id': self.sid, 'index': 0,
                                                      'candidate_index': 0, 'text': '好'})


if __name__ == '__main__':
    unittest.main()
