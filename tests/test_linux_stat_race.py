"""A disappearing owned /proc stat is not an ownership-supervisor failure."""
import errno
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import owned_process_linux as backend


@unittest.skipUnless(sys.platform == 'linux', 'Linux /proc and pidfd race regression only')
class LinuxStatRaceTests(unittest.TestCase):
    def test_esrch_stat_is_a_disappeared_process(self):
        with patch.object(backend.Path, 'read_text', side_effect=ProcessLookupError(errno.ESRCH, 'synthetic exited process')):
            self.assertIsNone(backend._stat(41001))

    def test_enoent_stat_remains_a_disappeared_process(self):
        with patch.object(backend.Path, 'read_text', side_effect=FileNotFoundError(errno.ENOENT, 'synthetic absent process')):
            self.assertIsNone(backend._stat(41001))

    def test_permission_error_is_not_treated_as_a_clean_exit(self):
        with patch.object(backend.Path, 'read_text', side_effect=PermissionError(errno.EACCES, 'synthetic denied stat')):
            with self.assertRaises(PermissionError):
                backend._stat(41001)

    def test_pidfd_is_closed_if_process_disappears_after_open(self):
        row = {'pid': 41001, 'ppid': 41000, 'start_ticks': 9, 'state': 'R'}
        known = {}
        with patch.object(backend, '_descendants', return_value={41001: row}), \
             patch.object(backend.os, 'pidfd_open', return_value=81), \
             patch.object(backend.Path, 'read_text', side_effect=ProcessLookupError(errno.ESRCH, 'synthetic post-open exit')), \
             patch.object(backend.os, 'close') as close, \
             patch.object(backend.signal, 'pidfd_send_signal') as signal:
            backend._remember(known)
            self.assertEqual(known, {})
            close.assert_called_once_with(81)
            signal.assert_not_called()

    def test_pidfd_is_closed_but_permission_error_still_fails(self):
        row = {'pid': 41001, 'ppid': 41000, 'start_ticks': 9, 'state': 'R'}
        with patch.object(backend, '_descendants', return_value={41001: row}), \
             patch.object(backend.os, 'pidfd_open', return_value=81), \
             patch.object(backend.Path, 'read_text', side_effect=PermissionError(errno.EACCES, 'synthetic denied stat')), \
             patch.object(backend.os, 'close') as close:
            with self.assertRaises(PermissionError):
                backend._remember({})
            close.assert_called_once_with(81)
