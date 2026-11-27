import argparse
import json
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS children (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS medicines (
    id INTEGER PRIMARY KEY,
    child INTEGER NOT NULL REFERENCES children(id),
    name TEXT NOT NULL COLLATE NOCASE,
    interval_minutes INTEGER NOT NULL,
    daily_limit INTEGER NOT NULL,
    UNIQUE(child, name)
);
CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY,
    medicine INTEGER NOT NULL REFERENCES medicines(id),
    taken_at INTEGER NOT NULL,
    note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dose_times ON doses(medicine, taken_at);
CREATE TABLE IF NOT EXISTS temperatures (
    id INTEGER PRIMARY KEY,
    child INTEGER NOT NULL REFERENCES children(id),
    measured_at INTEGER NOT NULL,
    tenths INTEGER NOT NULL,
    note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS temperature_times ON temperatures(child, measured_at);
"""

STYLE = """
:root {
    color-scheme: light dark;
    font: 16px system-ui, sans-serif;
    --bg: #f5f8f8;
    --fg: #152622;
    --muted: #465d57;
    --card: #fff;
    --border: #cfddda;
    --accent: #00675f;
    --tile: #d7efeb;
    --tile-fg: #084944;
    --wait: #fff0ce;
    --wait-fg: #713700;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg: #12201e;
        --fg: #e4f3ed;
        --muted: #b1c8c1;
        --card: #1b2d29;
        --border: #46635b;
        --accent: #08746b;
        --tile: #214540;
        --tile-fg: #ddf6f0;
        --wait: #55350e;
        --wait-fg: #ffe1a5;
    }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); }
main { max-width: 680px; margin: auto; padding: clamp(16px, 5vw, 40px); }
header { display: flex; align-items: center; gap: .85rem; margin-bottom: 1.5rem; }
.mark { display: grid; place-items: center; width: 48px; height: 48px; flex: none;
    border-radius: 13px; background: var(--accent); color: white; font-size: 2rem; }
h1, h2, h3, p { margin: 0; overflow-wrap: anywhere; }
h1 { font-size: clamp(1.7rem, 5vw, 2.35rem); letter-spacing: -.045em; }
h2 { font-size: 1.15rem; margin-bottom: .6rem; }
h3 { font-size: 1.05rem; margin-bottom: .35rem; }
header p, small, footer { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 15px;
    padding: 1rem; margin-bottom: .9rem; }
.quick-action { margin-bottom: .75rem; }
.quick-action > summary {
    display: flex; align-items: center; gap: .8rem; padding: 1rem; min-height: 76px;
    border-radius: 14px; background: var(--tile); color: var(--tile-fg);
    font-size: clamp(1.1rem, 4vw, 1.4rem); font-weight: 700; list-style: none;
}
.quick-action > summary::-webkit-details-marker { display: none; }
.quick-action > summary::after { content: "›"; margin-left: auto; font-size: 1.6rem; }
.quick-action[open] > summary::after { transform: rotate(90deg); }
.quick-action > .card { margin: .4rem 0 0; }
.action-icon { font-size: 1.55rem; }
form { display: flex; flex-wrap: wrap; align-items: end; gap: .65rem; margin: .65rem 0 0; }
label { display: grid; gap: .3rem; flex: 1 1 150px; font-size: .9rem; }
input, button, select { font: inherit; padding: .55rem .7rem; border: 1px solid var(--border);
    border-radius: 8px; min-width: 0; min-height: 44px; }
button { background: var(--accent); color: white; cursor: pointer; }
button:disabled { opacity: .55; cursor: default; }
summary { cursor: pointer; }
.settings { margin-top: .65rem; }
.child-picker { margin: 0 0 1rem; align-items: end; }
.badge { display: inline-block; padding: .25rem .6rem; border-radius: 8px;
    margin: .5rem 0 .15rem; background: var(--tile); color: var(--tile-fg); }
.wait { background: var(--wait); color: var(--wait-fg); }
.entries { list-style: none; margin: 0; padding: 0; }
.entries li { display: grid; grid-template-columns: 3.3rem minmax(0, 1fr); gap: .75rem;
    padding: .55rem 0; border-top: 1px solid var(--border); overflow-wrap: anywhere; }
