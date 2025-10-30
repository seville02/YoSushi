import sqlite3

conn = sqlite3.connect("attendance.db")
cur = conn.cursor()

# Create employees table
cur.execute("""
CREATE TABLE IF NOT EXISTS employees (
    emp_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    password TEXT NOT NULL
)
""")

# Create attendance table
cur.execute("""
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emp_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    clock_in TEXT,
    clock_out TEXT,
    break_start TEXT,
    break_end TEXT,
    FOREIGN KEY(emp_id) REFERENCES employees(emp_id)
)
""")

# Insert dummy employee
cur.execute("INSERT OR IGNORE INTO employees (emp_id, name, password) VALUES (1, 'John Doe', '1234')")

conn.commit()
conn.close()

print("Database and tables created successfully!")
