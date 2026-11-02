import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import t3cfg_sync  # noqa: E402


class SyncParsingTests(unittest.TestCase):
    def test_all(self):
        self.assertEqual(
            t3cfg_sync.parse_components("all"),
            {"t3", "omp", "opencode", "qwen"},
        )

    def test_list(self):
        self.assertEqual(
            t3cfg_sync.parse_components("t3,omp"),
            {"t3", "omp"},
        )

    def test_invalid(self):
        with self.assertRaises(Exception):
            t3cfg_sync.parse_components("t3,nope")


class SyncLockTests(unittest.TestCase):
    def test_lock_is_exclusive_and_releasable(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "sync.lock"
            first = t3cfg_sync.acquire_lock(lock, force=False)
            self.assertIsNotNone(first)
            self.assertIsNone(t3cfg_sync.acquire_lock(lock, force=False))
            t3cfg_sync.release_lock(first, lock)
            second = t3cfg_sync.acquire_lock(lock, force=False)
            self.assertIsNotNone(second)
            t3cfg_sync.release_lock(second, lock)
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
