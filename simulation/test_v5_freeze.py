"""The V5 freeze: the committed record verifies, any change is refused and the runner needs it."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import v5_freeze
import v5_validation


class FreezeTests(unittest.TestCase):
    def test_committed_record_verifies(self):
        if not v5_freeze.RECORD.is_file():
            self.skipTest('V5 is not frozen yet')
        result = v5_freeze.verify()
        self.assertTrue(result['verified'])
        self.assertEqual(result['candidate'], 'v5_full')

    def test_any_change_after_freezing_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in v5_freeze.FILES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(v5_freeze.ROOT / name, root / name)
            with mock.patch.object(v5_freeze, 'ROOT', root), \
                    mock.patch.object(v5_freeze, 'RECORD', root / 'v5_freeze_record.json'):
                frozen = v5_freeze.freeze()
                self.assertEqual(set(frozen['files']), set(v5_freeze.FILES))
                self.assertTrue(v5_freeze.verify()['verified'])
                with self.assertRaises(ValueError):
                    v5_freeze.freeze()
                plan = root / 'v5_ood_plan.json'
                plan.write_bytes(plan.read_bytes() + b' ')
                with self.assertRaisesRegex(ValueError, 'v5_ood_plan.json'):
                    v5_freeze.verify()

    def test_runner_refuses_without_a_valid_freeze_and_closed_seeds_stay_closed(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(v5_freeze, 'RECORD', Path(tmp) / 'missing.json'):
            with self.assertRaises(ValueError):
                v5_validation.require_frozen()
        for seed in (6000, 6063, 8000, 8063):
            with self.assertRaises(ValueError):
                v5_validation.validation_case(seed, 'generated_underload', False)


if __name__ == '__main__':
    unittest.main()
