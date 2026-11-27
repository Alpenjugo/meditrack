import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from app import SCHEMA, connect, dose_status, format_hours, hours_to_minutes, save


class DoseRulesTest(unittest.TestCase):
    def test_hour_input_preserves_existing_minute_values(self):
        for minutes in range(1, 10081):
            self.assertEqual(hours_to_minutes(format_hours(minutes)), minutes)
        self.assertEqual(hours_to_minutes("1,5"), 90)
        self.assertEqual(hours_to_minutes("1.5"), 90)
        for invalid in ("0", "-1", "NaN", "169"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                hours_to_minutes(invalid)

    def test_daily_limit_and_interval_across_midnight(self):
        zone = ZoneInfo("Europe/Zurich")
        now = int(datetime(2026, 10, 24, 23, 30, tzinfo=zone).timestamp())
        with closing(connect(":memory:")) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO children VALUES (1, 'Alex')")
            db.execute("INSERT INTO medicines(id, child, name, interval_minutes, daily_limit) VALUES (1, 1, 'Test', 120, 1)")
            db.commit()
            dose = {"action": ["dose"], "medicine": ["1"]}
            save(db, dose, now, zone)
            med = db.execute("SELECT * FROM medicines").fetchone()
            self.assertEqual(dose_status(db, med, now, zone), (1, now + 7200))
            self.assertEqual(dose_status(db, med, now + 1800, zone), (0, now + 7200))
            with self.assertRaises(ValueError):
                save(db, dose, now + 1800, zone)
            save(db, dose, now + 7200, zone)
            self.assertEqual(dose_status(db, med, now + 7200, zone)[0], 1)

    def test_daily_limit_spans_a_25_hour_day(self):
        zone = ZoneInfo("Europe/Zurich")
        now = int(datetime(2026, 10, 25, 0, 0, tzinfo=zone).timestamp())
        with closing(connect(":memory:")) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO children VALUES (1, 'Alex')")
            db.execute("INSERT INTO medicines(id, child, name, interval_minutes, daily_limit) VALUES (1, 1, 'Test', 1, 1)")
            db.commit()
            save(db, {"action": ["dose"], "medicine": ["1"]}, now, zone)
            med = db.execute("SELECT * FROM medicines").fetchone()
            self.assertEqual(dose_status(db, med, now, zone), (1, now + 25 * 3600))


class AppTest(unittest.TestCase):
    def test_recording_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(__file__).with_name("app.py")
            process = subprocess.Popen(
                [sys.executable, str(app), "--local", "--port", "0",
                 "--database", str(Path(directory) / "test.sqlite3")],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            try:
                port = int(process.stdout.readline().split()[-1])
                # Exercise the same trailing-slash subpath used by Home Assistant Ingress.
                url = f"http://127.0.0.1:{port}/api/hassio_ingress/test/"

                def request(query="", **fields):
                    data = urlencode(fields).encode() if fields else None
                    target = url + query
                    with urlopen(target, data=data, timeout=5) as response:
                        self.assertEqual(response.geturl(), target)
                        return response.read().decode()

                page = request()
                token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
                with self.assertRaises(HTTPError) as error:
                    request(action="child", name="Ungültig")
                self.assertEqual(error.exception.code, 403)
                self.assertIn("Alex", request(csrf=token, action="child", name="Alex"))
                self.assertIn("Testmedizin", request(
                    csrf=token, action="medicine", child=1,
                    name="Testmedizin", interval_hours="1,5", limit=2,
                ))
                self.assertIn("39,2 °C", request(
                    csrf=token, action="temperature", child=1, temperature="39,2",
                ))
                self.assertIn("1 von 2 Gaben heute", request(
                    csrf=token, action="dose", medicine=1, note="Testnotiz",
                ))
                with self.assertRaises(HTTPError) as error:
                    request(csrf=token, action="dose", medicine=1)
                self.assertEqual(error.exception.code, 400)
                page = request()
                self.assertIn("Testnotiz", page)
                self.assertIn("1 von 2 Gaben heute", page)
                self.assertIn("Temperatur erfassen", page)
                self.assertIn("Gabe erfassen", page)
                self.assertIn("Zuletzt erfasst", page)
                self.assertIn("1,5 Std. Abstand", page)

                request(csrf=token, action="child", name="Noah")
                page = request("?kind=2")
                self.assertIn('<option value="2" selected>Noah</option>', page)
                self.assertNotIn("Testmedizin", page)
                self.assertNotIn("39,2 °C", page)
                page = request("?kind=2", csrf=token, action="temperature", child=2, temperature="38,1")
                self.assertIn("38,1 °C", page)
                self.assertNotIn("38,1 °C", request("?kind=1"))

                page = request("?vorlage=ibuprofen")
                self.assertIn('name="active_ingredient" maxlength="120" value="Ibuprofen"', page)
                page = request(csrf=token, action="catalog_add", name="Katalogmedizin",
                               interval_hours="4", limit=3, active_ingredient="Testwirkstoff",
                               formulation="Sirup", medicine_note="Testnotiz zum Produkt")
                self.assertIn("Aus dem Katalog", page)
                page = request(csrf=token, action="catalog_assign", child=1, catalog_id=1)
                self.assertIn("0 von 3 Gaben heute · 4 Std. Abstand", page)
                self.assertIn("Testwirkstoff · Sirup", page)
                self.assertIn("Testnotiz zum Produkt", page)
                with self.assertRaises(HTTPError) as error:
                    request(csrf=token, action="catalog_assign", child=1, catalog_id=1)
                self.assertEqual(error.exception.code, 400)
                request(csrf=token, action="catalog_edit", catalog_id=1, name="Katalogmedizin",
                        interval_hours="6", limit=2)
                page = request("?kind=2", csrf=token, action="catalog_assign", child=2, catalog_id=1)
                self.assertIn("0 von 2 Gaben heute · 6 Std. Abstand", page)
                self.assertIn("0 von 3 Gaben heute · 4 Std. Abstand", request("?kind=1"))
            finally:
                process.terminate()
                process.wait(timeout=5)
                process.stdout.close()
