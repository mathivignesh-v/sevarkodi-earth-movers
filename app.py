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
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class CompatCursor:
    def __init__(self, cursor, is_pg=False):
        self.cursor, self.is_pg = cursor, is_pg
    def _row(self, row):
        if row is None: return None
        if isinstance(row, sqlite3.Row): return row
        if isinstance(row, dict): return CompatRow(row)
        cols = [d[0] for d in self.cursor.description] if self.cursor.description else []
        return CompatRow(zip(cols, row))
    def fetchone(self): return self._row(self.cursor.fetchone())
    def fetchall(self): return [self._row(r) for r in self.cursor.fetchall()]
    def __iter__(self): return iter(self.fetchall())
    @property
    def rowcount(self): return self.cursor.rowcount
    @property
    def lastrowid(self): return getattr(self.cursor, "lastrowid", None)

class CompatConnection:
    def __init__(self, raw, is_pg=False): self.raw, self.is_pg = raw, is_pg
    @property
    def row_factory(self): return getattr(self.raw, "row_factory", None)
    @row_factory.setter
    def row_factory(self, value):
        if not self.is_pg: self.raw.row_factory = value
    def _sql(self, sql): return sql.replace("?", "%s") if self.is_pg else sql
    def execute(self, sql, args=()):
        if self.is_pg:
            cur = self.raw.cursor(); cur.execute(self._sql(sql), args); return CompatCursor(cur, True)
        return CompatCursor(self.raw.execute(sql, args), False)
    def executemany(self, sql, seq):
        if self.is_pg:
            cur = self.raw.cursor(); cur.executemany(self._sql(sql), seq); return CompatCursor(cur, True)
        return CompatCursor(self.raw.executemany(sql, seq), False)
    def executescript(self, script):
        if self.is_pg:
            cur = self.raw.cursor()
            for statement in script.split(';'):
                statement = statement.strip()
                if statement: cur.execute(statement)
            return CompatCursor(cur, True)
        return CompatCursor(self.raw.executescript(script), False)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()

def get_connection():
    if DATABASE_URL:
        if psycopg2 is None:
            raise RuntimeError("PostgreSQL is configured but psycopg2-binary is not installed.")
        url = DATABASE_URL
        if url.startswith("postgres://"): url = "postgresql://" + url[len("postgres://"):]
        return CompatConnection(psycopg2.connect(url), True)
    conn = sqlite3.connect(DB_PATH)
    return CompatConnection(conn, False)

def db():
    conn = get_connection(); conn.row_factory = sqlite3.Row; return conn

def _add_column(conn, table, column, definition):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception as exc:
        # Column already exists is safe to ignore; rollback only the failed DDL transaction on PostgreSQL.
        try: conn.rollback()
        except Exception: pass

