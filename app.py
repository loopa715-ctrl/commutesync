"""CommuteSync - a small Flask carpooling app."""
import os
from datetime import date, datetime
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("COMMUTESYNC_SECRET_KEY", "dev-change-me"),
    # Absolute path: the app works no matter which folder it is started from.
    # Ignored when DATABASE_URL (Postgres, e.g. on Render) is set - see database.py.
    DATABASE=os.environ.get("COMMUTESYNC_DB", os.path.join(BASE_DIR, "commutesync.db")),
)


# ---------------------------------------------------------------- database
def get_db():
    if "db" not in g:
        g.db = database.connect(app.config["DATABASE"])
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = database.connect(app.config["DATABASE"])
    conn.executescript(database.SCHEMA)
    # Migrate databases created before the safety-contact columns existed.
    existing = database.existing_columns(conn, "users")
    for col in ("emergency_name", "emergency_phone"):
        if col not in existing:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()


# Create tables on import, so `flask run`, gunicorn and `python app.py` all work.
init_db()


# ---------------------------------------------------------------- helpers
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def upcoming_clause(prefix="rides."):
    """SQL fragment + params that keep only rides that haven't departed yet."""
    now = datetime.now()
    sql = (f"({prefix}ride_date > ? OR ({prefix}ride_date = ? "
           f"AND {prefix}ride_time >= ?))")
    today = now.date().isoformat()
    return sql, [today, today, now.strftime("%H:%M")]


def safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


def valid_phone(phone):
    digits = phone.replace(" ", "").replace("-", "")
    if digits.startswith("+"):
        digits = digits[1:]
    return digits.isdigit() and 7 <= len(digits) <= 15


@app.context_processor
def inject_safety_context():
    """Makes the logged-in user's emergency contact and next ride available
    to every template, so the SOS button in the navbar always has what it
    needs without every view having to fetch it."""
    if "user_id" not in session:
        return {}

    db = get_db()
    user = db.execute("SELECT emergency_name, emergency_phone FROM users WHERE id = ?",
                      (session["user_id"],)).fetchone()

    upcoming_sql, params = upcoming_clause()
    next_ride = db.execute(f"""
        SELECT pickup, destination, ride_date, ride_time FROM rides
        WHERE user_id = ? AND {upcoming_sql}
        ORDER BY ride_date, ride_time LIMIT 1
    """, [session["user_id"], *params]).fetchone()

    if next_ride is None:
        next_ride = db.execute(f"""
            SELECT rides.pickup, rides.destination, rides.ride_date, rides.ride_time
            FROM bookings JOIN rides ON rides.id = bookings.ride_id
            WHERE bookings.user_id = ? AND {upcoming_sql}
            ORDER BY rides.ride_date, rides.ride_time LIMIT 1
        """, [session["user_id"], *params]).fetchone()

    return {
        "emergency_name": user["emergency_name"] if user else None,
        "emergency_phone": user["emergency_phone"] if user else None,
        "next_ride": next_ride,
    }


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or len(password) < 6:
            flash("Enter your name, email and a password of at least 6 characters.", "error")
            return render_template("register.html", form=request.form), 400

        try:
            db = get_db()
            db.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                       (name, email, generate_password_hash(password)))
            db.commit()
        except database.IntegrityError:
            flash("That email is already registered. Try logging in.", "error")
            return render_template("register.html", form=request.form), 400

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))

        flash("Invalid email or password.", "error")
        return render_template("login.html", email=email), 401

    return render_template("login.html", email="")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]

    my_rides = db.execute("""
        SELECT rides.*, COUNT(bookings.id) AS passengers
        FROM rides LEFT JOIN bookings ON bookings.ride_id = rides.id
        WHERE rides.user_id = ?
        GROUP BY rides.id
        ORDER BY rides.ride_date, rides.ride_time
    """, (uid,)).fetchall()

    my_bookings = db.execute("""
        SELECT bookings.id AS booking_id, rides.*, users.name AS driver
        FROM bookings
        JOIN rides ON rides.id = bookings.ride_id
        JOIN users ON users.id = rides.user_id
        WHERE bookings.user_id = ?
        ORDER BY rides.ride_date, rides.ride_time
    """, (uid,)).fetchall()

    return render_template("dashboard.html", name=session["user_name"],
                           rides=my_rides, bookings=my_bookings)


