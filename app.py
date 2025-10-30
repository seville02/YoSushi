from flask import Flask, render_template,Blueprint, request, redirect, url_for, flash, session, make_response, g
import sqlite3
import datetime
from datetime import datetime, timedelta, date
import os
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
import sys
import base64
from collections import defaultdict
import json
import re

basedir = os.path.abspath(os.path.dirname(__file__))
DAYS_OF_WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
app = Flask(
    __name__,
    template_folder=os.path.join(basedir, 'templates')
)
app.secret_key = "supersecretkey"
DB_NAME = "attendance.db"
TARGET_LAT = 53.70146
TARGET_LON = -6.37396
RECORDS_PER_PAGE = 10

@app.after_request
def add_header(response):
    """Disable caching for all responses to prevent ghosting of old dashboard data."""
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.before_request
def load_logged_in_employee():
    """Loads the employee object into the global 'g' object before every request."""
    employee_id = session.get('emp_id')
    role = session.get('role')
    g.employee = None

    if employee_id is not None and role == 'employee':
        conn = get_db_connection()
        employee_data = conn.execute(
            "SELECT * FROM employees WHERE emp_id=?", (employee_id,)
        ).fetchone()
        conn.close()
        if employee_data:
            g.employee = employee_data
def get_db_connection():
    conn = sqlite3.connect('attendance.db')
    conn.row_factory = sqlite3.Row
    setup_db_schema(conn)
    return conn

def create_requests_table():
    conn = get_db_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                submitted_at TEXT NOT NULL,
                FOREIGN KEY (emp_id) REFERENCES employees (emp_id)
            );
        """)
        conn.commit()
        print("Database initialized: 'requests' table created successfully.")
    except Exception as e:
        print(f"Error creating requests table: {e}")
        conn.rollback()
    finally:
        conn.close()
def setup_db_schema(conn):
    try:
        conn.execute("ALTER TABLE employee_requests ADD COLUMN type TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE employee_requests ADD COLUMN admin_response TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE employee_requests ADD COLUMN end_date TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
           CREATE TABLE IF NOT EXISTS employee_requests (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               emp_id INTEGER NOT NULL,
               type TEXT NOT NULL,         
               start_date TEXT NOT NULL,
               end_date TEXT,
               reason TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'Pending',
               submitted_at TEXT NOT NULL,
               admin_response TEXT,        
               FOREIGN KEY (emp_id) REFERENCES employees (emp_id)
           );
       """)
    conn.commit()

def initialize_db():
    """Initializes the database tables and inserts dummy data."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS employees (
            emp_id INTEGER PRIMARY KEY, 
            name TEXT NOT NULL, 
            password TEXT NOT NULL
        )"""
    )
    try:
        cur.execute("ALTER TABLE employees ADD COLUMN employee_type TEXT DEFAULT 'Full-Time'")
    except:
        pass
    try:
        cur.execute("ALTER TABLE employees ADD COLUMN is_supervisor INTEGER DEFAULT 0")
    except:
        pass
    try:
        cur.execute("ALTER TABLE employees ADD COLUMN phone_number TEXT")
    except:
        pass
    try:
        cur.execute("ALTER TABLE employees ADD COLUMN email TEXT")
    except:
        pass
    try:
        cur.execute("ALTER TABLE employees ADD COLUMN address TEXT")
    except:
        pass
    cur.execute(
        """CREATE TABLE IF NOT EXISTS admins (admin_id INTEGER PRIMARY KEY, username TEXT NOT NULL, password TEXT NOT NULL)""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        clock_in TEXT,
        clock_out TEXT,
        break_start TEXT, 
        break_end TEXT,   
        latitude REAL,         
        longitude REAL,        
        FOREIGN KEY(emp_id) REFERENCES employees(emp_id)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS breaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attendance_id INTEGER NOT NULL,
        break_start TEXT NOT NULL,
        break_end TEXT, -- NULL if break is ongoing
        FOREIGN KEY(attendance_id) REFERENCES attendance(id)
    )
    """)
    cur.execute("INSERT OR IGNORE INTO employees (emp_id, name, password) VALUES (1, 'John Doe', '1234')")
    cur.execute("INSERT OR IGNORE INTO admins (admin_id, username, password) VALUES (1, 'admin', 'admin')")
    conn.commit()
    conn.close()
def get_start_of_week(week_offset=0):
    today = date.today()
    start_of_this_week = today - timedelta(days=today.weekday())
    return start_of_this_week + timedelta(weeks=week_offset)

