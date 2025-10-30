import sqlite3


def add_note_column():
    # Initialize conn to None so the finally block can safely check it
    conn = None
    try:
        # -------------------------------------------------------------
        # CRITICAL: ENSURE THIS PATH IS 100% CORRECT (SEE SECTION 2)
        # For a standard Flask app, it's often 'instance/flask.sqlite' or similar
        # -------------------------------------------------------------
        conn = sqlite3.connect('YoJapanese/attendance.db')
        cursor = conn.cursor()

        # Check if the column already exists
        cursor.execute("PRAGMA table_info(breaks)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'note' not in columns:
            cursor.execute("ALTER TABLE breaks ADD COLUMN note TEXT")
            conn.commit()
            print("SUCCESS: Added 'note' column to breaks table.")
        else:
            print("Column 'note' already exists. No change made.")

    except sqlite3.OperationalError as e:
        print(f"FATAL DATABASE ERROR: {e}")
        print("Please check the database file path is correct and the file exists.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    add_note_column()