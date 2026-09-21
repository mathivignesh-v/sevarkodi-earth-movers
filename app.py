from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from functools import wraps
import sqlite3
try:
    import psycopg2
except ImportError:
    psycopg2 = None
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "earthmovers.db"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "local-development-secret-change-for-production")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

class CompatRow(dict):
    """Dict-like row that also supports row[0] for compatibility with SQLite code."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class CompatCursor:
    def __init__(self, cursor, is_pg=False):
        self.cursor = cursor
        self.is_pg = is_pg
    def _row(self, row):
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return row
        if isinstance(row, dict):
            return CompatRow(row)
        cols = [d[0] for d in self.cursor.description] if self.cursor.description else []
        return CompatRow(zip(cols, row))
    def fetchone(self):
        return self._row(self.cursor.fetchone())
    def fetchall(self):
        return [self._row(r) for r in self.cursor.fetchall()]
    def __iter__(self):
        return iter(self.fetchall())
    @property
    def rowcount(self):
        return self.cursor.rowcount
    @property
    def lastrowid(self):
        return getattr(self.cursor, "lastrowid", None)

class CompatConnection:
    def __init__(self, raw, is_pg=False):
        self.raw = raw
        self.is_pg = is_pg
    @property
    def row_factory(self):
        return getattr(self.raw, "row_factory", None)
    @row_factory.setter
    def row_factory(self, value):
        if not self.is_pg:
            self.raw.row_factory = value
    def _sql(self, sql):
        return sql.replace("?", "%s") if self.is_pg else sql
    def execute(self, sql, args=()):
        if self.is_pg:
            cur = self.raw.cursor()
            cur.execute(self._sql(sql), args)
            return CompatCursor(cur, True)
        return CompatCursor(self.raw.execute(sql, args), False)
    def executemany(self, sql, seq):
        if self.is_pg:
            cur = self.raw.cursor()
            cur.executemany(self._sql(sql), seq)
            return CompatCursor(cur, True)
        return CompatCursor(self.raw.executemany(sql, seq), False)
    def executescript(self, script):
        if self.is_pg:
            cur = self.raw.cursor()
            for statement in script.split(';'):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)
            return CompatCursor(cur, True)
        return CompatCursor(self.raw.executescript(script), False)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()

def get_connection():
    """Use PostgreSQL online when DATABASE_URL is set; otherwise use local SQLite."""
    if DATABASE_URL:
        if psycopg2 is None:
            raise RuntimeError("PostgreSQL is configured but psycopg2-binary is not installed.")
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return CompatConnection(psycopg2.connect(url), True)
    conn = sqlite3.connect(DB_PATH)
    return CompatConnection(conn, False)

def db():
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    if DATABASE_URL:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS drivers (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            id SERIAL PRIMARY KEY,
            number TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Idle'
        );
        CREATE TABLE IF NOT EXISTS work_entries (
            id SERIAL PRIMARY KEY,
            driver_id INTEGER NOT NULL REFERENCES drivers(id),
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
            work_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            hours DOUBLE PRECISION NOT NULL,
            amount DOUBLE PRECISION NOT NULL
        );
        """)
    else:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Idle'
        );
        CREATE TABLE IF NOT EXISTS work_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_id INTEGER NOT NULL,
            vehicle_id INTEGER NOT NULL,
            work_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            hours REAL NOT NULL,
            amount REAL NOT NULL,
            FOREIGN KEY(driver_id) REFERENCES drivers(id),
            FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
        );
        """)

    if conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO drivers(name, phone, status) VALUES(?,?,?)",
            [("Kumar", "+91 98765 43210", "Active"), ("Ravi", "+91 91234 56789", "Active"),
             ("Mani", "+91 87654 32109", "Active"), ("Suresh", "+91 99887 65432", "Inactive"),
             ("Vijay", "+91 90897 65432", "Active"), ("Arun", "+91 81909 87654", "Active")])
    if conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO vehicles(number,type,status) VALUES(?,?,?)",
            [("TN 33 AB 1234", "JCB 3DX", "Working"), ("TN 33 CD 5678", "Excavator", "Working"),
             ("TN 33 EF 9101", "Tipper", "Idle"), ("TN 33 GH 1122", "JCB 4DX", "Working"),
             ("TN 33 IJ 3344", "Roller", "Idle")])
    if conn.execute("SELECT COUNT(*) FROM work_entries").fetchone()[0] == 0:
        drivers = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM drivers")}
        vehicles = {r["number"]: r["id"] for r in conn.execute("SELECT id,number FROM vehicles")}
        demo=[("Kumar","TN 33 AB 1234","2026-08-21","08:10","11:45",3.5833,2675),
              ("Kumar","TN 33 CD 5678","2026-08-21","12:15","15:45",3.5,2625),
              ("Kumar","TN 33 AB 1234","2026-08-21","16:15","19:45",3.5,2625),
              ("Kumar","TN 33 AB 1234","2026-08-17","08:10","17:30",9.3333,6950),
              ("Kumar","TN 33 AB 1234","2026-08-18","08:00","16:30",8.5,6375),
              ("Kumar","TN 33 AB 1234","2026-08-19","08:10","18:20",10.1667,7425),
              ("Kumar","TN 33 AB 1234","2026-08-20","08:10","16:40",8.5,6125),
              ("Kumar","TN 33 AB 1234","2026-08-22","08:00","16:00",8,5800)]
        conn.executemany("""INSERT INTO work_entries
            (driver_id,vehicle_id,work_date,start_time,end_time,hours,amount)
            VALUES(?,?,?,?,?,?,?)""", [(drivers[d],vehicles[v],dt,st,et,h,a) for d,v,dt,st,et,h,a in demo])
    conn.commit(); conn.close()

def query_one(sql, args=()):
    conn=db()
    try: return conn.execute(sql,args).fetchone()
    finally: conn.close()

def query_all(sql, args=()):
    conn=db()
    try: return conn.execute(sql,args).fetchall()
    finally: conn.close()

@app.template_filter("inr")
def inr(value):
    return "₹{:,.0f}".format(float(value or 0))


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin_user = os.environ.get("ADMIN_USERNAME", "admin")
        admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")
        if username == admin_user and password == admin_password:
            session["admin_logged_in"] = True
            session["admin_username"] = username
            return redirect(request.args.get("next") or url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    today = datetime.now().strftime("%Y-%m-%d")
    totals = query_one("""
        SELECT COALESCE(SUM(hours),0) hours, COALESCE(SUM(amount),0) amount
        FROM work_entries WHERE work_date=?
    """, (today,))
    overall = query_one("""
        SELECT COALESCE(SUM(hours),0) hours, COALESCE(SUM(amount),0) amount, COUNT(*) entries
        FROM work_entries
    """)
    driver_count = query_one("SELECT COUNT(*) c FROM drivers")["c"]
    active_drivers = query_one("SELECT COUNT(*) c FROM drivers WHERE status='Active'")["c"]
    vehicle_count = query_one("SELECT COUNT(*) c FROM vehicles")["c"]
    working = query_one("SELECT COUNT(*) c FROM vehicles WHERE status='Working'")["c"]
    idle = query_one("SELECT COUNT(*) c FROM vehicles WHERE status='Idle'")["c"]
    recent = query_all("""
        SELECT w.id, w.work_date, w.start_time, w.end_time, w.hours, w.amount,
               d.name driver_name, v.number vehicle_number, v.type vehicle_type
        FROM work_entries w
        JOIN drivers d ON d.id=w.driver_id
        JOIN vehicles v ON v.id=w.vehicle_id
        ORDER BY w.work_date DESC, w.id DESC LIMIT 8
    """)
    return render_template(
        "dashboard.html", totals=totals, overall=overall, recent=recent,
        driver_count=driver_count, active_drivers=active_drivers,
        vehicle_count=vehicle_count, working=working, idle=idle, today=today
    )

@app.route("/drivers")
@login_required
def drivers():
    rows = query_all("""
        SELECT d.*, COALESCE(SUM(w.hours),0) total_hours,
               COALESCE(SUM(w.amount),0) total_amount
        FROM drivers d LEFT JOIN work_entries w ON d.id=w.driver_id
        GROUP BY d.id ORDER BY d.id
    """)
    return render_template("drivers.html", drivers=rows)

@app.route("/vehicles")
@login_required
def vehicles():
    rows = query_all("SELECT * FROM vehicles ORDER BY id")
    return render_template("vehicles.html", vehicles=rows)

@app.route("/driver/<int:driver_id>")
@login_required
def driver_detail(driver_id):
    driver = query_one("SELECT * FROM drivers WHERE id=?", (driver_id,))
    if not driver:
        return redirect(url_for("drivers"))
    period = request.args.get("period", "week")
    entries = query_all("""
        SELECT w.*, v.number vehicle_number, v.type vehicle_type
        FROM work_entries w JOIN vehicles v ON v.id=w.vehicle_id
        WHERE w.driver_id=? ORDER BY w.work_date DESC, w.start_time DESC
    """, (driver_id,))
    total_hours = sum(float(e["hours"]) for e in entries)
    total_amount = sum(float(e["amount"]) for e in entries)
    return render_template("driver_detail.html", driver=driver, entries=entries,
                           total_hours=total_hours, total_amount=total_amount, period=period)

@app.route("/reports")
@login_required
def reports():
    selected = request.args.get("driver", "")
    drivers = query_all("SELECT * FROM drivers ORDER BY name")
    if selected:
        entries = query_all("""
            SELECT w.*, d.name driver_name, v.number vehicle_number
            FROM work_entries w
            JOIN drivers d ON d.id=w.driver_id
            JOIN vehicles v ON v.id=w.vehicle_id
            WHERE d.id=? ORDER BY w.work_date DESC, w.start_time DESC
        """, (selected,))
    else:
        entries = query_all("""
            SELECT w.*, d.name driver_name, v.number vehicle_number
            FROM work_entries w
            JOIN drivers d ON d.id=w.driver_id
            JOIN vehicles v ON v.id=w.vehicle_id
            ORDER BY w.work_date DESC, w.start_time DESC
        """)
    return render_template("reports.html", drivers=drivers, entries=entries, selected=selected)

@app.route("/add-driver", methods=["GET", "POST"])
@login_required
def add_driver():
    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        status = request.form.get("status", "Active")
        conn = db()
        conn.execute("INSERT INTO drivers(name,phone,status) VALUES(?,?,?)", (name, phone, status))
        conn.commit(); conn.close()
        flash("Driver added successfully.")
        return redirect(url_for("drivers"))
    return render_template("form.html", title="Add Driver", kind="driver")

@app.route("/add-vehicle", methods=["GET", "POST"])
@login_required
def add_vehicle():
    if request.method == "POST":
        number = request.form["number"].strip().upper()
        vtype = request.form["type"].strip()
        status = request.form.get("status", "Idle")
        conn = db()
        conn.execute("INSERT INTO vehicles(number,type,status) VALUES(?,?,?)", (number, vtype, status))
        conn.commit(); conn.close()
        flash("Vehicle added successfully.")
        return redirect(url_for("vehicles"))
    return render_template("form.html", title="Add Vehicle", kind="vehicle")

@app.route("/add-work", methods=["GET", "POST"])
@login_required
def add_work():
    drivers = query_all("SELECT * FROM drivers ORDER BY name")
    vehicles = query_all("SELECT * FROM vehicles ORDER BY number")
    if request.method == "POST":
        driver_id = request.form["driver_id"]
        vehicle_id = request.form["vehicle_id"]
        work_date = request.form["work_date"]
        start = request.form["start_time"]
        end = request.form["end_time"]
        amount = float(request.form["amount"])
        start_dt = datetime.strptime(start, "%H:%M")
        end_dt = datetime.strptime(end, "%H:%M")
        seconds = (end_dt - start_dt).total_seconds()
        if seconds < 0:
            seconds += 24 * 3600
        hours = seconds / 3600
        conn = db()
        conn.execute("""INSERT INTO work_entries
            (driver_id,vehicle_id,work_date,start_time,end_time,hours,amount)
            VALUES(?,?,?,?,?,?,?)""",
            (driver_id, vehicle_id, work_date, start, end, hours, amount))
        conn.commit(); conn.close()
        flash("Work entry saved successfully.")
        return redirect(url_for("driver_detail", driver_id=driver_id))
    return render_template("work_form.html", drivers=drivers, vehicles=vehicles)


@app.route("/bill/<int:entry_id>")
@login_required
def bill(entry_id):
    entry = query_one("""
        SELECT w.*, d.name driver_name, d.phone driver_phone,
               v.number vehicle_number, v.type vehicle_type
        FROM work_entries w
        JOIN drivers d ON d.id=w.driver_id
        JOIN vehicles v ON v.id=w.vehicle_id
        WHERE w.id=?
    """, (entry_id,))
    if not entry:
        return "Bill not found", 404
    return render_template("bill.html", entry=entry)


@app.post("/delete-driver/<int:driver_id>")
@login_required
def delete_driver(driver_id):
    conn = db()
    try:
        driver = conn.execute("SELECT * FROM drivers WHERE id=?", (driver_id,)).fetchone()
        if not driver:
            flash("Driver not found.")
        else:
            work_count = conn.execute(
                "SELECT COUNT(*) FROM work_entries WHERE driver_id=?", (driver_id,)
            ).fetchone()[0]
            if work_count:
                flash("Cannot delete this driver because work records exist. Delete the work records first.")
            else:
                conn.execute("DELETE FROM drivers WHERE id=?", (driver_id,))
                conn.commit()
                flash("Driver deleted successfully.")
    finally:
        conn.close()
    return redirect(url_for("drivers"))


@app.post("/delete-vehicle/<int:vehicle_id>")
@login_required
def delete_vehicle(vehicle_id):
    conn = db()
    try:
        vehicle = conn.execute("SELECT * FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
        if not vehicle:
            flash("Vehicle not found.")
        else:
            work_count = conn.execute(
                "SELECT COUNT(*) FROM work_entries WHERE vehicle_id=?", (vehicle_id,)
            ).fetchone()[0]
            if work_count:
                flash("Cannot delete this vehicle because work records exist. Delete the work records first.")
            else:
                conn.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
                conn.commit()
                flash("Vehicle deleted successfully.")
    finally:
        conn.close()
    return redirect(url_for("vehicles"))


@app.post("/delete-work/<int:entry_id>")
@login_required
def delete_work(entry_id):
    conn = db()
    try:
        entry = conn.execute("SELECT driver_id FROM work_entries WHERE id=?", (entry_id,)).fetchone()
        if not entry:
            flash("Work record not found.")
            return redirect(url_for("reports"))
        driver_id = entry["driver_id"]
        conn.execute("DELETE FROM work_entries WHERE id=?", (entry_id,))
        conn.commit()
        flash("Work record deleted successfully.")
        return redirect(url_for("driver_detail", driver_id=driver_id))
    finally:
        conn.close()


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
