"""End-to-end tests for CommuteSync. Run: python -m unittest discover tests  (or pytest)"""
import os
import sqlite3
import sys
import tempfile
from datetime import date, timedelta

import unittest

DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["COMMUTESYNC_DB"] = DB
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as cs  # noqa: E402

TOMORROW = (date.today() + timedelta(days=1)).isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def fresh_client():
    with cs.app.app_context():
        db = cs.get_db()
        db.executescript("DELETE FROM bookings; DELETE FROM rides; DELETE FROM users;")
        db.commit()
    return cs.app.test_client()


def register_login(c, name, email):
    c.post("/register", data={"name": name, "email": email, "password": "secret1"})
    return c.post("/login", data={"email": email, "password": "secret1"})


def make_ride(c, seats=1, pickup="Ambattur", dest="Guindy", day=TOMORROW):
    return c.post("/create-ride", data={"pickup": pickup, "destination": dest,
                                        "ride_date": day, "ride_time": "09:00",
                                        "seats": seats, "fare": 50})


def ride_row(pickup="Ambattur"):
    with cs.app.app_context():
        return cs.get_db().execute("SELECT * FROM rides WHERE pickup=?", (pickup,)).fetchone()


class CommuteSyncTests(unittest.TestCase):
    def test_all_pages_render(self):
        client = fresh_client()
        for path in ["/", "/login", "/register"]:
            assert client.get(path).status_code == 200
        assert client.get("/nope").status_code == 404
        assert client.get("/dashboard").status_code == 302  # login required


    def test_auth_flow(self):
        client = fresh_client()
        assert register_login(client, "Driver", "d@x.com").status_code == 302
        assert client.get("/register").status_code == 302  # logged-in users skip register
        client.get("/logout")
        dup = client.post("/register", data={"name": "X", "email": "D@X.com", "password": "secret1"})
        assert dup.status_code == 400 and b"already registered" in dup.data
        bad = client.post("/login", data={"email": "d@x.com", "password": "wrong!"})
        assert bad.status_code == 401 and b"Invalid email" in bad.data


    def test_create_ride_validation(self):
        client = fresh_client()
        register_login(client, "Driver", "d@x.com")
        assert make_ride(client, day=YESTERDAY).status_code == 400
        assert make_ride(client, seats=0).status_code == 400
        assert make_ride(client, pickup="A", dest="a").status_code == 400
        assert make_ride(client).status_code == 302
        assert b"Ambattur" in client.get("/dashboard").data


    def test_join_flow_and_seat_accounting(self):
        client = fresh_client()
        driver, p1, p2 = (cs.app.test_client() for _ in range(3))
        register_login(driver, "Driver", "d@x.com")
        make_ride(driver, seats=1)
        rid = ride_row()["id"]

        # driver can't see or join own ride
        # driver still sees their own ride mentioned in the SOS navbar
        # data attribute; check the ride-card attribute specifically.
        assert b'data-pickup="Ambattur"' not in driver.get("/search").data
        driver.post(f"/rides/{rid}/join")
        assert ride_row()["seats"] == 1

        register_login(p1, "P1", "p1@x.com")
        assert b"Join Ride" in p1.get("/search").data
        p1.post(f"/rides/{rid}/join")
        assert ride_row()["seats"] == 0
        assert b"Joined" in p1.get("/search").data
        p1.post(f"/rides/{rid}/join")               # double join blocked
        assert ride_row()["seats"] == 0

        register_login(p2, "P2", "p2@x.com")
        assert b"Full" in p2.get("/search").data
        p2.post(f"/rides/{rid}/join")               # full ride blocked
        assert ride_row()["seats"] == 0

        # cancel frees the seat
        with cs.app.app_context():
            bid = cs.get_db().execute("SELECT id FROM bookings").fetchone()["id"]
        p1.post(f"/bookings/{bid}/cancel")
        assert ride_row()["seats"] == 1
        p2.post(f"/bookings/{bid}/cancel")          # can't cancel someone else's (already gone anyway)

        # delete ride cascades bookings
        p2.post(f"/rides/{rid}/join")
        p2.post(f"/rides/{rid}/delete")             # not owner -> no effect
        assert ride_row() is not None
        driver.post(f"/rides/{rid}/delete")
        assert ride_row() is None
        with cs.app.app_context():
            assert cs.get_db().execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0


    def test_search_filters(self):
        client = fresh_client()
        driver = cs.app.test_client()
        register_login(driver, "Driver", "d@x.com")
        make_ride(driver, pickup="Ambattur", dest="Guindy")
        make_ride(driver, pickup="Tambaram", dest="Velachery")
        register_login(client, "P", "p@x.com")
        html = client.get("/search?pickup=ambat").data
        assert b"Ambattur" in html and b"Tambaram" not in html
        html = client.get("/search?destination=Velach").data
        assert b"Tambaram" in html and b"Ambattur" not in html
        assert b"No rides match" in client.get("/search?ride_date=" + YESTERDAY).data

    def test_safety_contact_and_sos_data(self):
        client = fresh_client()
        register_login(client, "Driver", "d@x.com")

        html = client.get("/dashboard").data
        assert b'id="sos-btn"' in html
        assert b'data-phone=""' in html
        assert b"You haven't set an emergency contact" in html

        bad = client.post("/safety", data={"emergency_name": "Mom", "emergency_phone": "abc"})
        assert bad.status_code == 400

        good = client.post("/safety", data={"emergency_name": "Mom", "emergency_phone": "+919876543210"})
        assert good.status_code == 302

        html = client.get("/dashboard").data
        assert b'data-phone="+919876543210"' in html
        assert b"You haven't set an emergency contact" not in html
        assert b"+919876543210" in client.get("/safety").data

    def test_sos_includes_next_ride_in_navbar_data(self):
        client = fresh_client()
        register_login(client, "Driver", "d@x.com")
        client.post("/safety", data={"emergency_name": "Mom", "emergency_phone": "+919876543210"})
        make_ride(client, pickup="Ambattur", dest="Guindy")

        html = client.get("/dashboard").data
        assert b'data-ride-pickup="Ambattur"' in html
        assert b'data-ride-destination="Guindy"' in html

    def test_search_page_exposes_route_selection_data(self):
        client = fresh_client()  # clears the DB before any client registers
        driver = cs.app.test_client()
        register_login(driver, "Driver", "d@x.com")
        make_ride(driver, pickup="Ambattur", dest="Guindy")
        rid = ride_row()["id"]

        register_login(client, "Rider", "r@x.com")
        html = client.get("/search").data
        assert b'id="route-summary"' in html
        assert f'data-ride-id="{rid}"'.encode() in html
        assert f'id="join-form-{rid}"'.encode() in html

    def test_db_migration_adds_safety_columns_to_old_schema(self):
        old_db = os.path.join(tempfile.mkdtemp(), "old.db")
        conn = sqlite3.connect(old_db)
        conn.execute("""CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password TEXT NOT NULL)""")
        conn.execute("INSERT INTO users (name, email, password) VALUES ('Old', 'old@x.com', 'h')")
        conn.commit()
        conn.close()

        old_path = cs.app.config["DATABASE"]
        cs.app.config["DATABASE"] = old_db
        try:
            cs.init_db()
            with cs.app.app_context():
                cols = {r["name"] for r in cs.get_db().execute("PRAGMA table_info(users)")}
        finally:
            cs.app.config["DATABASE"] = old_path

        assert "emergency_name" in cols and "emergency_phone" in cols


if __name__ == "__main__":
    unittest.main()