@app.route("/create-ride", methods=["GET", "POST"])
@login_required
def create_ride():
    if request.method == "POST":
        f = request.form
        pickup = f.get("pickup", "").strip()
        destination = f.get("destination", "").strip()
        ride_date = f.get("ride_date", "")
        ride_time = f.get("ride_time", "")

        errors = []
        if not pickup or not destination:
            errors.append("Pickup and destination are required.")
        elif pickup.lower() == destination.lower():
            errors.append("Pickup and destination must be different.")
        try:
            d = date.fromisoformat(ride_date)
            datetime.strptime(ride_time, "%H:%M")
            if datetime.combine(d, datetime.strptime(ride_time, "%H:%M").time()) < datetime.now():
                errors.append("The ride must be in the future.")
        except ValueError:
            errors.append("Enter a valid date and time.")
        try:
            seats = int(f.get("seats", ""))
            if not 1 <= seats <= 10:
                raise ValueError
        except ValueError:
            errors.append("Seats must be a whole number from 1 to 10.")
        try:
            fare = round(float(f.get("fare", "")), 2)
            if fare < 0:
                raise ValueError
        except ValueError:
            errors.append("Fare must be 0 or more.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("create_ride.html", form=f, today=date.today().isoformat()), 400

        db = get_db()
        db.execute("""INSERT INTO rides
                      (user_id, pickup, destination, ride_date, ride_time, seats, fare)
                      VALUES (?, ?, ?, ?, ?, ?, ?)""",
                   (session["user_id"], pickup, destination, ride_date, ride_time, seats, fare))
        db.commit()
        flash("Ride created.", "success")
        return redirect(url_for("dashboard"))

    return render_template("create_ride.html", form={}, today=date.today().isoformat())


@app.route("/search")
@login_required
def search_ride():
    pickup = request.args.get("pickup", "").strip()
    destination = request.args.get("destination", "").strip()
    ride_date = request.args.get("ride_date", "").strip()

    upcoming_sql, params = upcoming_clause()
    sql = f"""
        SELECT rides.*, users.name,
               EXISTS (SELECT 1 FROM bookings b
                       WHERE b.ride_id = rides.id AND b.user_id = ?) AS joined
        FROM rides JOIN users ON rides.user_id = users.id
        WHERE rides.user_id != ? AND {upcoming_sql}
    """
    params = [session["user_id"], session["user_id"], *params]
    if pickup:
        sql += " AND rides.pickup LIKE ?"
        params.append(f"%{pickup}%")
    if destination:
        sql += " AND rides.destination LIKE ?"
        params.append(f"%{destination}%")
    if ride_date:
        sql += " AND rides.ride_date = ?"
        params.append(ride_date)
    sql += " ORDER BY rides.ride_date, rides.ride_time"

    rides = get_db().execute(sql, params).fetchall()
    return render_template("search_ride.html", rides=rides, pickup=pickup,
                           destination=destination, ride_date=ride_date)


@app.post("/rides/<int:ride_id>/join")
@login_required
def join_ride(ride_id):
    db = get_db()
    uid = session["user_id"]
    ride = db.execute("SELECT * FROM rides WHERE id = ?", (ride_id,)).fetchone()

    if ride is None:
        flash("That ride no longer exists.", "error")
    elif ride["user_id"] == uid:
        flash("You can't join your own ride.", "error")
    elif db.execute("SELECT 1 FROM bookings WHERE ride_id = ? AND user_id = ?",
                    (ride_id, uid)).fetchone():
        flash("You've already joined this ride.", "error")
    else:
        # Decrement only if a seat is left - safe even with two people clicking at once.
        cur = db.execute("UPDATE rides SET seats = seats - 1 WHERE id = ? AND seats > 0",
                         (ride_id,))
        if cur.rowcount == 0:
            flash("Sorry, this ride is full.", "error")
        else:
            db.execute("INSERT INTO bookings (ride_id, user_id) VALUES (?, ?)", (ride_id, uid))
            db.commit()
            flash(f"You joined the ride {ride['pickup']} → {ride['destination']}.", "success")
            return redirect(url_for("dashboard"))

    return redirect(request.referrer or url_for("search_ride"))


@app.post("/bookings/<int:booking_id>/cancel")
@login_required
def cancel_booking(booking_id):
    db = get_db()
    booking = db.execute("SELECT * FROM bookings WHERE id = ? AND user_id = ?",
                         (booking_id, session["user_id"])).fetchone()
    if booking is None:
        flash("Booking not found.", "error")
    else:
        db.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
        db.execute("UPDATE rides SET seats = seats + 1 WHERE id = ?", (booking["ride_id"],))
        db.commit()
        flash("Booking cancelled.", "success")
    return redirect(url_for("dashboard"))


@app.post("/rides/<int:ride_id>/delete")
@login_required
def delete_ride(ride_id):
    db = get_db()
    cur = db.execute("DELETE FROM rides WHERE id = ? AND user_id = ?",
                     (ride_id, session["user_id"]))
    db.commit()
    flash("Ride deleted." if cur.rowcount else "Ride not found.",
          "success" if cur.rowcount else "error")
    return redirect(url_for("dashboard"))


@app.route("/safety", methods=["GET", "POST"])
@login_required
def safety():
    if request.method == "POST":
        name = request.form.get("emergency_name", "").strip()
        phone = request.form.get("emergency_phone", "").strip()

        if not name or not phone:
            flash("Enter both a contact name and phone number.", "error")
            return render_template("safety.html", emergency_name=name,
                                   emergency_phone_form=phone), 400
        if not valid_phone(phone):
            flash("Enter a valid phone number, digits only (7-15), optionally starting with +.", "error")
            return render_template("safety.html", emergency_name=name,
                                   emergency_phone_form=phone), 400

        get_db().execute("UPDATE users SET emergency_name = ?, emergency_phone = ? WHERE id = ?",
                         (name, phone, session["user_id"]))
        get_db().commit()
        flash("Emergency contact saved. The SOS button is ready to use.", "success")
        return redirect(url_for("safety"))

    if request.args.get("need_contact"):
        flash("Add an emergency contact below so the SOS button has someone to message.", "error")

    row = get_db().execute("SELECT emergency_name, emergency_phone FROM users WHERE id = ?",
                           (session["user_id"],)).fetchone()
    return render_template("safety.html", emergency_name=row["emergency_name"] or "",
                           emergency_phone_form=row["emergency_phone"] or "")


@app.errorhandler(404)
def not_found(_e):
    return render_template("message.html", title="Page not found",
                           message="The page you're looking for doesn't exist."), 404


if __name__ == "__main__":
    app.run(debug=True)
