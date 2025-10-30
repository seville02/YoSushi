def attempt_fix_break_linkage(attendance_id, emp_id, shift_date, conn):
    """
    Attempts to link any unassigned break records to the correct attendance_id.
    This fixes the 'Found 1 break records' issue if breaks were created with a NULL or
    incorrect attendance_id due to a previous bug.
    """
    if attendance_id is None:
        return 0

    # 1. Look for break records that belong to this employee and date but are UNLINKED (NULL attendance_id)
    # NOTE: Assuming 'breaks' table has an 'emp_id' column for this check. If not, you must JOIN.
    # We will JOIN with the attendance table to be safe.

    # Get the list of all valid break IDs that SHOULD belong to this employee on this date
    # but are currently missing the correct attendance_id.

    # We select breaks whose start time falls on the shift date and whose end time is also on or after the shift date,
    # but which are NOT linked to the current attendance_id.
    unlinked_breaks = conn.execute(
        """
        SELECT b.id FROM breaks b
        JOIN employees e ON e.emp_id = ?  -- Join condition to filter by the correct employee
        WHERE 
            (b.attendance_id IS NULL OR b.attendance_id != ?)
            AND b.break_start LIKE ?      -- Break starts on the shift date
            AND b.break_end IS NOT NULL
        """,
        (emp_id, attendance_id, f"{shift_date}%")
    ).fetchall()

    # 2. Update the unlinked breaks to the correct attendance_id
    if unlinked_breaks:
        unlinked_ids = [row['id'] for row in unlinked_breaks]

        # Convert list of IDs to a comma-separated string for the SQL IN clause
        id_list_str = ','.join(map(str, unlinked_ids))

        conn.execute(
            f"UPDATE breaks SET attendance_id = ? WHERE id IN ({id_list_str})",
            (attendance_id,)
        )
        conn.commit()
        print(f"DEBUG: FIXED {len(unlinked_ids)} unlinked breaks for ATTENDANCE ID {attendance_id}.")
        return len(unlinked_ids)

    return 0