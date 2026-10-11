"""Deterministic pipe fragmentation checks for the native test launcher."""
import os
import threading
import time
import unittest

from display_ready import read_display_notice


class DisplayReadyTests(unittest.TestCase):
    def fragmented(self, initial, remainder):
        incoming, outgoing = os.pipe()
        os.write(outgoing, initial)
        errors = []
        def finish():
            try:
                time.sleep(.04)
                os.write(outgoing, remainder)
            except OSError as error: errors.append(error)
            finally: os.close(outgoing)
        writer = threading.Thread(target=finish); writer.start()
        try: data = read_display_notice(incoming, timeout=.5)
        finally:
            os.close(incoming); writer.join(1)
        self.assertFalse(writer.is_alive())
        self.assertEqual(data, initial+remainder)
        self.assertEqual(errors, [], 'Reader closed before the complete server notice')

    def test_newline_in_separate_write(self):
        self.fragmented(b'123', b'\n')

    def test_digits_in_separate_writes(self):
        self.fragmented(b'1', b'23\n')

    def test_eof_does_not_invent_a_complete_notice(self):
        for notice in (b'', b'123'):
            incoming, outgoing = os.pipe()
            os.write(outgoing, notice); os.close(outgoing)
            try: self.assertEqual(read_display_notice(incoming, timeout=.5), notice)
            finally: os.close(incoming)

    def test_unterminated_notice_is_bounded(self):
        incoming, outgoing = os.pipe(); os.write(outgoing, b'1'*128)
        try: self.assertEqual(read_display_notice(incoming, timeout=.5), b'1'*64)
        finally: os.close(incoming); os.close(outgoing)

    def test_partial_notice_does_not_refresh_or_bypass_deadline(self):
        incoming, outgoing = os.pipe(); os.write(outgoing, b'1')
        started = time.monotonic()
        try: self.assertEqual(read_display_notice(incoming, timeout=.06), b'1')
        finally: os.close(incoming); os.close(outgoing)
        elapsed = time.monotonic()-started
        self.assertGreaterEqual(elapsed, .04); self.assertLess(elapsed, .5)


if __name__ == '__main__': unittest.main(verbosity=2)