.entries time { color: var(--muted); font-variant-numeric: tabular-nums; }
.alert { margin-bottom: 1rem; padding: .85rem; border-radius: 8px;
    background: var(--wait); color: var(--wait-fg); }
footer { font-size: .85rem; }
"""


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def clean(value, maximum, required=False):
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValueError(f"Bitte einen gültigen Text mit maximal {maximum} Zeichen eingeben.")
    return value


def number(value, low, high):
    try:
        result = int(value)
    except ValueError:
        raise ValueError("Bitte eine ganze Zahl eingeben.") from None
    if not low <= result <= high:
        raise ValueError(f"Bitte eine Zahl von {low} bis {high} eingeben.")
    return result


def local_time(timestamp, zone):
    return datetime.fromtimestamp(timestamp, zone)


def format_time(timestamp, zone):
    return local_time(timestamp, zone).strftime("%d.%m.%Y, %H:%M %Z")


def dose_status(db, medicine, now, zone):
    current = local_time(now, zone)
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = midnight + timedelta(days=1)
    last = db.execute(
        "SELECT MAX(taken_at) FROM doses WHERE medicine = ?", (medicine["id"],)
    ).fetchone()[0]
    count = db.execute(
        "SELECT COUNT(*) FROM doses WHERE medicine = ? AND taken_at >= ? AND taken_at < ?",
        (medicine["id"], int(midnight.timestamp()), int(tomorrow.timestamp())),
    ).fetchone()[0]
    next_time = now
    if last is not None:
        next_time = max(next_time, last + medicine["interval_minutes"] * 60)
    if count >= medicine["daily_limit"]:
        next_time = max(next_time, int(tomorrow.timestamp()))
    return count, next_time


def save(db, values, now, zone):
    def field(name):
        return values.get(name, [""])[0]

    action = field("action")
    with db:
        db.execute("BEGIN IMMEDIATE")
        if action == "child":
            db.execute("INSERT INTO children(name) VALUES (?)", (clean(field("name"), 80, True),))
        elif action in {"medicine", "edit_medicine"}:
            settings = (
                clean(field("name"), 80, True),
                number(field("interval"), 1, 10080),
                number(field("limit"), 1, 99),
            )
            if action == "medicine":
                db.execute(
                    "INSERT INTO medicines(name, interval_minutes, daily_limit, child) VALUES (?, ?, ?, ?)",
                    (*settings, int(field("child"))),
                )
            else:
                result = db.execute(
                    "UPDATE medicines SET name = ?, interval_minutes = ?, daily_limit = ? WHERE id = ?",
                    (*settings, int(field("medicine"))),
                )
                if result.rowcount != 1:
                    raise ValueError("Medikament nicht gefunden.")
        elif action == "temperature":
            try:
                value = Decimal(field("temperature").strip().replace(",", ".")) * 10
                if not value.is_finite() or not 200 <= value <= 500 or value != value.to_integral_value():
                    raise InvalidOperation
            except InvalidOperation:
                raise ValueError("Bitte eine Temperatur zwischen 20 und 50 °C mit höchstens einer Nachkommastelle eingeben.") from None
            db.execute(
                "INSERT INTO temperatures(child, measured_at, tenths, note) VALUES (?, ?, ?, ?)",
                (int(field("child")), now, int(value), clean(field("note"), 240)),
            )
        elif action == "dose":
            medicine = db.execute("SELECT * FROM medicines WHERE id = ?", (int(field("medicine")),)).fetchone()
            if medicine is None:
                raise ValueError("Medikament nicht gefunden.")
            _, next_time = dose_status(db, medicine, now, zone)
            if next_time > now:
                raise ValueError(f"Nach deinen Einstellungen frühestens wieder am {format_time(next_time, zone)}.")
            db.execute(
                "INSERT INTO doses(medicine, taken_at, note) VALUES (?, ?, ?)",
                (medicine["id"], now, clean(field("note"), 240)),
            )
        else:
            raise ValueError("Unbekannte Aktion.")


def render(db, now, zone, token, error="", selected="", open_action=""):
    def form(action, content, button, **fields):
        hidden = {"csrf": token, "action": action, **fields}
        inputs = "".join(
            f'<input type="hidden" name="{key}" value="{escape(str(value))}">'
            for key, value in hidden.items()
        )
        return f'<form method="post">{inputs}{content}<button type="submit">{button}</button></form>'

    def medicine_fields(name="", interval="", limit=""):
        return (
            f'<label>Medikament<input name="name" maxlength="80" value="{escape(name)}" required></label>'
            f'<label>Mindestabstand (Minuten)<input name="interval" type="number" min="1" max="10080" value="{interval}" required></label>'
            f'<label>Max. Gaben pro Kalendertag<input name="limit" type="number" min="1" max="99" value="{limit}" required></label>'
        )

    note = '<label>Notiz (optional)<input name="note" maxlength="240"></label>'
    parts = [
        f'<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>MediTrack</title><style>{STYLE}</style><main>',
        '<header><span class="mark" aria-hidden="true">+</span><div><h1>MediTrack</h1>'
        '<p>Fieber- und Medikamenten-Tracker</p></div></header>',
    ]
    if error:
        parts.append(f'<p class="alert" role="alert">{escape(error)}</p>')
    children = db.execute("SELECT * FROM children ORDER BY name, id").fetchall()
    if children:
        child = next((row for row in children if str(row["id"]) == selected), children[0])
        child_id = child["id"]
        if len(children) > 1:
            parts.append('<form method="get" class="child-picker"><label>Kind<select name="kind">')
            for row in children:
                active = " selected" if row["id"] == child_id else ""
                parts.append(f'<option value="{row["id"]}"{active}>{escape(row["name"])}</option>')
            parts.append('</select></label><button type="submit">Anzeigen</button></form>')
        else:
            parts.append(f'<h2>{escape(child["name"])}</h2>')

        medicines = db.execute(
            "SELECT * FROM medicines WHERE child = ? ORDER BY name", (child_id,)
        ).fetchall()
        statuses = [(med, *dose_status(db, med, now, zone)) for med in medicines]
        opened = " open" if open_action == "temperature" else ""
        parts.append(f'<details class="quick-action"{opened}><summary><span class="action-icon" aria-hidden="true">🌡</span>Temperatur erfassen</summary><div class="card">')
        parts.append(form("temperature", '<label>Temperatur (°C)<input name="temperature" inputmode="decimal" maxlength="5" required></label>' + note, "Messung speichern", child=child_id))
        parts.append('</div></details>')
        opened = " open" if open_action == "dose" else ""
        parts.append(f'<details class="quick-action"{opened}><summary><span class="action-icon" aria-hidden="true">💊</span>Gabe erfassen</summary><div class="card">')
        if not medicines:
            parts.append('<p>Für dieses Kind ist noch kein Medikament hinterlegt.</p>')
        for med, _, next_time in statuses:
            parts.append(f'<h3>{escape(med["name"])}</h3>')
            if next_time > now:
                parts.append(f'<p class="badge wait">Frühestens {format_time(next_time, zone)}</p>')
            else:
                parts.append(form("dose", note, "Gabe speichern", medicine=med["id"]))
        parts.append('</div></details>')

        for med, count, next_time in statuses:
            waiting = next_time > now
            status = f'Frühestens {format_time(next_time, zone)}' if waiting else 'Eintrag möglich'
            badge = "badge wait" if waiting else "badge"
            parts.append(f'<article class="card"><h3>{escape(med["name"])}</h3>'
                         f'<p>{count} von {med["daily_limit"]} Gaben heute · {med["interval_minutes"]} Min. Abstand</p>'
                         f'<p class="{badge}">{escape(status)}</p>')
            opened = " open" if open_action == "edit_medicine" else ""
            parts.append(f'<details class="settings"{opened}><summary>Medikament bearbeiten</summary>')
            parts.append(form("edit_medicine", medicine_fields(med["name"], med["interval_minutes"], med["daily_limit"]), "Speichern", medicine=med["id"]))
            parts.append("</details></article>")

        parts.append('<section class="card"><h2>Zuletzt erfasst</h2>')
        entries = db.execute("""
            SELECT measured_at AS timestamp, 0 AS kind, tenths, '' AS name, note, id
            FROM temperatures WHERE child = ?
            UNION ALL
            SELECT taken_at, 1, 0, medicines.name, doses.note, doses.id
            FROM doses JOIN medicines ON medicines.id = doses.medicine WHERE medicines.child = ?
            ORDER BY timestamp DESC, kind DESC, id DESC LIMIT 50
        """, (child_id, child_id)).fetchall()
        if entries:
            parts.append('<ul class="entries">')
            for entry in entries:
                label = entry["name"] if entry["kind"] else f'{entry["tenths"] / 10:.1f}'.replace(".", ",") + " °C"
                recorded = local_time(entry["timestamp"], zone)
                date = "" if recorded.date() == local_time(now, zone).date() else recorded.strftime("%d.%m.%Y · ")
                suffix = f'<br><small>{escape(entry["note"])}</small>' if entry["note"] else ""
                parts.append(f'<li><time datetime="{recorded.isoformat()}">{recorded:%H:%M}</time>'
                             f'<div>{date}<strong>{escape(label)}</strong>{suffix}</div></li>')
            parts.append('</ul>')
        else:
            parts.append('<p>Noch keine Einträge.</p>')
        parts.append('</section>')
        opened = " open" if not medicines or open_action == "medicine" else ""
        parts.append(f'<details class="card"{opened}><summary>Medikament hinzufügen</summary>')
        parts.append(form("medicine", medicine_fields(), "Hinzufügen", child=child_id))
        parts.append('</details>')

    opened = " open" if not children or open_action == "child" else ""
    parts.append(f'<details class="card"{opened}><summary>Kind hinzufügen</summary>')
    parts.append(form("child", '<label>Name oder Kürzel<input name="name" maxlength="80" required></label>', "Hinzufügen"))
    parts.append(f'</details><footer>Kalendertage gelten in {escape(zone.key)}. Die App prüft deine Einstellungen. Sie berechnet keine Dosierung.</footer></main></html>')
    return "".join(parts).encode()


class Handler(BaseHTTPRequestHandler):
    def allowed(self):
        if self.client_address[0] != self.server.allowed_ip:
            self.send_error(403)
            return False
        return True

    def selected_child(self):
        query = parse_qs(urlsplit(self.path).query)
        return query.get("kind", [""])[0]

    def page(self, status=200, error="", open_action=""):
        with closing(connect(self.server.database)) as db:
            body = render(
                db, int(datetime.now(timezone.utc).timestamp()), self.server.zone,
                self.server.token, error, self.selected_child(), open_action,
            )
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.allowed():
            self.page()

    def do_POST(self):
        if not self.allowed():
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                self.send_error(400)
                return
            values = parse_qs(self.rfile.read(size).decode(), keep_blank_values=True, max_num_fields=12)
        except ValueError:
            self.send_error(400)
            return
        token = values.get("csrf", [""])[0].encode()
        if not secrets.compare_digest(token, self.server.token.encode()):
            self.send_error(403)
            return
        action = values.get("action", [""])[0]
        try:
            with closing(connect(self.server.database)) as db:
                save(db, values, int(datetime.now(timezone.utc).timestamp()), self.server.zone)
        except ValueError as exc:
            self.page(400, str(exc), action)
            return
        except sqlite3.IntegrityError:
            self.page(400, "Der Name ist bereits vergeben oder der zugehörige Eintrag fehlt.", action)
            return
        self.send_response(303)
        selected = self.selected_child()
        query = "?" + urlencode({"kind": selected}) if selected else ""
        self.send_header("Location", "./" + query)
        self.end_headers()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--timezone")
    args = parser.parse_args()
    options = Path("/data/options.json")
    settings = json.loads(options.read_text()) if not args.local and options.exists() else {}
    database = args.database or Path("meditrack.sqlite3" if args.local else "/data/meditrack.sqlite3")
    zone = ZoneInfo(args.timezone or settings.get("timezone", "Europe/Zurich"))
    with closing(connect(database)) as db:
        db.executescript(SCHEMA)
    with ThreadingHTTPServer(("127.0.0.1" if args.local else "0.0.0.0", args.port), Handler) as server:
        server.allowed_ip = "127.0.0.1" if args.local else "172.30.32.2"
        server.database = database
        server.zone = zone
        server.token = secrets.token_urlsafe(32)
        print(f"MediTrack listening on port {server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