def init_db():
    conn = db()
    if DATABASE_URL:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS drivers (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            id SERIAL PRIMARY KEY, number TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Idle'
        );
        CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '',
            address TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS rates (
            id SERIAL PRIMARY KEY, vehicle_type TEXT NOT NULL UNIQUE,
            rate_per_hour DOUBLE PRECISION NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS work_entries (
            id SERIAL PRIMARY KEY, driver_id INTEGER NOT NULL REFERENCES drivers(id),
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
            work_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
            hours DOUBLE PRECISION NOT NULL, rate_per_hour DOUBLE PRECISION NOT NULL DEFAULT 0,
            amount DOUBLE PRECISION NOT NULL, customer_id INTEGER REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
            payment_date TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL,
            note TEXT DEFAULT ''
        );
        """)
    else:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Idle'
        );
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT DEFAULT '',
            address TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_type TEXT NOT NULL UNIQUE,
            rate_per_hour REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS work_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, driver_id INTEGER NOT NULL,
            vehicle_id INTEGER NOT NULL, work_date TEXT NOT NULL, start_time TEXT NOT NULL,
            end_time TEXT NOT NULL, hours REAL NOT NULL, rate_per_hour REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL, customer_id INTEGER,
            FOREIGN KEY(driver_id) REFERENCES drivers(id), FOREIGN KEY(vehicle_id) REFERENCES vehicles(id),
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL, amount REAL NOT NULL, note TEXT DEFAULT '',
            FOREIGN KEY(customer_id) REFERENCES customers(id)
        );
        """)

    # Safe migration for the database already running on Render/local machines.
    _add_column(conn, "work_entries", "rate_per_hour", "DOUBLE PRECISION DEFAULT 0" if DATABASE_URL else "REAL DEFAULT 0")
    _add_column(conn, "work_entries", "customer_id", "INTEGER")

    if conn.execute("SELECT COUNT(*) FROM drivers").fetchone()[0] == 0:
        conn.executemany("INSERT INTO drivers(name,phone,status) VALUES(?,?,?)", [
            ("Kumar", "+91 98765 43210", "Active"), ("Ravi", "+91 91234 56789", "Active"),
            ("Mani", "+91 87654 32109", "Active"), ("Suresh", "+91 99887 65432", "Inactive"),
            ("Vijay", "+91 90897 65432", "Active"), ("Arun", "+91 81909 87654", "Active")])
    if conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 0:
        conn.executemany("INSERT INTO vehicles(number,type,status) VALUES(?,?,?)", [
            ("TN 33 AB 1234", "JCB 3DX", "Working"), ("TN 33 CD 5678", "Excavator", "Working"),
            ("TN 33 EF 9101", "Tipper", "Idle"), ("TN 33 GH 1122", "JCB 4DX", "Working"),
            ("TN 33 IJ 3344", "Tractor", "Idle")])
    if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
        conn.executemany("INSERT INTO customers(name,phone,address,status) VALUES(?,?,?,?)", [
            ("ABC Constructions", "+91 90000 00001", "Erode", "Active"),
            ("Demo Customer", "+91 90000 00002", "Erode", "Active")])
    if conn.execute("SELECT COUNT(*) FROM rates").fetchone()[0] == 0:
        conn.executemany("INSERT INTO rates(vehicle_type,rate_per_hour) VALUES(?,?)", [
            ("JCB 3DX", 1000), ("JCB 4DX", 1100), ("Tipper", 1200),
            ("Tractor", 900), ("Excavator", 1200)])

    # Give legacy work records a usable rate without changing their old billed amount.
    legacy = conn.execute("SELECT id, vehicle_id, hours, amount, rate_per_hour FROM work_entries WHERE COALESCE(rate_per_hour,0)=0").fetchall()
    for e in legacy:
        v = conn.execute("SELECT type FROM vehicles WHERE id=?", (e["vehicle_id"],)).fetchone()
        rate = conn.execute("SELECT rate_per_hour FROM rates WHERE vehicle_type=?", (v["type"],)).fetchone() if v else None
        if rate and float(e["hours"] or 0):
            conn.execute("UPDATE work_entries SET rate_per_hour=? WHERE id=?", (float(rate["rate_per_hour"]), e["id"]))

    conn.commit(); conn.close()

def query_one(sql, args=()):
    conn=db()
    try: return conn.execute(sql,args).fetchone()
    finally: conn.close()

def query_all(sql, args=()):
    conn=db()
    try: return conn.execute(sql,args).fetchall()
    finally: conn.close()

def customer_financials(customer_id):
    """Return customer billing total, payments and current balance."""
    total_row = query_one(
        "SELECT COALESCE(SUM(amount),0) total FROM work_entries WHERE customer_id=?",
        (customer_id,)
    )
    paid_row = query_one(
        "SELECT COALESCE(SUM(amount),0) total FROM payments WHERE customer_id=?",
        (customer_id,)
    )
    total = float(total_row["total"] or 0)
    paid = float(paid_row["total"] or 0)
    return {
        "total": total,
        "paid": paid,
        "balance": round(total - paid, 2)
    }

@app.template_filter("inr")
def inr(value): return "₹{:,.0f}".format(float(value or 0))

@app.template_filter("hours_fmt")
def hours_fmt(value): return f"{float(value or 0):.2f}"

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped_view