@app.route("/admin/roster/edit", methods=['GET', 'POST'])
def admin_roster_editor():
    if 'admin_user' not in session and not session.get('is_admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("home"))
    try:
        week_offset = int(request.args.get('week_offset', 0))
    except (ValueError, TypeError):
        week_offset = 0
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        week_start_date = get_start_of_week(week_offset)
        week_start_date_str = week_start_date.strftime('%Y-%m-%d')
        week_end_date_str = (week_start_date + timedelta(days=6)).strftime('%Y-%m-%d')
        dates_in_week = [week_start_date + timedelta(days=i) for i in range(7)]
        date_headers = [f"{d.strftime('%a')} {d.strftime('%Y-%m-%d')}" for d in dates_in_week]
        employees = conn.execute("SELECT emp_id, name FROM employees ORDER BY name").fetchall()
        if request.method == 'POST':
            cursor.execute("BEGIN TRANSACTION")
            for employee in employees:
                cursor.execute(
                    "DELETE FROM roster WHERE emp_id = ? AND date BETWEEN ? AND ?",
                    (employee['emp_id'], week_start_date_str, week_end_date_str)
                )
            shifts_to_insert = []
            for key, value in request.form.items():
                if key.startswith('shift_start_'):
                    start_time = value.strip() if value else None
                    if not start_time: continue
                    parts = key.split('_')
                    if len(parts) != 4: continue
                    try:
                        emp_id = int(parts[2])
                        date_str = parts[3]
                    except ValueError:
                        continue
                    end_key = f"shift_end_{emp_id}_{date_str}"
                    end_time = request.form.get(end_key).strip() if request.form.get(end_key) else None
                    if end_time:
                        shifts_to_insert.append((emp_id, date_str, start_time, end_time, 'Scheduled Shift'))
            if shifts_to_insert:
                cursor.executemany(
                    "INSERT INTO roster (emp_id, date, start_time, end_time, shift_name) VALUES (?, ?, ?, ?, ?)",
                    shifts_to_insert
                )
            conn.commit()
            flash(f"Roster for week {week_start_date_str} saved successfully! ({len(shifts_to_insert)} shifts inserted)", "success")
            return redirect(url_for('admin_roster_editor', week_offset=week_offset))
        week_data = {}
        daily_totals_minutes = {d.strftime('%a'): 0 for d in dates_in_week}
        weekly_totals_minutes = {}
        for employee in employees:
            emp_id = employee['emp_id']
            employee_total_minutes = 0
            week_data[emp_id] = {}
            raw_roster = get_employee_roster_data(emp_id, week_start_date_str, conn)
            roster_map = {shift['date']: shift for shift in raw_roster}
            for d in dates_in_week:
                date_str = d.strftime('%Y-%m-%d')
                day_name = d.strftime('%a')
                shift = roster_map.get(date_str, {})
                start_time = shift.get('start_time')
                end_time = shift.get('end_time')
                if shift.get('has_shift', True) and start_time and end_time:
                    break_minutes = get_break_duration_for_shift(emp_id, date_str, conn) or 0
                    gross_minutes = calculate_time_diff(start_time, end_time, date_str)
                    net_minutes = max(0, gross_minutes - break_minutes)
                    week_data[emp_id][day_name] = f"{start_time} - {end_time}"
                    employee_total_minutes += net_minutes
                    daily_totals_minutes[day_name] += net_minutes
                else:
                    week_data[emp_id][day_name] = 'OFF'
            weekly_totals_minutes[emp_id] = employee_total_minutes

        nav_dates = {'start': week_start_date_str, 'end': week_end_date_str}
        daily_totals_hours = {k: round(v / 60.0, 2) for k, v in daily_totals_minutes.items()}
        weekly_totals_hours = {k: round(v / 60.0, 2) for k, v in weekly_totals_minutes.items()}

        return render_template(
            "admin_roster_editor.html",
            employees=employees,
            date_headers=date_headers,
            week_data=week_data,
            daily_totals=daily_totals_hours,
            weekly_totals=weekly_totals_hours,
            nav_dates=nav_dates,
            week_offset=week_offset,
            current_week=week_offset == 0
        )

    except Exception as e:
        print(f"ROSTER EDITOR CRITICAL FAILURE: {e}")
        error_message = f"Error processing roster: {e}"
        if conn:
            conn.rollback()
        return jsonify({'status': 'error', 'message': error_message}), 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.route("/admin/graphs")
def admin_graphs():
    chart_data = {
        'hoursTrend': {"labels": [], "data": []},
        'typeBreakdown': {"labels": [], "data": []},
        'totalEmployees': 0,
        'todayAttendance': [],
        'shiftsCompleted': {"labels": [], "data": []}
    }

    if 'admin_user' not in session:
        flash("Access denied. Please log in as admin.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    try:
        raw_attendance_query = """
        SELECT 
            date,
            clock_in,
            clock_out
        FROM attendance 
        WHERE clock_out IS NOT NULL 
        ORDER BY date, clock_in ASC
        """
        raw_logs = conn.execute(raw_attendance_query).fetchall()
        weekly_hours = defaultdict(float)

        for log in raw_logs:
            shift_date_str = log['date']
            clock_in_str = log['clock_in']
            clock_out_str = log['clock_out']

            if not clock_in_str or not clock_out_str or not shift_date_str:
                continue
            shift_minutes = calculate_time_diff(clock_in_str, clock_out_str, shift_date_str)
            try:
                clock_in_dt = datetime.strptime(shift_date_str, '%Y-%m-%d')
                if shift_minutes > 0:
                    year, week, _ = clock_in_dt.isocalendar()
                    week_label = f"{year}-W{week:02d}"
                    weekly_hours[week_label] += shift_minutes / 60.0
            except ValueError:
                continue

        sorted_weeks = sorted(weekly_hours.keys())
        chart_data['hoursTrend'] = {
            "labels": sorted_weeks,
            "data": [weekly_hours[week] for week in sorted_weeks]
        }
        type_breakdown_query = "SELECT employee_type, COUNT(emp_id) as count FROM employees GROUP BY employee_type"
        type_data = conn.execute(type_breakdown_query).fetchall()
        chart_data['typeBreakdown'] = {
            "labels": [row['employee_type'] for row in type_data],
            "data": [row['count'] for row in type_data]
        }
        total_employees_query = "SELECT COUNT(emp_id) as total FROM employees"
        total_employees_result = conn.execute(total_employees_query).fetchone()
        chart_data['totalEmployees'] = total_employees_result['total'] if total_employees_result else 0
        shifts_query = """
        SELECT 
            e.name, 
            COUNT(a.id) as shift_count
        FROM attendance a
        JOIN employees e ON a.emp_id = e.emp_id
        WHERE a.clock_out IS NOT NULL
        GROUP BY e.name
        ORDER BY shift_count DESC
        """
        shifts_data = conn.execute(shifts_query).fetchall()
        chart_data['shiftsCompleted'] = {
            "labels": [row['name'] for row in shifts_data],
            "data": [row['shift_count'] for row in shifts_data]
        }
        today_date = datetime.now().strftime('%Y-%m-%d')
        attendance_query = f"""
        SELECT 
            e.name, 
            (a.date || ' ' || a.clock_in) AS clock_in_full,
            (a.date || ' ' || a.clock_out) AS clock_out_full,
            CASE WHEN a.clock_out IS NULL THEN 'Active' ELSE 'Completed' END AS status
        FROM attendance a
        JOIN employees e ON a.emp_id = e.emp_id
        WHERE a.date = '{today_date}'
        ORDER BY a.clock_in DESC
        """
        attendance_data = conn.execute(attendance_query).fetchall()
        chart_data['todayAttendance'] = [{
            'name': row['name'],
            'clock_in': row['clock_in_full'],
            'status': row['status']
        } for row in attendance_data]

    except Exception as e:
        print(f"FATAL APPLICATION ERROR FETCHING GRAPHS: {e}")
        flash(f"Error fetching analytics data: {e}. Check server logs.", "error")
    finally:
        if conn:
            conn.close()
    return render_template("admin_graphs.html", chart_data=chart_data)

@app.route("/", methods=["GET", "POST"])
def home():
    if 'emp_id' in session and session.get('role') == 'employee':
        emp_id = session['emp_id']
        return redirect(url_for("employee_dashboard", emp_id=emp_id))

    if 'admin_user' in session and session.get('role') == 'admin':
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        conn = get_db_connection()
        admin_record = None

        if "emp_id" in request.form:
            emp_id = request.form["emp_id"]
            password = request.form["password"]
            employee = conn.execute("SELECT * FROM employees WHERE emp_id=? AND password=?",
                                    (emp_id, password)).fetchone()
            if employee:
                session['emp_id'] = employee["emp_id"]
                session['name'] = employee["name"]
                session['role'] = 'employee'
                session['logged_in'] = True
                flash(f"Welcome, {employee['name']}!", "success")
                conn.close()
                return redirect(url_for("employee_dashboard", emp_id=employee["emp_id"]))
            else:
                flash("Invalid Employee ID or Password.", "error")

        elif "admin_username" in request.form:
            username = request.form["admin_username"]
            password = request.form["admin_password"]
            admin = conn.execute("SELECT * FROM admins WHERE username=? AND password=?",
                                 (username, password)).fetchone()
            if admin:
                session['admin_user'] = admin["username"]
                session['role'] = 'admin'
                session['logged_in'] = True
                session['is_admin'] = True
                flash("Admin login successful.", "success")
                conn.close()
                return redirect(url_for('admin_panel'))
            else:
                flash("Invalid Admin Credentials", "error")
        try:
            if admin_record and admin_record['password'] == admin_password:
                session['logged_in'] = True
                session['role'] = 'admin'
                session['admin_user'] = admin_username
                session['is_admin'] = True
                return redirect(url_for('admin_panel'))
            else:
                pass
        except NameError:
            pass
        except KeyError:
            pass

        conn.close()

    return render_template("home.html")

@app.route("/employee/logout")
def employee_logout():
    session.clear()
    flash("You have successfully logged out.", "success")
    response = make_response(redirect(url_for("home")))
    response.set_cookie('session', '', expires=0)
    return response


@app.route('/employee/<int:emp_id>/make-request', methods=['GET', 'POST'])
def employee_make_request(emp_id):
    if 'emp_id' not in session or int(session.get('emp_id')) != emp_id:
        flash("Access denied. Please log in.", 'warning')
        return redirect(url_for("home"))
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    employee_data_row = conn.execute("SELECT * FROM employees WHERE emp_id=?", (emp_id,)).fetchone()
    if not employee_data_row:
        conn.close()
        flash("Employee record not found.", 'danger')
        return redirect(url_for('home'))
    employee_details = dict(employee_data_row)

    if request.method == 'POST':
        try:
            request_type = request.form['request_type']
            start_date = request.form['start_date']
            end_date = request.form.get('end_date')
            reason_details = request.form['reason']
            conn.execute("""
                INSERT INTO employee_requests 
                (emp_id, request_type, start_date, end_date, content, status, date_submitted) 
                VALUES (?, ?, ?, ?, ?, 'Pending', datetime('now'))
            """, (emp_id, request_type, start_date, end_date if end_date else None, reason_details))
            conn.commit()
            flash("Your request has been submitted successfully!", 'success')
            conn.close()
            return redirect(url_for('employee_make_request', emp_id=emp_id))

        except sqlite3.Error as e:
            flash(f"Database error during submission: {e}", 'danger')
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    try:
        total_requests_row = conn.execute(
            "SELECT COUNT(*) FROM employee_requests WHERE emp_id=?",
            (emp_id,)
        ).fetchone()
        total_requests = total_requests_row[0]
        total_pages = math.ceil(total_requests / RECORDS_PER_PAGE) if total_requests > 0 else 1
        if page < 1: page = 1
        if page > total_pages: page = total_pages
        offset = (page - 1) * RECORDS_PER_PAGE
        raw_requests = conn.execute(
            f"""
            SELECT * FROM employee_requests 
            WHERE emp_id=? 
            ORDER BY date_submitted DESC 
            LIMIT {RECORDS_PER_PAGE} OFFSET {offset}
            """,
            (emp_id,)
        ).fetchall()
        employee_requests = [dict(row) for row in raw_requests]
    except sqlite3.OperationalError as e:
        employee_requests = []
        total_pages = 1
        flash(f"Database error loading history: {e}", "danger")
    finally:
        conn.close()
    return render_template('employee_make_request.html',
                           employee=employee_details,
                           requests=employee_requests,
                           page=page,
                           total_pages=total_pages,
                           pages=range(1, total_pages + 1))

@app.route("/employee/dashboard/<int:emp_id>", methods=["GET", "POST"])
def employee_dashboard(emp_id):
    if 'emp_id' not in session or int(session.get('emp_id')) != emp_id:
        flash("Session expired or unauthorized access.", "warning")
        return redirect(url_for("home"))

    conn = get_db_connection()
    now_dt = datetime.now()
    now_time = now_dt.strftime("%H:%M:%S")
    now_date = now_dt.strftime("%Y-%m-%d")
    transaction_successful = False
    employee_data_row = conn.execute("SELECT * FROM employees WHERE emp_id=?", (emp_id,)).fetchone()
    if not employee_data_row:
        conn.close()
        flash("Employee record not found. Please contact admin.", "error")
        session.clear()
        return redirect(url_for("home"))
    employee_details = dict(employee_data_row)
    current_shift_data = conn.execute(
        "SELECT id, clock_in, clock_out FROM attendance WHERE emp_id=? AND date=? ORDER BY id DESC LIMIT 1",
        (emp_id, now_date)
    ).fetchone()
    current_shift = None
    shift_is_active = (current_shift_data is not None and current_shift_data['clock_out'] is None)
    log_id = None
    if shift_is_active:
        current_shift = dict(current_shift_data)
        log_id = current_shift['id']
    else:
        current_shift = {'id': None, 'clock_in': 'N/A', 'clock_out': 'N/A'}
        log_id = None
    active_break = None
    break_is_active = "false"
    has_any_break_entry = "false"
    total_break_display = "0h 0m"
    total_break_minutes = 0
    last_break_start_display = "N/A"
    last_break_end_display = "N/A"
    if log_id:
        active_break_row = conn.execute(
            "SELECT id, break_start FROM breaks WHERE attendance_id=? AND break_end IS NULL ORDER BY id DESC LIMIT 1",
            (log_id,)
        ).fetchone()
        if active_break_row:
            active_break = active_break_row
            break_is_active = "true"
        break_count = conn.execute("SELECT COUNT(*) FROM breaks WHERE attendance_id=?", (log_id,)).fetchone()[0]
        if break_count > 0:
            has_any_break_entry = "true"
            completed_breaks = conn.execute(
                "SELECT break_start, break_end FROM breaks WHERE attendance_id=? AND break_end IS NOT NULL",
                (log_id,)
            ).fetchall()
            for b in completed_breaks:
                total_break_minutes += calculate_time_diff(b['break_start'], b['break_end'], now_date)
            total_break_display = format_minutes_to_hours_and_minutes(total_break_minutes)
            last_break_entry = conn.execute(
                "SELECT break_start, break_end FROM breaks WHERE attendance_id=? ORDER BY id DESC LIMIT 1",
                (log_id,)
            ).fetchone()

            if last_break_entry:
                last_break_start_display = last_break_entry['break_start'].split(' ')[-1] if last_break_entry[
                    'break_start'] else "N/A"
                last_break_end_display = last_break_entry['break_end'].split(' ')[-1] if last_break_entry[
                    'break_end'] else "N/A"
    if request.method == 'POST':
        action = request.form.get('action')
        current_lat = request.form.get('current_lat')
        current_lon = request.form.get('current_lon')
        request_content = request.form.get("request_content")
        request_type = request.form.get("request_type")

    elif request.method == 'GET':
        action = request.args.get('action')
        current_lat = request.args.get('current_lat')
        current_lon = request.args.get('current_lon')
        request_content = None
        request_type = None
    else:
        action = None
        current_lat = None
        current_lon = None
        request_content = None
        request_type = None
    if action:
        if action == "clock_in":
            if shift_is_active:
                flash("You are already clocked in.", "warning")
            else:
                lat_to_save = current_lat if current_lat else None
                lon_to_save = current_lon if current_lon else None
                conn.execute(
                    "INSERT INTO attendance (emp_id, date, clock_in, latitude, longitude) VALUES (?, ?, ?, ?, ?)",
                    (emp_id, now_date, now_time, lat_to_save, lon_to_save)
                )
                conn.commit()
                flash(f"🕒 Clocked in at {now_time}.", "success")
                transaction_successful = True
        elif action == "clock_out":
            if not shift_is_active:
                flash("You are not currently clocked in.", "error")
            elif break_is_active == "true":
                flash("Finish your break before clocking out.", "error")
            elif log_id is None:
                flash("Cannot clock out without an active shift log.", "error")
            else:
                conn.execute("UPDATE breaks SET break_end=? WHERE attendance_id=? AND break_end IS NULL",
                             (now_time, log_id))
                conn.execute(
                    "UPDATE attendance SET clock_out=? WHERE id=?",
                    (now_time, log_id)
                )
                conn.commit()
                flash(f"👋 Clocked out at {now_time}. Total break: {total_break_display}.", "success")
                conn.close()
                return redirect(url_for("employee_dashboard", emp_id=emp_id))

        elif action == "break_start":
            if not shift_is_active or log_id is None:
                flash("You must be clocked in to start a break.", "error")
            elif break_is_active == "true":
                flash("You are already on an active break.", "warning")
            else:
                conn.execute(
                    "INSERT INTO breaks (attendance_id, break_start) VALUES (?, ?)",
                    (log_id, now_time)
                )
                conn.commit()
                flash(f"☕ Break started at {now_time}.", "success")
                transaction_successful = True
        elif action == "break_end":
            active_break_row = conn.execute("SELECT id FROM breaks WHERE attendance_id=? AND break_end IS NULL",
                                            (log_id,)).fetchone()
            if break_is_active != "true" or active_break_row is None:
                flash("No active break to end.", "warning")
            else:
                active_break_id = active_break_row[0]
                conn.execute(
                    "UPDATE breaks SET break_end=? WHERE id=?",
                    (now_time, active_break_id)
                )
                conn.commit()
                flash(f"✅ Break ended at {now_time}.", "success")
                transaction_successful = True
        elif action == "submit_request":
            if not request_type or not request_content:
                flash("Request type and details are required.", "error")
            else:
                conn.execute(
                    """INSERT INTO employee_requests 
                       (emp_id, date_submitted, request_type, content, status) 
                       VALUES (?, ?, ?, ?, ?)""",
                    (emp_id, now_date, request_type, request_content, 'Pending')
                )
                conn.commit()
                flash("✉️ Request submitted successfully! The Admin will review it.", "success")
                transaction_successful = True
        if transaction_successful:
            conn.close()
            return redirect(url_for("employee_dashboard", emp_id=emp_id))
    conn.close()
    return render_template(
        "employee.html",
        emp_id=emp_id,
        employee=employee_details,
        current_shift=current_shift,
        shift_is_active=shift_is_active,
        break_is_active=break_is_active,
        has_any_break_entry=has_any_break_entry,
        total_break_display=total_break_display,
        last_break_start=last_break_start_display,
        last_break_end=last_break_end_display,
        now_time=now_time,
        today=now_date
    )
def setup_db_schema(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS roster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            shift_name TEXT,
            FOREIGN KEY (emp_id) REFERENCES employees(emp_id),
            UNIQUE (emp_id, date)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employee_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id INTEGER NOT NULL,
            date_submitted TEXT NOT NULL,
            request_type TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            admin_response TEXT,
            response_date TEXT,
            FOREIGN KEY (emp_id) REFERENCES employees(emp_id)
        )
    """)
    try:
        cursor.execute("ALTER TABLE employees ADD COLUMN is_admin INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE employees ADD COLUMN is_supervisor INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.commit()
def calculate_shift_hours(start_time_str, end_time_str, break_minutes, date_str):
    if break_minutes is None:
        break_minutes = 0.0
    try:
        break_minutes = float(break_minutes)
    except (TypeError, ValueError):
        break_minutes = 0.0
    gross_minutes = calculate_time_diff(start_time_str, end_time_str, date_str)

    if gross_minutes is None or gross_minutes <= 0:
        return '0h 0m', '0h 0m'
    net_minutes = gross_minutes - break_minutes

    if net_minutes < 0:
        net_minutes = 0
    gross_hours_str = format_minutes_to_hours_and_minutes(gross_minutes)
    net_hours_str = format_minutes_to_hours_and_minutes(net_minutes)
    return net_hours_str, gross_hours_str
def minutes_to_hours(minutes):
    if minutes is None or minutes <= 0:
        return 0.0
    return (minutes / 60.0, 2)
RECORDS_PER_PAGE = 10
def get_aggregated_attendance_details_for_date(emp_id, shift_date, conn):
    total_net_minutes = 0.0
    total_break_minutes = 0.0
    first_clock_in = None
    last_clock_out = None
    attendance_logs = conn.execute(
        "SELECT id, clock_in, clock_out FROM attendance WHERE emp_id=? AND date=?",
        (emp_id, shift_date)
    ).fetchall()

    for log in attendance_logs:
        attendance_id = log['id']
        clock_in = log['clock_in']
        clock_out = log['clock_out']
        break_min_segment = 0.0
        break_records = conn.execute(
            "SELECT break_start, break_end FROM breaks WHERE attendance_id=? AND break_end IS NOT NULL ORDER BY break_start",
            (attendance_id,)
        ).fetchall()

        for record in break_records:
            break_min = calculate_time_diff(record['break_start'], record['break_end'], shift_date)
            break_min_segment += break_min
        total_break_minutes += max(0.0, break_min_segment)
        gross_min_log = 0.0
        if clock_in and clock_out:
            gross_min_log = calculate_time_diff(clock_in, clock_out, shift_date)

        net_min = gross_min_log - break_min_segment
        total_net_minutes += max(0.0, net_min)
        if clock_in:
            if first_clock_in is None or str(clock_in) < str(first_clock_in):
                first_clock_in = clock_in
        if clock_out:
            if last_clock_out is None or str(clock_out) > str(last_clock_out):
                last_clock_out = clock_out

    return round(total_net_minutes, 0), round(total_break_minutes, 0), first_clock_in, last_clock_out
def format_minutes_to_hours_and_minutes(total_minutes):
    if total_minutes is None:
        return "-"
    try:
        total_minutes = float(total_minutes)
    except Exception:
        return "-"
    if total_minutes < 0:
        return "-"
    hours = int(total_minutes // 60)
    minutes = int(total_minutes % 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if not parts:
        return "0m"
    return " ".join(parts)

def calculate_shift_gross_net(roster_start, roster_end, break_min, act_start, act_end, shift_date):
    scheduled_gross = calculate_time_diff(roster_start, roster_end, shift_date) if roster_start and roster_end else 0

    if act_start and act_end:
        gross_min = calculate_time_diff(act_start, act_end, shift_date)
    elif act_start and not act_end and roster_end:
        dt_start = datetime.strptime(f"{shift_date} {act_start}", "%Y-%m-%d %H:%M")
        dt_end = datetime.strptime(f"{shift_date} {roster_end}", "%Y-%m-%d %H:%M")
        if dt_end < dt_start:
            dt_end += timedelta(days=1)
        gross_min = (dt_end - dt_start).total_seconds() / 60.0
    else:
        gross_min = scheduled_gross

    net_min = max(0, int(gross_min) - int(break_min or 0))
    return gross_min, net_min
def calculate_time_diff(start_time_str, end_time_str, date_str):
    if not start_time_str or not end_time_str:
        return 0.0

    FMT = '%Y-%m-%d %H:%M'

    def extract_time_hm(time_value):
        time_str = str(time_value)
        match = re.search(r'(\d{1,2}:\d{2})', time_str.split()[-1])
        if match:
            h, m = map(int, match.group(1).split(':'))
            return f"{h:02d}:{m:02d}"
        return "00:00"

    start_time_hm = extract_time_hm(start_time_str)
    end_time_hm = extract_time_hm(end_time_str)
    start_full = f"{date_str} {start_time_hm}"
    end_full = f"{date_str} {end_time_hm}"

    try:
        dt_start = datetime.strptime(start_full, FMT)
        dt_end = datetime.strptime(end_full, FMT)

        if dt_end < dt_start:
            dt_end += timedelta(days=1)

        diff_minutes = (dt_end - dt_start).total_seconds() / 60.00

        return max(0.0, diff_minutes)

    except ValueError as e:
        return 0.0

def safe_parse_log_time(time_str, date_hint=None):
    if not time_str:
        return None
    s = str(time_str).strip()
    s = s.split('.')[0]
    patterns = [
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%H:%M:%S',
        '%H:%M'
    ]
    for p in patterns:
        try:
            dt = datetime.strptime(s, p)
            if p in ('%H:%M:%S', '%H:%M') and date_hint:
                date_part = date_hint
                return datetime.combine(datetime.strptime(date_part, '%Y-%m-%d').date(), dt.time())
            return dt
        except Exception:
            pass
    time_only_match = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', s)
    if time_only_match and date_hint:
        t = time_only_match.group(1)
        try:
            if len(t.split(':')) == 2:
                full = f"{date_hint} {t}"
                return datetime.strptime(full, '%Y-%m-%d %H:%M')
            else:
                full = f"{date_hint} {t}"
                return datetime.strptime(full, '%Y-%m-%d %H:%M:%S')
        except Exception:
            return None
    return None


@app.route("/employee/<int:emp_id>/roster")
def employee_view_roster(emp_id):
    try:
        week_offset = int(request.args.get("week_offset", 0))
    except ValueError:
        week_offset = 0

    if "emp_id" not in session or int(session.get("emp_id")) != emp_id:
        flash("Access denied. Please log in.", "warning")
        return redirect(url_for("home"))

    conn = get_db_connection()
    default_hours = "-"
    day_off_display = "-"
    NET_WORK_MINIMUM_THRESHOLD_MINUTES = 10
    now_dt = datetime.now()

    try:
        employee_row = conn.execute("SELECT * FROM employees WHERE emp_id=?", (emp_id,)).fetchone()
        if not employee_row:
            flash("Employee record not found. Cannot display roster.", "danger")
            return redirect(url_for("employee_dashboard", emp_id=emp_id))
        employee = dict(employee_row)
        employee_name = employee.get("name", "Employee")
        week_start = get_start_of_week(week_offset)
        week_start_str = week_start.strftime("%Y-%m-%d")
        week_end_str = (week_start + timedelta(days=6)).strftime("%Y-%m-%d")
        raw_roster = get_employee_roster_data(emp_id, week_start_str, conn)
        processed = []

        for s in raw_roster:
            shift_date = s.get("date")
            roster_start = s.get("start_time")
            roster_end = s.get("end_time")
            override_status = s.get("override_status")

            s.update({
                "work_hours_net": default_hours,
                "total_hours_gross": default_hours,
                "actual_status": "",
                "shift_name": "",
                "status_class": "",
                "row_color": ""
            })

            try:
                dt = datetime.strptime(shift_date, "%Y-%m-%d")
                s["day"] = dt.strftime("%a")
                is_future = dt.date() > now_dt.date()
            except Exception:
                s["day"] = "???"
                is_future = False

            gross_db, breaks_db, act_start, act_end = get_aggregated_attendance_details_for_date(emp_id, shift_date,
                                                                                                 conn)
            s["break_min"] = int(breaks_db or 0)

            if override_status == "DAY OFF" or not roster_start:
                s.update({
                    "shift_name": "DAY OFF",
                    "actual_status": day_off_display,
                    "row_color": "bg-danger-subtle text-danger",
                    "start_time": "",
                    "end_time": "",
                    "work_hours_net": day_off_display,
                    "total_hours_gross": day_off_display,
                    "break_min": 0
                })
                processed.append(s)
                continue

            if roster_start and roster_end:
                s["shift_name"] = f"{roster_start} - {roster_end}"
                s["start_time"] = roster_start
                s["end_time"] = roster_end

                scheduled_gross_min = calculate_time_diff(roster_start, roster_end, shift_date)

                has_attendance = bool(act_start)

                if has_attendance:
                    gross_min_display = scheduled_gross_min
                    net_min_display = max(0, int(gross_min_display) - int(s["break_min"]))

                    s["total_hours_gross"] = format_minutes_to_hours_and_minutes(gross_min_display)
                    s["work_hours_net"] = format_minutes_to_hours_and_minutes(net_min_display)

                    s.update({
                        "actual_status": "Attendance Logged",
                        "status_class": "logged",
                        "row_color": "bg-success-subtle text-success"
                    })

                elif not is_future:
                    s.update({
                        "actual_status": "Shift Missed",
                        "status_class": "missed",
                        "row_color": "bg-danger-subtle text-danger",
                    })
                    s["work_hours_net"] = default_hours

                else:
                    scheduled_net_min = max(0, scheduled_gross_min - s["break_min"])
                    s.update({
                        "actual_status": "Scheduled",
                        "status_class": "scheduled",
                        "row_color": "bg-info-subtle text-info",
                        "total_hours_gross": format_minutes_to_hours_and_minutes(scheduled_gross_min),
                        "work_hours_net": format_minutes_to_hours_and_minutes(scheduled_net_min)
                    })

            processed.append(s)

        return render_template(
            "employee_roster.html",
            roster_data=processed,
            datetime=datetime,
            employee=employee,
            employee_name=employee_name,
            emp_id=emp_id,
            week_offset=week_offset,
            nav_dates={"start": week_start_str, "end": week_end_str},
            current_week=(week_offset == 0),
        )


    except Exception as e:
        import sys
        print(f"EMPLOYEE ROSTER ERROR for {emp_id}: {e}", file=sys.stderr)
        flash("Error loading roster. Please try again later.", "danger")
        return redirect(url_for("employee_dashboard", emp_id=emp_id))
    finally:
        if conn:
            conn.close()

def parse_duration_to_minutes(duration_str):
    if not duration_str or duration_str == '-':
        return 0
    hours = minutes = 0
    if 'h' in duration_str:
        hours_part = duration_str.split('h')[0].strip()
        hours = int(hours_part) if hours_part.isdigit() else 0
        if 'm' in duration_str:
            minutes_part = duration_str.split('h')[1].replace('m', '').strip()
            minutes = int(minutes_part) if minutes_part.isdigit() else 0
    elif 'm' in duration_str:
        minutes = int(duration_str.replace('m', '').strip())
    return hours * 60 + minutes

@app.route("/admin/employees")
def view_all_employees():
    if 'admin_user' not in session:
        flash("Access denied. Please log in as admin.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    employees_raw = conn.execute(
        "SELECT emp_id, name, employee_type, is_supervisor, phone_number, email, address FROM employees ORDER BY emp_id"
    ).fetchall()
    employees = []
    clocked_in_count = 0
    for row in employees_raw:
        employee = dict(row)
        emp_id = employee['emp_id']
        active_log = conn.execute(
            "SELECT id FROM attendance WHERE emp_id=? AND clock_out IS NULL",
            (emp_id,)
        ).fetchone()
        employee['status'] = 'Clocked-In' if active_log else 'Clocked-Out'
        if active_log:
            clocked_in_count += 1
        employees.append(employee)
    conn.close()

    return render_template(
        "admin_manage_employees.html",
        roster_employees=employees,
        clocked_in_count=clocked_in_count
    )

@app.route("/admin/employee/manage/<int:emp_id>", methods=['GET', 'POST'])
def manage_employee(emp_id):
    if 'admin_user' not in session and not session.get('is_admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    if request.method == 'GET':
        employee_data = conn.execute(
            "SELECT * FROM employees WHERE emp_id = ?",
            (emp_id,)
        ).fetchone()
        conn.close()

        if not employee_data:
            flash(f"Employee ID {emp_id} not found.", "error")
            return redirect(url_for("view_all_employees"))
        employee = dict(employee_data)

        return render_template(
            "admin_edit_employee.html",
            employee=employee,
            emp_id=emp_id
        )
    elif request.method == 'POST':
        new_name = request.form.get('name')
        new_type = request.form.get('employee_type')
        new_phone = request.form.get('phone_number')
        new_email = request.form.get('email')
        new_is_supervisor = 1 if request.form.get('is_supervisor') == 'on' else 0

        try:
            conn.execute(
                """
                UPDATE employees 
                SET 
                    name=?, 
                    employee_type=?, 
                    is_supervisor=?, 
                    phone_number=?, 
                    email=?
                WHERE emp_id=?
                """,
                (new_name, new_type, new_is_supervisor, new_phone, new_email, emp_id)
            )
            conn.commit()
            flash(f"Employee ID {emp_id} ({new_name}) updated successfully!", "success")
            return redirect(url_for("view_all_employees", emp_id=emp_id))

        except Exception as e:
            flash(f"Error updating employee: {e}", "error")
            conn.rollback()
            return redirect(url_for("view_all_employees", emp_id=emp_id))
        finally:
            conn.close()
@app.route("/admin/manage_employees", methods=["GET", "POST"])
def manage_employees():
    if 'admin_user' not in session and not session.get('is_admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row

    try:
        employees_raw = conn.execute(
            """SELECT emp_id, name, employee_type, is_supervisor, phone_number, email
               FROM employees 
               ORDER BY name"""
        ).fetchall()
        employees = []
        clocked_in_count = 0
        for row in employees_raw:
            employee = dict(row)
            emp_id = employee['emp_id']
            active_log = conn.execute(
                "SELECT id FROM attendance WHERE emp_id=? AND clock_out IS NULL",
                (emp_id,)
            ).fetchone()
            employee['status'] = 'Clocked-In' if active_log else 'Clocked-Out'
            if active_log:
                clocked_in_count += 1
            employees.append(employee)
        conn.close()

        return render_template(
            "admin_manage_employees.html",
            roster_employees=employees,
            clocked_in_count=clocked_in_count
        )
    except Exception as e:
        if conn:
            conn.close()
        print(f"EMPLOYEE MANAGE CRITICAL ERROR: {e}")
        flash("An error occurred loading the employee list.", "error")
        return redirect(url_for('admin_panel'))

def get_employee_by_id(emp_id):
            conn = get_db_connection()
            try:
                employee_data = conn.execute(
                    "SELECT emp_id, name, password, is_supervisor, employee_type FROM employees WHERE emp_id = ?",
                    (emp_id,)
                ).fetchone()
                if employee_data:
                    return dict(employee_data)
            except Exception as e:
                print(f"Error fetching employee {emp_id}: {e}")
            finally:
                conn.close()
            return None
def get_employee_roster_data(emp_id, week_start_date_str, conn):
    roster_data = []
    start_date_obj = datetime.strptime(week_start_date_str, '%Y-%m-%d').date()
    end_date_obj = start_date_obj + timedelta(days=6)
    week_end_date_str = end_date_obj.strftime('%Y-%m-%d')
    try:
        SQL = """
        SELECT id AS roster_id, date, start_time, end_time, shift_name
        FROM roster
        WHERE emp_id = ? AND date BETWEEN ? AND ?
        ORDER BY date;
        """
        raw_shifts = conn.execute(
            SQL,
            (emp_id, week_start_date_str, week_end_date_str)
        ).fetchall()
        roster_map = {row['date']: dict(row) for row in raw_shifts}
        all_dates = [start_date_obj + timedelta(days=i) for i in range(7)]

        for d in all_dates:
            date_str = d.strftime('%Y-%m-%d')
            if date_str in roster_map:
                shift_data = roster_map[date_str]
                shift_data['has_shift'] = True
                roster_data.append(shift_data)
            else:
                roster_data.append(
                    {'date': date_str, 'start_time': None, 'end_time': None, 'shift_name': None, 'has_shift': False, 'roster_id': None})

    except Exception as e:
        print(f"Error fetching roster data for employee {emp_id}: {e}")

    return roster_data

def get_roster_data(conn, emp_id=None):
    base_query = """
        SELECT r.id, r.date, r.start_time, r.end_time, r.shift_name, e.name AS employee_name, r.emp_id
        FROM roster r
        JOIN employees e ON r.emp_id = e.emp_id
        ORDER BY r.date DESC, e.name
    """
    if emp_id:
        roster_data = conn.execute(
            "SELECT r.id, r.date, r.start_time, r.end_time, r.shift_name FROM roster r WHERE r.emp_id=? ORDER BY r.date DESC",
            (emp_id,)
        ).fetchall()
        return [dict(row) for row in roster_data]
    else:
        roster_data = conn.execute(base_query).fetchall()
        roster_grid = {}
        employee_names = sorted(list(set(row['employee_name'] for row in roster_data)))
        unique_dates = sorted(list(set(row['date'] for row in roster_data)), reverse=True)
        for name in employee_names:
            roster_grid[name] = {date: 'OFF' for date in unique_dates}
        for row in roster_data:
            start_time = row['start_time'] if row['start_time'] else ''
            end_time = row['end_time'] if row['end_time'] else ''
            shift_name = row['shift_name'] if row['shift_name'] else ''
            shift = f"{start_time} - {end_time}"
            if shift_name:
                shift += f" ({shift_name})"
            roster_grid[row['employee_name']][row['date']] = shift
        return {
            'dates': unique_dates,
            'grid': roster_grid
        }

def get_employee_roster(emp_id):
    global db
    try:
        employee_data = db.execute(
            "SELECT emp_id, name, ... FROM employees WHERE emp_id = ?",
            (emp_id,)
        ).fetchone()

        if employee_data:
            return employee_data

    except Exception as e:
        print(f"FATAL DB ERROR: {e}")
    return None
    return [
        {'date': '2025-10-06', 'start_time': '09:00', 'end_time': '17:00', 'shift_name': 'Morning'},
        {'date': '2025-10-07', 'start_time': '10:00', 'end_time': '18:00', 'shift_name': 'Day'},
        {'date': '2025-10-08', 'start_time': None, 'end_time': None, 'shift_name': None},
    ]

def get_break_duration_for_shift(roster_id, shift_date_str, conn):
    attendance_id = get_actual_attendance_id(roster_id, conn)
    if attendance_id is None:
        return 0.0
    attendance_date_row = conn.execute(
        "SELECT date FROM attendance WHERE id=?", (attendance_id,)
    ).fetchone()

    correct_date_str = attendance_date_row['date'] if attendance_date_row and attendance_date_row[
        'date'] else shift_date_str

    total_break_minutes = 0.0
    break_records = conn.execute(
        "SELECT break_start, break_end FROM breaks WHERE attendance_id=? AND break_end IS NOT NULL ORDER BY break_start",
        (attendance_id,)
    ).fetchall()
    for record in break_records:
        raw_start_ts = record['break_start']
        raw_end_ts = record['break_end']
        if not raw_start_ts or not raw_end_ts: continue
        try:
            start_time_str = str(raw_start_ts).split()[-1][:5]
            end_time_str = str(raw_end_ts).split()[-1][:5]
            start_dt = datetime.strptime(f"{correct_date_str} {start_time_str}", '%Y-%m-%d %H:%M')
            end_dt = datetime.strptime(f"{correct_date_str} {end_time_str}", '%Y-%m-%d %H:%M')

            if end_dt < start_dt: end_dt += timedelta(days=1)

            diff = (end_dt - start_dt).total_seconds() / 60.0
            total_break_minutes += max(0.0, diff)

        except Exception as e:
            continue
    rounded_minutes = round(max(0.0, total_break_minutes), 0)
    return rounded_minutes

def get_roster_details(roster_id, conn):
    details = conn.execute(
        "SELECT emp_id, date FROM roster WHERE id=?",
        (roster_id,)
    ).fetchone()
    if details:
        return details['emp_id'], details['date']
    return None, None
def get_actual_attendance_id(roster_id, conn):
    if roster_id is None:
        return None
    roster_details = conn.execute(
        "SELECT emp_id, date FROM roster WHERE id=?",
        (roster_id,)
    ).fetchone()

    if not roster_details:
        return None

    emp_id = roster_details['emp_id']
    shift_date = roster_details['date']
    attendance_row = conn.execute(
        "SELECT id FROM attendance WHERE emp_id=? AND date=?",
        (emp_id, shift_date)
    ).fetchone()

    if attendance_row:
        return attendance_row['id']
    return None

def attempt_fix_break_linkage(attendance_id, shift_date, conn):
    if attendance_id is None:
        return 0

    unlinked_breaks = conn.execute(
        """
        SELECT id FROM breaks 
        WHERE 
            (attendance_id IS NULL OR attendance_id != ?)
            AND break_start LIKE ?      
            AND break_end IS NOT NULL
        """,
        (attendance_id, f"{shift_date}%")
    ).fetchall()

    if unlinked_breaks:
        unlinked_ids = [row['id'] for row in unlinked_breaks]
        id_list_str = ','.join(map(str, unlinked_ids))
        conn.execute(
            f"UPDATE breaks SET attendance_id = ? WHERE id IN ({id_list_str})",
            (attendance_id,)
        )
        conn.commit()
        return len(unlinked_ids)
    return 0
def get_all_employees_for_roster(conn):
    return conn.execute("SELECT emp_id, name FROM employees ORDER BY name").fetchall()

def get_roster_for_week(start_date, employee_ids):
    week_data = {emp_id: {} for emp_id in employee_ids}
    if 1 in employee_ids:
        week_data[1] = {
            'Mon': '08:00-16:00',
            'Tue': '08:00-16:00',
            'Wed': 'OFF',
            'Thu': '10:00-18:00',
            'Fri': '10:00-18:00',
            'Sat': 'OFF',
            'Sun': 'OFF'
        }
    return week_data

def save_roster_data(roster_data, week_start_date):
    print(f"Attempting to save roster for week starting: {week_start_date}")
    return True

@app.route("/admin/roster", methods=['GET'])
def admin_roster_index():
    conn = get_db_connection()
    employees_for_roster = []

    try:
        employees_for_roster = get_all_employees_for_roster(conn)
        print("Employees Fetched:", employees_for_roster)
        total_employees = len(employees_for_roster)
        clocked_in_count = 0
        return render_template(
            "admin_roster.html",
            employees=employees,
            currently_clocked_in=clocked_in_count

        )

    except Exception as e:
        print(f"Error in admin_roster_index: {e}")
        return "An internal server error occurred. See server logs for details.", 500

    finally:
        if conn:
            conn.close()

@app.route("/employee/history/<int:emp_id>")
def employee_history(emp_id):
    if 'emp_id' not in session or int(session.get('emp_id')) != emp_id:
        flash("Access denied. Please log in.", "warning")
        return redirect(url_for("home"))

    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    PER_PAGE = 10
    page = request.args.get('page', 1, type=int)
    offset = (page - 1) * PER_PAGE
    now = datetime.now()
    end_date = now.strftime("%Y-%m-%d")
    start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    logs = []
    total_pages = 1
    employee_details = {'name': 'Employee', 'emp_id': emp_id}
    try:
        employee_row = conn.execute("SELECT * FROM employees WHERE emp_id=?", (emp_id,)).fetchone()
        if employee_row:
            employee_details = dict(employee_row)
        total_logs_query = conn.execute(
            """SELECT COUNT(*) FROM attendance 
               WHERE emp_id=? AND date BETWEEN ? AND ?""",
            (emp_id, start_date, end_date)
        ).fetchone()

        total_logs = total_logs_query[0] if total_logs_query and total_logs_query[0] is not None else 0
        total_pages = (total_logs + PER_PAGE - 1) // PER_PAGE if total_logs > 0 else 1
        history_logs = conn.execute(
            """SELECT id, date, clock_in, clock_out
               FROM attendance 
               WHERE emp_id=? AND date BETWEEN ? AND ? 
               ORDER BY date DESC, clock_in DESC 
               LIMIT ? OFFSET ?""",
            (emp_id, start_date, end_date, PER_PAGE, offset)
        ).fetchall()
        logs = []
        for row in history_logs:
            log_dict = dict(row)
            log_date = log_dict.get('date', now.strftime("%Y-%m-%d"))
            duration_display = "0h 0m"
            if log_dict.get('clock_in') and log_dict.get('clock_out'):
                duration_minutes = calculate_time_diff(log_dict['clock_in'], log_dict['clock_out'], log_date)
                duration_display = format_minutes_to_hours_and_minutes(duration_minutes)
            logs.append({
                'date': log_dict.get('date', ''),
                'clock_in': log_dict.get('clock_in') or '',
                'clock_out': log_dict.get('clock_out') or '',
                'duration_display': duration_display
            })

    except Exception as e:
        print(f"Error retrieving history for EMP ID {emp_id}: {e}", file=sys.stderr)
        flash("Error retrieving history. Check server logs.", 'danger')
        logs = []
        total_pages = 1
    conn.close()

    return render_template(
        "employee_history.html",
        employee=employee_details,  # Now guaranteed to have 'emp_id'
        datetime=datetime,
        emp_id=emp_id,
        logs=logs,
        start_date=start_date,
        end_date=end_date,
        employee_name=employee_details['name'],
        current_page=page,
        total_pages=total_pages
    )

@app.route("/employee/profile/<int:emp_id>", methods=["GET", "POST"])
def employee_profile(emp_id):
    if 'emp_id' not in session or int(session.get('emp_id')) != emp_id:
        flash("Access denied. Please log in.", "warning")
        return redirect(url_for("home"))
    conn = get_db_connection()
    employee_data = conn.execute("SELECT * FROM employees WHERE emp_id=?", (emp_id,)).fetchone()
    if request.method == "POST":
        name = request.form.get("name")
        phone_number = request.form.get("phone_number")
        email = request.form.get("email")
        address = request.form.get("address")
        if not name:
            flash("Name is required.", "error")
        else:
            try:
                conn.execute(
                    """UPDATE employees 
                       SET name=?, phone_number=?, email=?, address=? 
                       WHERE emp_id=?""",
                    (name, phone_number, email, address, emp_id)
                )
                conn.commit()
                flash("✅ Profile updated successfully!", "success")
                conn.close()
                return redirect(url_for("employee_profile", emp_id=emp_id))
            except sqlite3.Error as e:
                flash(f"Database error: {e}", "error")
                conn.close()
                return redirect(url_for("employee_profile", emp_id=emp_id))
            conn.close()
    return render_template("employee_profile.html", emp_id=emp_id, employee=dict(employee_data))

@app.route("/admin/panel")
def admin_panel():
    if 'admin_user' not in session:
        flash("Access denied. Please log in as admin.", "error")
        return redirect(url_for("home"))
    return render_template("admin.html")

@app.route("/admin/add_employee", methods=["GET", "POST"])
def add_employee():
    if 'admin_user' not in session:
        flash("Access denied. Please log in as admin.", "error")
        return redirect(url_for("home"))

    if request.method == "POST":
        emp_id = request.form.get("emp_id")
        name = request.form.get("name")
        password = request.form.get("password")
        employee_type = request.form.get("employee_type")
        phone_number = request.form.get("phone_number")
        email = request.form.get("email")
        address = request.form.get("address")
        is_supervisor_status = request.form.get("is_supervisor")
        is_supervisor_db = 1 if is_supervisor_status == 'Yes' else 0
        if not emp_id or not name or not password or not employee_type:
            flash("Employee ID, Name, Password, and Employee Type are all required.", "error")
            return render_template(
                "admin_add_employee.html",
                name=name,
                password=password,
                emp_id=emp_id,
                employee_type=employee_type,
                is_supervisor=is_supervisor_status,  # For re-checking the box
                phone_number=phone_number,
                email=email,
                address=address
            )
        conn = get_db_connection()
        try:
            existing_employee = conn.execute(
                "SELECT emp_id FROM employees WHERE emp_id=?",
                (emp_id,)
            ).fetchone()
            if existing_employee:
                flash(f"🛑 Error: Employee ID **{emp_id}** already exists. Please choose a unique ID.", "error")
                return render_template(
                    "admin_add_employee.html",
                    name=name,
                    password=password,
                    emp_id=emp_id,
                    employee_type=employee_type,
                    is_supervisor=is_supervisor_status,
                    phone_number=phone_number,
                    email=email,
                    address=address
                )
            conn.execute(
                """INSERT INTO employees (emp_id, name, password, employee_type, is_supervisor, phone_number, email, address) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (emp_id, name, password, employee_type, is_supervisor_db, phone_number, email, address)
            )
            conn.commit()
            status_text = "Supervisor" if is_supervisor_db else employee_type
            flash(f"✅ Employee **{name}** ({status_text}) added successfully with ID: **{emp_id}**.", "success")
            return redirect(url_for("admin_panel"))
        except sqlite3.Error as e:
            flash(f"Database error while adding employee: {e}", "error")
        finally:
            conn.close()
    return render_template("admin_add_employee.html", name="", password="", emp_id="", employee_type="",
                           is_supervisor=None, phone_number="", email="", address="")
def get_all_attendance_logs_for_week(conn, report_start_date, report_end_date):

    query = """
    SELECT 
        a.id AS attendance_id, 
        a.emp_id, 
        a.date, 
        a.clock_in, 
        a.clock_out,
        -- Calculate and sum break minutes for this specific attendance log
        COALESCE(SUM(CAST((JULIANDAY(b.break_end) - JULIANDAY(b.break_start)) * 1440 AS REAL)), 0) AS total_break_minutes
    FROM attendance a
    LEFT JOIN breaks b ON a.id = b.attendance_id AND b.break_end IS NOT NULL
    WHERE a.date BETWEEN ? AND ? AND a.clock_out IS NOT NULL
    GROUP BY a.id, a.emp_id, a.date, a.clock_in, a.clock_out
    """
    return conn.execute(query, (report_start_date, report_end_date)).fetchall()
@app.route("/admin/report/weekly")
@app.route("/admin/report/weekly/page/<int:page>")
def weekly_report(page=1):
    if 'admin_user' not in session:
        return redirect(url_for("home"))
    conn = get_db_connection()
    try:
        week_offset = int(request.args.get('week', 0))
    except ValueError:
        week_offset = 0
    today = datetime.now().date()
    current_monday = today - timedelta(days=today.weekday())
    start_date = current_monday + timedelta(weeks=week_offset)
    end_date = start_date + timedelta(days=6)
    report_start_date = start_date.strftime('%Y-%m-%d')
    report_end_date = end_date.strftime('%Y-%m-%d')

    all_employees = conn.execute("SELECT emp_id, name FROM employees ORDER BY emp_id").fetchall()
    all_logs_raw = get_all_attendance_logs_for_week(conn, report_start_date, report_end_date)

    weekly_totals = {}
    for emp in all_employees:
        weekly_totals[emp['emp_id']] = {
            'name': emp['name'],
            'shifts_completed': 0,
            'total_net_minutes': 0.0,
            'total_break_minutes': 0.0
        }

    for log in all_logs_raw:
        emp_id = log['emp_id']
        date_str = log['date']
        gross_minutes = calculate_time_diff(log['clock_in'], log['clock_out'], date_str)
        break_minutes = log['total_break_minutes']
        net_minutes = max(0.0, gross_minutes - break_minutes)

        if emp_id in weekly_totals:
            weekly_totals[emp_id]['shifts_completed'] += 1
            weekly_totals[emp_id]['total_net_minutes'] += net_minutes
            weekly_totals[emp_id]['total_break_minutes'] += break_minutes

    all_weekly_data = []
    for emp_id, data in weekly_totals.items():
        total_net_hours = round(data['total_net_minutes'] / 60.0, 2)
        total_break_hours = round(data['total_break_minutes'] / 60.0, 2)
        all_weekly_data.append({
            'emp_id': emp_id,
            'name': data['name'],
            'shifts_completed': data['shifts_completed'],
            'total_net_work_hours': total_net_hours,
            'total_break_hours': total_break_hours
        })

    total_employees = len(all_weekly_data)
    total_pages = math.ceil(total_employees / RECORDS_PER_PAGE)
    offset = (page - 1) * RECORDS_PER_PAGE
    weekly_data_paged = all_weekly_data[offset:offset + RECORDS_PER_PAGE]
    conn.close()

    return render_template(
        "weekly_report.html",
        weekly_data=weekly_data_paged,
        report_start_date=report_start_date,
        report_end_date=report_end_date,
        page=page,
        total_pages=total_pages,
        pages=range(1, total_pages + 1),
        current_week_offset=week_offset
    )

@app.route("/logout")
def logout():
    session.clear()
    flash("You have successfully logged out.", "success")
    response = make_response(redirect(url_for("home")))
    response.set_cookie('session', '', expires=0)
    return response

@app.route("/admin/logs/edit/<int:log_id>", methods=["GET", "POST"])
def edit_log(log_id):
    if 'admin_user' not in session:
        flash("Access denied. Please log in as admin.", "error")
        return redirect(url_for("home"))

    conn = get_db_connection()
    log = conn.execute("SELECT * FROM attendance WHERE id=?", (log_id,)).fetchone()
    breaks = conn.execute("SELECT id, break_start, break_end FROM breaks WHERE attendance_id=? ORDER BY break_start",
                          (log_id,)).fetchall()

    if log is None:
        flash(f"Error: Log ID {log_id} not found.", "error")
        conn.close()
        return redirect(url_for("admin_logs"))

    if request.method == "POST":
        clock_in = request.form["clock_in"]
        clock_out = request.form["clock_out"]
        conn.execute(
            "UPDATE attendance SET clock_in=?, clock_out=? WHERE id=?",
            (clock_in, clock_out, log_id)
        )
        for b in breaks:
            break_id = b['id']
            new_start = request.form.get(f"break_start_{break_id}")
            new_end = request.form.get(f"break_end_{break_id}")
            new_end_db = new_end if new_end else None
            conn.execute(
                "UPDATE breaks SET break_start=?, break_end=? WHERE id=?",
                (new_start, new_end_db, break_id)
            )
        new_break_start = request.form.get("new_break_start")
        new_break_end = request.form.get("new_break_end")

        if new_break_start:
            new_break_end_db = new_break_end if new_break_end else None
            conn.execute(
                "INSERT INTO breaks (attendance_id, break_start, break_end) VALUES (?, ?, ?)",
                (log_id, new_break_start, new_break_end_db)
            )
            flash("New break added.", "info")
        conn.commit()
        conn.close()
        flash(f"✅ Log ID {log_id} and associated breaks successfully updated.", "success")
        return redirect(url_for("admin_logs"))
    conn.close()
    return render_template("edit_log.html", log_id=log_id, log=log, breaks=breaks)

@app.route("/admin/logs/delete/<int:log_id>")
def delete_log(log_id):
    if 'admin_user' not in session:
        return redirect(url_for("home"))

    conn = get_db_connection()
    conn.execute("DELETE FROM breaks WHERE attendance_id=?", (log_id,))
    conn.execute("DELETE FROM attendance WHERE id=?", (log_id,))
    conn.commit()
    conn.close()
    flash(f"🗑️ Log ID {log_id} successfully deleted.", "success")
    return redirect(url_for("admin_logs"))

@app.route('/admin/employee/delete/<int:emp_id>', methods=['POST'])
def delete_employee(emp_id):
    if 'admin_user' not in session:
        flash("Access denied. Please log in as an administrator.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute(
            "DELETE FROM breaks WHERE attendance_id IN (SELECT id FROM attendance WHERE emp_id=?)",
            (emp_id,)
        )
        conn.execute("DELETE FROM attendance WHERE emp_id=?", (emp_id,))
        conn.execute("DELETE FROM employees WHERE emp_id=?", (emp_id,))
        conn.commit()
        flash(f"🗑️ Employee (ID: {emp_id}) and all associated data deleted permanently.", "success")
    except sqlite3.Error as e:
        conn.rollback()
        flash(f"Database error during deletion: {e}", "error")
    except Exception as e:
        conn.rollback()
        flash(f"An unexpected error occurred: {e}", "error")
    finally:
        conn.close()
    return redirect(url_for("manage_employees"))

@app.route("/admin/employee/delete/<int:emp_id>", methods=['POST'])
def admin_employee_delete(emp_id):
    if not session.get('is_admin'):
        flash("Admin privileges required to delete an employee.", "error")
        return redirect(url_for("home"))
    if request.method == 'POST':
        try:
            conn = sqlite3.connect('attendance.db')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM employees WHERE emp_id=?", (emp_id,))
            conn.commit()
            conn.close()
            flash(f"Employee ID {emp_id} successfully deleted. 🗑️", "success")
        except sqlite3.Error as e:
            flash(f"Database error during deletion: {e}", "danger")
            conn.rollback()
        finally:
            if conn:
                conn.close()
    return redirect(url_for('admin_roster_index'))
CLOCK_DATE_COL = 'date'
CLOCK_TIME_COL = 'clock_in'
CLOCK_OUT_COL = 'clock_out'

@app.route("/admin/logs")
@app.route("/admin/logs/page/<int:page>")
def admin_logs(page=1):
    return _admin_log_fetcher(page=page, filter_type='today')


@app.route("/admin/history")
@app.route("/admin/history/page/<int:page>")
def admin_history(page=1):
    return _admin_log_fetcher(page=page, filter_type='all')


def _admin_log_fetcher(page=1, filter_type='today'):
    if 'admin_user' not in session:
        return redirect(url_for("home"))
    conn = None
    logs = []

    MIN_SHIFT_DURATION_MINUTES = 1
    MIN_COMPLETED_HOURS_MINUTES = 240
    MAX_COMPLETED_HOURS_MINUTES = 600

    RECORDS_PER_PAGE_LOCAL = globals().get('RECORDS_PER_PAGE', 20)
    TARGET_LAT_LOCAL = globals().get('TARGET_LAT', None)
    TARGET_LON_LOCAL = globals().get('TARGET_LON', None)
    CLOCK_DATE_COL_LOCAL = globals().get('CLOCK_DATE_COL', 'date')
    today_date_str = datetime.now().strftime('%Y-%m-%d')
    offset = (page - 1) * RECORDS_PER_PAGE_LOCAL

    if filter_type == 'today':
        where_condition = f"WHERE a.{CLOCK_DATE_COL_LOCAL} = ?"
        filter_value = (today_date_str,)
        page_title = "Today's Attendance Logs"
    else:
        where_condition = ""
        filter_value = ()
        page_title = "Full Attendance History (All Logs)"
    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        total_logs_row = conn.execute(
            f"SELECT COUNT(*) FROM attendance a {where_condition}",
            filter_value
        ).fetchone()
        total_logs = total_logs_row[0] if total_logs_row else 0
        total_pages = math.ceil(total_logs / RECORDS_PER_PAGE_LOCAL) if total_logs > 0 else 1
        if page < 1:
            page = 1
        if total_pages > 0 and page > total_pages:
            page = total_pages
            offset = (page - 1) * RECORDS_PER_PAGE_LOCAL
        elif total_pages == 0:
            page = 1
            offset = 0
        logs_raw = conn.execute(f"""
            SELECT 
                a.id, a.emp_id, a.date, a.clock_in, a.clock_out,
                a.latitude AS clock_out_lat, a.longitude AS clock_out_lon, 
                a.employee_note, a.note, a.clock_in_lat, a.clock_in_lon,
                e.name 
            FROM attendance a 
            JOIN employees e ON CAST(a.emp_id AS INTEGER) = CAST(e.emp_id AS INTEGER)
            {where_condition} 
            ORDER BY a.date DESC, a.clock_in DESC
            LIMIT {RECORDS_PER_PAGE_LOCAL} OFFSET {offset}
        """, filter_value).fetchall()

        for row in logs_raw:
            log = dict(row)
            current_date_str = log[CLOCK_DATE_COL_LOCAL]
            log['work_duration'] = '0h 0m'
            log['gross_duration'] = '0h 0m'
            log['total_break_minutes'] = 0
            log['location_status_text'] = 'N/A'
            log['work_status'] = "Incomplete Shift"
            log['status_class'] = "status-incomplete"
            log['breaks'] = []
            total_break_minutes = 0
            breaks_raw = conn.execute(
                "SELECT break_start, break_end FROM breaks WHERE attendance_id=? ORDER BY break_start",
                (log['id'],)
            ).fetchall()
            for b in breaks_raw:
                b_dict = dict(b)
                break_minutes = 0
                if b_dict.get('break_start') and b_dict.get('break_end'):
                    break_minutes = calculate_time_diff(
                        b_dict['break_start'],
                        b_dict['break_end'],
                        current_date_str
                    )
                total_break_minutes += break_minutes
                if b_dict.get('break_end') and break_minutes > 0:
                    b_dict['duration'] = format_minutes_to_hours_and_minutes(break_minutes)
                elif b_dict.get('break_end') is None:
                    b_dict['duration'] = "ACTIVE"
                else:
                    b_dict['duration'] = ""
                log['breaks'].append(b_dict)
            log['total_break_minutes'] = int(round(total_break_minutes, 0))
            if log.get('clock_in') and log.get('clock_out'):
                gross_work_minutes = calculate_time_diff(log['clock_in'], log['clock_out'], current_date_str)
                net_work_minutes = gross_work_minutes - total_break_minutes

                net_h_str, gross_h_str = calculate_shift_hours(
                    log['clock_in'],
                    log['clock_out'],
                    total_break_minutes,
                    current_date_str
                )

                log['net_duration'] = net_h_str
                log['gross_duration'] = gross_h_str
                log['shift_duration'] = gross_h_str
                if gross_work_minutes <= MIN_SHIFT_DURATION_MINUTES:
                    log['work_status'] = "Incomplete Shift"
                    log['status_class'] = "status-incomplete"
                elif net_work_minutes > MAX_COMPLETED_HOURS_MINUTES:
                    log['work_status'] = "Over 10 hours net (Warning)"
                    log['status_class'] = "status-warning-over"
                elif MIN_COMPLETED_HOURS_MINUTES <= net_work_minutes <= MAX_COMPLETED_HOURS_MINUTES:
                    log['work_status'] = "COMPLETED"
                    log['status_class'] = "status-completed"
                elif net_work_minutes < MIN_COMPLETED_HOURS_MINUTES:
                    log['work_status'] = "Under 4 hours net (Warning)"
                    log['status_class'] = "status-warning-under"

                else:
                    log['work_status'] = "Incomplete Shift"
                    log['status_class'] = "status-incomplete"
            elif log.get('clock_in') and not log.get('clock_out'):
                log['work_status'] = "Shift Active"
                log['status_class'] = "status-active"

            lat_to_check = log.get('clock_out_lat')
            lon_to_check = log.get('clock_out_lon')
            if (lat_to_check and lon_to_check and
                    TARGET_LAT_LOCAL is not None and TARGET_LON_LOCAL is not None):
                try:
                    distance = haversine_distance(TARGET_LAT_LOCAL, TARGET_LON_LOCAL, float(lat_to_check),
                                                  float(lon_to_check))
                    if distance > 50:
                        log['location_status_text'] = f"Outside Zone ({distance:.0f}m away)"
                    else:
                        log['location_status_text'] = "In Zone"
                except (ValueError, TypeError, NameError):
                    log['location_status_text'] = "Data Error"
            else:
                log['location_status_text'] = "N/A"
            logs.append(log)
    except Exception as e:
        import sys
        import traceback
        print(f"ADMIN LOGS CRITICAL ERROR: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        flash(f"Error loading logs: {e}", "error")
    finally:
        if conn:
            conn.close()
    return render_template(
        "admin_logs.html",
        logs=logs,
        page=page,
        total_pages=total_pages,
        pages=range(1, total_pages + 1),
        current_route=request.endpoint,
        filter_type=filter_type,
        page_title=page_title
    )

@app.route("/admin/delete_all_requests", methods=['POST'])
def admin_delete_all_requests():
    if 'admin_user' not in session:
        flash("Access denied.", "danger")
        return redirect(url_for("home"))
    if request.form.get('confirm_delete') != 'DELETE_ALL_REQUESTS':
        flash("Confirmation failed. All requests were NOT deleted.", "warning")
        return redirect(url_for("manage_requests"))

    conn = None
    try:
        conn = get_db_connection()
        conn.execute("DELETE FROM employee_requests")

        conn.commit()

        flash("SUCCESS! All employee requests have been permanently deleted.", "success")

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"CRITICAL REQUEST DELETION ERROR: {e}", file=sys.stderr)
        flash(f"Error deleting requests: {e}. Changes were rolled back.", "danger")
    finally:
        if conn:
            conn.close()

    return redirect(url_for("manage_requests"))

@app.route("/admin/delete_all_logs", methods=['POST'])
def admin_delete_all_logs():
    if 'admin_user' not in session:
        flash("Access denied.", "danger")
        return redirect(url_for("home"))

    if request.form.get('confirm_delete') != 'DELETE_ALL_LOGS':
        flash("Confirmation failed. All logs were NOT deleted.", "warning")
        return redirect(url_for("admin_history"))

    conn = None
    try:
        conn = get_db_connection()
        conn.execute("BEGIN TRANSACTION")
        conn.execute("DELETE FROM breaks")
        conn.execute("DELETE FROM attendance")
        conn.commit()
        flash("SUCCESS! All attendance and break logs have been permanently deleted.", "success")
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"CRITICAL LOG DELETION ERROR: {e}", file=sys.stderr)
        flash(f"Error deleting logs: {e}. Changes were rolled back.", "danger")
    finally:
        if conn:
            conn.close()
    return redirect(url_for("admin_logs"))

