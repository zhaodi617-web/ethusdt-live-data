import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import event_risk_alert as m

SAMPLE = '''BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Producer Price Index for October 2026\nDTSTART;TZID=America/New_York:20261113T083000\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:Holiday\nDTSTART;TZID=America/New_York:20261111T083000\nEND:VEVENT\nEND:VCALENDAR'''

class EventAlertTest(unittest.TestCase):
    def test_calendar_and_dst(self):
        data = m.parse_ical(SAMPLE)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['utc'], '2026-11-13T13:30:00+00:00')
    def test_folded_name(self):
        data = m.parse_ical('BEGIN:VEVENT\nSUMMARY:Consumer Price \n Index\nDTSTART:20261210T133000Z\nEND:VEVENT')
        self.assertEqual(len(data), 1)
    def test_one_alert_per_threshold(self):
        with TemporaryDirectory() as td, patch.object(m, 'STATE', Path(td)/'state.json'), \
             patch.object(m, 'get_events', return_value=m.VERIFIED_FALLBACK), \
             patch.object(m, 'send_message') as send:
            self.assertEqual(m.main(datetime(2026,11,13,11,30,tzinfo=timezone.utc)),1)
            self.assertEqual(m.main(datetime(2026,11,13,11,35,tzinfo=timezone.utc)),0)
            self.assertEqual(m.main(datetime(2026,11,13,12,30,tzinfo=timezone.utc)),1)
            self.assertEqual(m.main(datetime(2026,11,13,12,35,tzinfo=timezone.utc)),0)
            self.assertEqual(send.call_count,2)
    def test_no_post_release(self):
        with TemporaryDirectory() as td, patch.object(m, 'STATE', Path(td)/'state.json'), \
             patch.object(m, 'get_events', return_value=m.VERIFIED_FALLBACK), \
             patch.object(m, 'send_message') as send:
            self.assertEqual(m.main(datetime(2026,11,13,13,31,tzinfo=timezone.utc)),0)
            send.assert_not_called()
if __name__ == '__main__': unittest.main()