@app.route("/login", methods=["GET","POST"])
def login():
    if session.get("admin_logged_in"): return redirect(url_for("dashboard"))
    error=None
    if request.method=="POST":
        username=request.form.get("username","").strip(); password=request.form.get("password","")
        if username==os.environ.get("ADMIN_USERNAME","admin") and password==os.environ.get("ADMIN_PASSWORD","admin123"):
            session["admin_logged_in"]=True; session["admin_username"]=username
            return redirect(request.args.get("next") or url_for("dashboard"))
        error="Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    today=datetime.now().strftime("%Y-%m-%d")
    totals=query_one("SELECT COALESCE(SUM(hours),0) hours, COALESCE(SUM(amount),0) amount FROM work_entries WHERE work_date=?",(today,))
    overall=query_one("SELECT COALESCE(SUM(hours),0) hours, COALESCE(SUM(amount),0) amount, COUNT(*) entries FROM work_entries")
    driver_count=query_one("SELECT COUNT(*) c FROM drivers")["c"]; active_drivers=query_one("SELECT COUNT(*) c FROM drivers WHERE status='Active'")["c"]
    vehicle_count=query_one("SELECT COUNT(*) c FROM vehicles")["c"]; working=query_one("SELECT COUNT(*) c FROM vehicles WHERE status='Working'")["c"]; idle=query_one("SELECT COUNT(*) c FROM vehicles WHERE status='Idle'")["c"]
    recent=query_all("""SELECT w.id,w.work_date,w.start_time,w.end_time,w.hours,w.rate_per_hour,w.amount,
        d.name driver_name,v.number vehicle_number,v.type vehicle_type,COALESCE(c.name,'—') customer_name
        FROM work_entries w JOIN drivers d ON d.id=w.driver_id JOIN vehicles v ON v.id=w.vehicle_id
        LEFT JOIN customers c ON c.id=w.customer_id ORDER BY w.work_date DESC,w.id DESC LIMIT 10""")
    return render_template("dashboard.html", totals=totals, overall=overall, recent=recent, driver_count=driver_count,
        active_drivers=active_drivers, vehicle_count=vehicle_count, working=working, idle=idle, today=today)

@app.route("/customers")
@login_required
def customers():
    rows = query_all("SELECT * FROM customers ORDER BY name")
    customers_data = []
    for c in rows:
        item = dict(c)
        item.update(customer_financials(c["id"]))
        customers_data.append(item)
    return render_template("customers.html", customers=customers_data)

@app.route("/add-customer", methods=["GET","POST"])
@login_required
def add_customer():
    if request.method=="POST":
        conn=db(); conn.execute("INSERT INTO customers(name,phone,address,status) VALUES(?,?,?,?)",(
            request.form["name"].strip(),request.form.get("phone","").strip(),request.form.get("address","").strip(),request.form.get("status","Active"))); conn.commit(); conn.close()
        flash("Customer added successfully."); return redirect(url_for("customers"))
    return render_template("customer_form.html", title="Add Customer", customer=None)

@app.route("/edit-customer/<int:customer_id>", methods=["GET","POST"])
@login_required
def edit_customer(customer_id):
    customer=query_one("SELECT * FROM customers WHERE id=?",(customer_id,))
    if not customer: return redirect(url_for("customers"))
    if request.method=="POST":
        conn=db(); conn.execute("UPDATE customers SET name=?,phone=?,address=?,status=? WHERE id=?",(
            request.form["name"].strip(),request.form.get("phone","").strip(),request.form.get("address","").strip(),request.form.get("status","Active"),customer_id)); conn.commit(); conn.close()
        flash("Customer updated successfully."); return redirect(url_for("customers"))
    return render_template("customer_form.html", title="Edit Customer", customer=customer)

@app.route("/customer/<int:customer_id>")
@login_required
def customer_detail(customer_id):
    customer = query_one("SELECT * FROM customers WHERE id=?", (customer_id,))
    if not customer:
        flash("Customer not found.")
        return redirect(url_for("customers"))

    entries = query_all("""SELECT w.*, d.name driver_name, v.number vehicle_number,
        v.type vehicle_type
        FROM work_entries w
        JOIN drivers d ON d.id=w.driver_id
        JOIN vehicles v ON v.id=w.vehicle_id
        WHERE w.customer_id=?
        ORDER BY w.work_date DESC, w.id DESC""", (customer_id,))
    payments = query_all(
        "SELECT * FROM payments WHERE customer_id=? ORDER BY payment_date DESC, id DESC",
        (customer_id,)
    )
    financials = customer_financials(customer_id)
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template(
        "customer_detail.html",
        customer=customer,
        entries=entries,
        payments=payments,
        today=today,
        **financials
    )