def get_full_timestamp(time_value, date_str):
    if time_value is None or str(time_value).strip() == '':
        return None
    cleaned_time = str(time_value).strip()
    time_part = cleaned_time.split()[-1][:5]
    if date_str:
        return f"{date_str} {time_part}"
    return time_part

@app.route("/admin/requests")
def manage_requests():
    """Admin view to see all employee requests."""
    if 'admin_user' not in session:
        flash("Access denied.", "error")
        return redirect(url_for("home"))

    conn = get_db_connection()
    requests = conn.execute(
        """SELECT r.*, e.name 
           FROM employee_requests r 
           JOIN employees e ON r.emp_id = e.emp_id 
           ORDER BY CASE 
             WHEN r.status = 'Pending' THEN 1 
             ELSE 2 
           END, r.date_submitted DESC"""
    ).fetchall()
    conn.close()
    return render_template("admin_manage_requests.html", requests=requests)

@app.route("/admin/request/process/<int:request_id>", methods=['POST'])
def process_request(request_id):
    if 'admin_user' not in session:
        flash("Access denied.", "error")
        return redirect(url_for("home"))
    action = request.form.get("action")
    admin_response = request.form.get("admin_response")

    if action not in ['Accepted', 'Rejected']:
        flash("Invalid action.", "error")
        return redirect(url_for("manage_requests"))

    conn = get_db_connection()
    try:
        conn.execute(
            """UPDATE employee_requests 
               SET status=?, admin_response=? 
               WHERE id=?""",
            (action, admin_response, request_id)
        )
        conn.commit()
        flash(f"✅ Request ID {request_id} successfully marked as **{action}**.", "success")
    except sqlite3.Error as e:
        flash(f"Database error while processing request: {e}", "error")
    finally:
        conn.close()
    return redirect(url_for("manage_requests"))

