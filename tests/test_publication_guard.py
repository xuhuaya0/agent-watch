import unittest

from tools.check_publication import private_path


class DiagnosticPrivacyTests(unittest.TestCase):
    def test_diagnostic_logs_and_rotations_are_private_but_source_is_publishable(self):
        for name in ('control-diagnostics.jsonl', 'control-diagnostics.jsonl.1',
                     'nested/control-diagnostics.jsonl'):
            with self.subTest(name=name):
                self.assertTrue(private_path(name))
        self.assertFalse(private_path('aw/control/diagnostics.py'))