@app.route("/customer/<int:customer_id>/statement")
@login_required
def customer_statement(customer_id):
    customer = query_one("SELECT * FROM customers WHERE id=?", (customer_id,))
    if not customer:
        return "Customer not found", 404

    entries = query_all("""SELECT w.*, d.name driver_name, v.number vehicle_number,
        v.type vehicle_type
        FROM work_entries w
        JOIN drivers d ON d.id=w.driver_id
        JOIN vehicles v ON v.id=w.vehicle_id
        WHERE w.customer_id=?
        ORDER BY w.work_date ASC, w.id ASC""", (customer_id,))
    payments = query_all(
        "SELECT * FROM payments WHERE customer_id=? ORDER BY payment_date ASC, id ASC",
        (customer_id,)
    )
    financials = customer_financials(customer_id)
    return render_template(
        "customer_statement.html",
        customer=customer,
        entries=entries,
        payments=payments,
        **financials
    )

@app.route("/add-payment", methods=["POST"])
@login_required
def add_payment():
    customer_id = request.form.get("customer_id")
    payment_date = request.form.get("payment_date") or datetime.now().strftime("%Y-%m-%d")
    note = request.form.get("note", "").strip()
    try:
        amount = float(request.form.get("amount") or 0)
    except ValueError:
        amount = 0
    if not customer_id or amount <= 0:
        flash("Enter a valid customer and payment amount.")
        return redirect(url_for("reports"))

    financials = customer_financials(customer_id)
    if amount > financials["balance"] + 0.01:
        flash("Payment cannot be greater than the customer's current pending balance.")
        return redirect(url_for("customer_detail", customer_id=customer_id))

    conn = db()
    conn.execute(
        "INSERT INTO payments(customer_id,payment_date,amount,note) VALUES(?,?,?,?)",
        (customer_id, payment_date, round(amount, 2), note)
    )
    conn.commit(); conn.close()
    flash("Payment recorded successfully. Customer balance was updated.")
    return redirect(url_for("customer_detail", customer_id=customer_id))

@app.route("/edit-payment/<int:payment_id>", methods=["GET","POST"])
@login_required
def edit_payment(payment_id):
    payment = query_one("SELECT * FROM payments WHERE id=?", (payment_id,))
    if not payment:
        flash("Payment not found.")
        return redirect(url_for("reports"))

    if request.method == "POST":
        payment_date = request.form.get("payment_date") or payment["payment_date"]
        note = request.form.get("note", "").strip()
        try:
            amount = float(request.form.get("amount") or 0)
        except ValueError:
            amount = 0

        if amount <= 0:
            flash("Payment amount must be greater than zero.")
            return redirect(url_for("edit_payment", payment_id=payment_id))

        financials = customer_financials(payment["customer_id"])
        available = financials["balance"] + float(payment["amount"] or 0)
        if amount > available + 0.01:
            flash("Payment cannot be greater than the customer's pending balance.")
            return redirect(url_for("edit_payment", payment_id=payment_id))

        conn = db()
        conn.execute(
            "UPDATE payments SET payment_date=?, amount=?, note=? WHERE id=?",
            (payment_date, round(amount, 2), note, payment_id)
        )
        conn.commit(); conn.close()
        flash("Payment updated successfully.")
        return redirect(url_for("customer_detail", customer_id=payment["customer_id"]))

    return render_template("payment_form.html", title="Edit Payment", payment=payment)

@app.post("/delete-payment/<int:payment_id>")
@login_required
def delete_payment(payment_id):
    payment = query_one("SELECT * FROM payments WHERE id=?", (payment_id,))
    if not payment:
        flash("Payment not found.")
        return redirect(url_for("reports"))

    conn = db()
    conn.execute("DELETE FROM payments WHERE id=?", (payment_id,))
    conn.commit(); conn.close()
    flash("Payment deleted. Customer balance was recalculated.")
    return redirect(url_for("customer_detail", customer_id=payment["customer_id"]))