@app.route("/admin/change_password", methods=["GET", "POST"])
def change_admin_password():
    if 'admin_user' not in session:
        flash("Access denied.", "error")
        return redirect(url_for("home"))
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")
        conn = get_db_connection()
        admin_record = conn.execute("SELECT password FROM admins WHERE username='admin'").fetchone()
        if not admin_record or admin_record['password'] != current_password:
            conn.close()
            flash("🛑 Error: Incorrect current password.", "error")
            return render_template("admin_change_password.html")
        if new_password != confirm_password:
            conn.close()
            flash("🛑 Error: New password and confirmation do not match.", "error")
            return render_template("admin_change_password.html")
        conn.execute("UPDATE admins SET password=? WHERE username='admin'", (new_password,))
        conn.commit()
        conn.close()
        flash("✅ Admin password successfully updated!", "success")
        return redirect(url_for("admin_panel"))
    return render_template("admin_change_password.html")

@app.route("/admin/manage_roster", methods=["GET", "POST"])
def admin_manage_roster():
    if not session.get('is_admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    if request.method == "POST":
        action = request.form.get("action")
        if action == 'add_shift':
            conn.close()
            return redirect(url_for('admin_manage_roster'))
    employees = conn.execute(
        "SELECT emp_id, name, employee_type, is_supervisor, phone_number, email FROM employees ORDER BY name"
    ).fetchall()
    try:
        clocked_in_count = conn.execute(
            "SELECT COUNT(DISTINCT emp_id) FROM attendance WHERE clock_out IS NULL"
        ).fetchone()[0]
    except:
        clocked_in_count = 0
    conn.close()
    return render_template(
        "admin_roster.html",  # Renders the Employee List View
        employees=employees,
        currently_clocked_in=clocked_in_count
    )

@app.route("/admin/roster/delete/<int:shift_id>", methods=["POST"])
def admin_delete_shift(shift_id):
    if not session.get('is_admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM roster WHERE id=?", (shift_id,))
        conn.commit()
        flash("Shift deleted successfully! 🗑️", "success")
    except Exception as e:
        flash(f"Error deleting shift: {e}", "error")
    finally:
        conn.close()
    return redirect(url_for('admin_manage_roster'))

@app.route("/admin/roster/edit/<int:shift_id>", methods=["GET", "POST"])
def admin_edit_shift(shift_id):
    if not session.get('is_admin'):
        flash("Admin access required.", "error")
        return redirect(url_for("home"))
    conn = get_db_connection()
    shift = conn.execute("SELECT * FROM roster WHERE id=?", (shift_id,)).fetchone()
    employees = conn.execute("SELECT emp_id, name FROM employees ORDER BY name").fetchall()

    if shift is None:
        flash("Shift not found.", "error")
        conn.close()
        return redirect(url_for('admin_manage_roster'))

    if request.method == "POST":
        emp_id = request.form.get("emp_id")
        date = request.form.get("date")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
        shift_name = request.form.get("shift_name")

        try:
            conn.execute(
                """UPDATE roster SET emp_id=?, date=?, start_time=?, end_time=?, shift_name=?
                   WHERE id=?""",
                (emp_id, date, start_time, end_time, shift_name, shift_id)
            )
            conn.commit()
            flash("Shift updated successfully! ✏️", "success")
            conn.close()
            return redirect(url_for('admin_manage_roster'))
        except sqlite3.IntegrityError:
            flash("Error: That employee already has a shift on that date.", "error")
        except Exception as e:
            flash(f"Error updating shift: {e}", "error")
        conn.close()
        return redirect(url_for('admin_edit_shift', shift_id=shift_id))
    conn.close()
    return render_template("admin_roster_editor.html", shift=dict(shift), employees=employees)

@app.route("/admin/roster/edit", methods=['POST'])
def admin_roster_editor_post():
    flash("Roster saving feature is currently disabled.", "info")
    week_start_date = request.form.get('week_start_date')
    return redirect(url_for('admin_roster_editor', week_offset=0))

def get_roster_for_week(conn, start_date, employee_ids):
    if not employee_ids:
        return {}
    end_date = start_date + timedelta(days=6)
    start_date_str = start_date.strftime('%Y-%m-%d')
    end_date_str = end_date.strftime('%Y-%m-%d')
    id_placeholders = ','.join('?' for _ in employee_ids)
    query = f"""
        SELECT 
            emp_id, 
            date, 
            start_time, 
            end_time,
            shift_name
        FROM roster
        WHERE date BETWEEN ? AND ?
        AND emp_id IN ({id_placeholders})
        ORDER BY date, emp_id
    """

    params = (start_date_str, end_date_str) + tuple(employee_ids)
    conn.row_factory = sqlite3.Row
    shifts = conn.execute(query, params).fetchall()
    roster_data = {}

    for emp_id in employee_ids:
        roster_data[emp_id] = {}

    for shift in shifts:
        emp_id = shift['emp_id']
        shift_date = datetime.strptime(shift['date'], '%Y-%m-%d')
        day_name = shift_date.strftime('%a')
        shift_time = ""

        if shift['start_time'] and shift['end_time']:
            shift_time = f"{shift['start_time']}-{shift['end_time']}"
        elif shift['shift_name'] and shift['shift_name'].upper() == 'OFF':
            shift_time = 'OFF'
        elif shift['shift_name']:
            shift_time = shift['shift_name']
        roster_data[emp_id][day_name] = shift_time
    return roster_data

@app.route('/admin/dashboard', methods=['GET', 'POST'])
@app.route('/admin/dashboard', methods=['GET', 'POST'])
def admin_dashboard():
    if not session.get('logged_in') or session.get('role') != 'admin':
        flash('Access denied. Please log in with admin credentials.', 'error')
        return redirect(url_for('home'))
    conn = None
    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        total_employees = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        pending_requests = conn.execute(
            """SELECT 
                r.id, e.name as employee_name, r.request_type, r.date_submitted
            FROM employee_requests r 
            JOIN employees e ON r.emp_id = e.emp_id 
            WHERE r.status='Pending'                  
            ORDER BY r.date_submitted DESC 
            LIMIT 10"""
        ).fetchall()
        active_shifts = conn.execute(
            """SELECT 
                a.clock_in, e.name  
            FROM attendance a 
            JOIN employees e ON a.emp_id = e.emp_id 
            WHERE a.clock_out IS NULL 
            ORDER BY a.clock_in DESC"""
        ).fetchall()
        return render_template(
            "admin_dashboard.html",
            total_employees=total_employees,
            pending_requests=pending_requests,
            active_shifts=active_shifts,
        )
    except Exception as e:
        print(f"An error occurred in admin_dashboard: {e}")
        flash("An internal error occurred while loading the dashboard.", "error")
        return redirect(url_for('home'))

    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

###if __name__ == "__main__":
 ####   initialize_db()
  ###  app.run(host='0.0.0.0', port=5000, debug=True)