"""The imported v0.4.0 source keeps every byte after moving into simulation/."""
import hashlib
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent
# Git reads these only at the repository root, so they stayed there.
AT_ROOT = {'.gitattributes', '.gitignore'}


def located(path):
    return (REPOSITORY if path in AT_ROOT else HERE) / path


class SourceLayoutTests(unittest.TestCase):
    def test_every_imported_file_is_byte_identical(self):
        manifest = json.loads((HERE / 'SOURCE_IMPORT_MANIFEST.json').read_text())
        retained = manifest['retained_original_files']
        self.assertEqual(len(retained), manifest['retained_original_file_count'])
        entries = [(e['path'], e['retained_sha256']) for e in retained]
        entries += [(e['path'], e['sha256']) for e in manifest['added_import_files']]
        for path, digest in entries:
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256(located(path).read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