@app.route("/drivers")
@login_required
def drivers():
    rows=query_all("""SELECT d.*,COALESCE(SUM(w.hours),0) total_hours,COALESCE(SUM(w.amount),0) total_amount
        FROM drivers d LEFT JOIN work_entries w ON d.id=w.driver_id GROUP BY d.id ORDER BY d.id""")
    return render_template("drivers.html", drivers=rows)

@app.route("/vehicles")
@login_required
def vehicles(): return render_template("vehicles.html", vehicles=query_all("SELECT * FROM vehicles ORDER BY id"))

@app.route("/rates", methods=["GET","POST"])
@login_required
def rates():
    if request.method=="POST":
        vehicle_type=request.form["vehicle_type"].strip(); rate=float(request.form["rate_per_hour"] or 0)
        conn=db()
        existing=conn.execute("SELECT id FROM rates WHERE vehicle_type=?",(vehicle_type,)).fetchone()
        if existing: conn.execute("UPDATE rates SET rate_per_hour=? WHERE vehicle_type=?",(rate,vehicle_type))
        else: conn.execute("INSERT INTO rates(vehicle_type,rate_per_hour) VALUES(?,?)",(vehicle_type,rate))
        # Market-rate changes immediately flow to all matching saved work and bills.
        conn.execute("""UPDATE work_entries SET rate_per_hour=?, amount=hours*? WHERE vehicle_id IN
            (SELECT id FROM vehicles WHERE type=?)""",(rate,rate,vehicle_type))
        conn.commit(); conn.close(); flash(f"Rate updated for {vehicle_type}. Existing entries and bills were recalculated.")
        return redirect(url_for("rates"))
    return render_template("rates.html", rates=query_all("SELECT * FROM rates ORDER BY vehicle_type"), vehicle_types=query_all("SELECT DISTINCT type FROM vehicles ORDER BY type"))

@app.route("/driver/<int:driver_id>")
@login_required
def driver_detail(driver_id):
    driver=query_one("SELECT * FROM drivers WHERE id=?",(driver_id,))
    if not driver: return redirect(url_for("drivers"))
    entries=query_all("""SELECT w.*,v.number vehicle_number,v.type vehicle_type,COALESCE(c.name,'—') customer_name
        FROM work_entries w JOIN vehicles v ON v.id=w.vehicle_id LEFT JOIN customers c ON c.id=w.customer_id
        WHERE w.driver_id=? ORDER BY w.work_date DESC,w.start_time DESC""",(driver_id,))
    return render_template("driver_detail.html",driver=driver,entries=entries,total_hours=sum(float(e["hours"]) for e in entries),total_amount=sum(float(e["amount"]) for e in entries))

@app.route("/reports")
@login_required
def reports():
    selected_driver = request.args.get("driver","")
    selected_customer = request.args.get("customer","")
    drivers = query_all("SELECT * FROM drivers ORDER BY name")
    customers = query_all("SELECT * FROM customers ORDER BY name")

    base="""SELECT w.*,d.name driver_name,v.number vehicle_number,v.type vehicle_type,
        COALESCE(c.name,'—') customer_name
        FROM work_entries w
        JOIN drivers d ON d.id=w.driver_id
        JOIN vehicles v ON v.id=w.vehicle_id
        LEFT JOIN customers c ON c.id=w.customer_id"""

    conditions=[]; args=[]
    if selected_driver:
        conditions.append("d.id=?"); args.append(selected_driver)
    if selected_customer:
        conditions.append("c.id=?"); args.append(selected_customer)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    entries = query_all(base + where + " ORDER BY w.work_date DESC,w.start_time DESC", tuple(args))

    payments = query_all("""SELECT p.*, c.name customer_name
        FROM payments p JOIN customers c ON c.id=p.customer_id
        ORDER BY p.payment_date DESC, p.id DESC""")
    payment_customers = customers
    return render_template(
        "reports.html",
        drivers=drivers, customers=customers, entries=entries, payments=payments,
        payment_customers=payment_customers,
        selected=selected_driver, selected_customer=selected_customer
    )

@app.route("/add-driver", methods=["GET","POST"])
@login_required
def add_driver():
    if request.method=="POST":
        conn=db(); conn.execute("INSERT INTO drivers(name,phone,status) VALUES(?,?,?)",(request.form["name"].strip(),request.form["phone"].strip(),request.form.get("status","Active"))); conn.commit(); conn.close(); flash("Operator added successfully."); return redirect(url_for("drivers"))
    return render_template("form.html",title="Add Operator",kind="driver")

@app.route("/add-vehicle", methods=["GET","POST"])
@login_required
def add_vehicle():
    if request.method=="POST":
        number=request.form["number"].strip().upper(); vtype=request.form["type"].strip(); status=request.form.get("status","Idle")
        conn=db(); conn.execute("INSERT INTO vehicles(number,type,status) VALUES(?,?,?)",(number,vtype,status));
        if not conn.execute("SELECT id FROM rates WHERE vehicle_type=?",(vtype,)).fetchone(): conn.execute("INSERT INTO rates(vehicle_type,rate_per_hour) VALUES(?,?)",(vtype,float(request.form.get("rate_per_hour") or 0)))
        conn.commit(); conn.close(); flash("Vehicle added successfully."); return redirect(url_for("vehicles"))
    return render_template("form.html",title="Add Vehicle",kind="vehicle")

def calculate_hours(start,end):
    if not start or not end: return 0
    try:
        s=datetime.strptime(start,"%H:%M"); e=datetime.strptime(end,"%H:%M"); seconds=(e-s).total_seconds()
        if seconds<0: seconds += 24*3600
        return round(seconds/3600,2)
    except ValueError: return 0

def save_work(form, entry_id=None):
    driver_id=form["driver_id"]; vehicle_id=form["vehicle_id"]; customer_id=form.get("customer_id") or None
    work_date=form["work_date"]; start=form.get("start_time",""); end=form.get("end_time","")
    manual_hours=form.get("hours","").strip()
    hours=float(manual_hours) if manual_hours else calculate_hours(start,end)
    rate=float(form.get("rate_per_hour") or 0)
    if rate<=0:
        r=query_one("SELECT rate_per_hour FROM rates WHERE vehicle_type=(SELECT type FROM vehicles WHERE id=?)",(vehicle_id,)); rate=float(r["rate_per_hour"]) if r else 0
    amount=round(hours*rate,2)
    conn=db()
    if entry_id:
        conn.execute("""UPDATE work_entries SET driver_id=?,vehicle_id=?,customer_id=?,work_date=?,start_time=?,end_time=?,hours=?,rate_per_hour=?,amount=? WHERE id=?""",
            (driver_id,vehicle_id,customer_id,work_date,start,end,hours,rate,amount,entry_id))
    else:
        conn.execute("""INSERT INTO work_entries(driver_id,vehicle_id,customer_id,work_date,start_time,end_time,hours,rate_per_hour,amount) VALUES(?,?,?,?,?,?,?,?,?)""",
            (driver_id,vehicle_id,customer_id,work_date,start,end,hours,rate,amount))
    conn.commit(); conn.close(); return driver_id

@app.route("/add-work", methods=["GET","POST"])
@login_required
def add_work():
    drivers=query_all("SELECT * FROM drivers WHERE status='Active' ORDER BY name"); vehicles=query_all("SELECT * FROM vehicles ORDER BY number"); customers=query_all("SELECT * FROM customers WHERE status='Active' ORDER BY name")
    if request.method=="POST":
        driver_id=save_work(request.form); flash("Work entry saved. Hours and bill amount were calculated automatically."); return redirect(url_for("driver_detail",driver_id=driver_id))
    return render_template("work_form.html",title="Add Work Entry",drivers=drivers,vehicles=vehicles,customers=customers,entry=None,rates=query_all("SELECT * FROM rates"))

@app.route("/edit-work/<int:entry_id>", methods=["GET","POST"])
@login_required
def edit_work(entry_id):
    entry=query_one("SELECT * FROM work_entries WHERE id=?",(entry_id,))
    if not entry: return redirect(url_for("reports"))
    drivers=query_all("SELECT * FROM drivers ORDER BY name"); vehicles=query_all("SELECT * FROM vehicles ORDER BY number"); customers=query_all("SELECT * FROM customers ORDER BY name")
    if request.method=="POST":
        driver_id=save_work(request.form,entry_id); flash("Saved work entry updated. The bill was recalculated automatically."); return redirect(url_for("driver_detail",driver_id=driver_id))
    return render_template("work_form.html",title="Edit Work Entry",drivers=drivers,vehicles=vehicles,customers=customers,entry=entry,rates=query_all("SELECT * FROM rates"))

@app.route("/bill/<int:entry_id>")
@login_required
def bill(entry_id):
    entry=query_one("""SELECT w.*,d.name driver_name,d.phone driver_phone,v.number vehicle_number,v.type vehicle_type,
        COALESCE(c.name,'—') customer_name,COALESCE(c.phone,'') customer_phone,COALESCE(c.address,'') customer_address,
        c.id customer_id
        FROM work_entries w
        JOIN drivers d ON d.id=w.driver_id
        JOIN vehicles v ON v.id=w.vehicle_id
        LEFT JOIN customers c ON c.id=w.customer_id
        WHERE w.id=?""",(entry_id,))
    if not entry: return "Bill not found",404

    financials = customer_financials(entry["customer_id"]) if entry["customer_id"] else {"total": float(entry["amount"]), "paid": 0, "balance": float(entry["amount"])}
    payments = query_all(
        "SELECT * FROM payments WHERE customer_id=? ORDER BY payment_date ASC, id ASC",
        (entry["customer_id"],)
    ) if entry["customer_id"] else []
    return render_template("bill.html", entry=entry, payments=payments, **financials)

@app.post("/delete-driver/<int:driver_id>")
@login_required
def delete_driver(driver_id):
    conn=db();
    try:
        if not conn.execute("SELECT * FROM drivers WHERE id=?",(driver_id,)).fetchone(): flash("Operator not found.")
        elif conn.execute("SELECT COUNT(*) FROM work_entries WHERE driver_id=?",(driver_id,)).fetchone()[0]: flash("Cannot delete this operator because work records exist.")
        else: conn.execute("DELETE FROM drivers WHERE id=?",(driver_id,)); conn.commit(); flash("Operator deleted successfully.")
    finally: conn.close()
    return redirect(url_for("drivers"))

@app.post("/delete-vehicle/<int:vehicle_id>")
@login_required
def delete_vehicle(vehicle_id):
    conn=db();
    try:
        if not conn.execute("SELECT * FROM vehicles WHERE id=?",(vehicle_id,)).fetchone(): flash("Vehicle not found.")
        elif conn.execute("SELECT COUNT(*) FROM work_entries WHERE vehicle_id=?",(vehicle_id,)).fetchone()[0]: flash("Cannot delete this vehicle because work records exist.")
        else: conn.execute("DELETE FROM vehicles WHERE id=?",(vehicle_id,)); conn.commit(); flash("Vehicle deleted successfully.")
    finally: conn.close()
    return redirect(url_for("vehicles"))

@app.post("/delete-work/<int:entry_id>")
@login_required
def delete_work(entry_id):
    conn=db()
    try:
        entry=conn.execute("SELECT driver_id FROM work_entries WHERE id=?",(entry_id,)).fetchone()
        if not entry: flash("Work record not found."); return redirect(url_for("reports"))
        driver_id=entry["driver_id"]; conn.execute("DELETE FROM work_entries WHERE id=?",(entry_id,)); conn.commit(); flash("Work record deleted successfully."); return redirect(url_for("driver_detail",driver_id=driver_id))
    finally: conn.close()

@app.post("/delete-customer/<int:customer_id>")
@login_required
def delete_customer(customer_id):
    conn=db()
    try:
        customer = conn.execute("SELECT id FROM customers WHERE id=?", (customer_id,)).fetchone()
        payment_count = conn.execute("SELECT COUNT(*) FROM payments WHERE customer_id=?", (customer_id,)).fetchone()[0]
        if not customer:
            flash("Customer not found.")
        elif payment_count:
            flash("Cannot delete this customer because payment history exists. Keep the customer record for account history.")
        else:
            conn.execute("UPDATE work_entries SET customer_id=NULL WHERE customer_id=?", (customer_id,))
            conn.execute("DELETE FROM customers WHERE id=?", (customer_id,))
            conn.commit()
            flash("Customer deleted.")
    finally:
        conn.close()
    return redirect(url_for("customers"))

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
