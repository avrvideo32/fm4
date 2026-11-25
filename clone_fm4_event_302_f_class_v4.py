import sqlite3
from pathlib import Path

SOURCE = Path(r"D:\code\py\fm4\gamedb.slt")
DEST = SOURCE.with_name("gamedb_f_class_clone_test_v4.slt")
TEMPLATE_EVENT_ID = 302

# Global maxima across the base game AND official FM4 DLC databases.
# Source: FMdangaina/ForzaStudio-EVO/docs/FM4/IdMax.json (generated 2026-10-31).
# The older scripts used base-database-only MAX()+1 values, which collide
# with official DLC rows (for example Events.Id 727, Races.Id 3676,
# EventRestrictions.ID 341).
DLC_GLOBAL_MAX = {
    "Events": ("Id", 748),
    "Races": ("Id", 3842),
    "EventParticipants": ("ID", 76007),
    "EventRestrictions": ("ID", 439),
}


def get_columns(conn, table):
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def safe_new_id(conn, table):
    id_column, global_max = DLC_GLOBAL_MAX[table]
    base_max = conn.execute(
        f'SELECT COALESCE(MAX("{id_column}"), 0) FROM "{table}"'
    ).fetchone()[0]
    new_id = max(int(base_max), global_max) + 1

    collision = conn.execute(
        f'SELECT 1 FROM "{table}" WHERE "{id_column}" = ? LIMIT 1',
        (new_id,),
    ).fetchone()
    if collision:
        raise RuntimeError(
            f"Unexpected ID collision: {table}.{id_column}={new_id}"
        )
    return new_id


def insert_record(conn, table, record):
    names = list(record.keys())
    quoted_names = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f'INSERT INTO "{table}" ({quoted_names}) VALUES ({placeholders})',
        [record[name] for name in names],
    )


def copy_linked_rows(conn, table, event_column, template_event_id,
                     new_event_id):
    id_column, _ = DLC_GLOBAL_MAX[table]
    new_row_id = safe_new_id(conn, table)
    cols = get_columns(conn, table)
    rows = conn.execute(
        f'SELECT * FROM "{table}" WHERE "{event_column}" = ?',
        (template_event_id,),
    ).fetchall()

    for row in rows:
        record = dict(zip(cols, row))
        record[id_column] = new_row_id
        record[event_column] = new_event_id
        insert_record(conn, table, record)

    return len(rows), new_row_id


def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Source database not found: {SOURCE}")
    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or delete the old output before running this script."
        )

    src = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(DEST)
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        dst.execute("BEGIN IMMEDIATE")

        event_cols = get_columns(dst, "Events")
        template_row = dst.execute(
            'SELECT * FROM "Events" WHERE "Id" = ?',
            (TEMPLATE_EVENT_ID,),
        ).fetchone()
        if template_row is None:
            raise RuntimeError(f"Template Event {TEMPLATE_EVENT_ID} not found")

        event = dict(zip(event_cols, template_row))
        new_event_id = safe_new_id(dst, "Events")

        # Keep the event in the same type/level/group and use the first
        # unused order within that exact combination.
        next_order = dst.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 '
            'FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (event["CareerTypeId"], event["Level"], event["GroupMask"]),
        ).fetchone()[0]

        event["Id"] = new_event_id
        event["EventOrder"] = int(next_order)
        insert_record(dst, "Events", event)

        race_count, race_id = copy_linked_rows(
            dst, "Races", "EventId", TEMPLATE_EVENT_ID, new_event_id
        )
        participant_count, participant_id = copy_linked_rows(
            dst, "EventParticipants", "EventID",
            TEMPLATE_EVENT_ID, new_event_id
        )
        # Event 302's restriction is what makes the player need the Ford Ka.
        # Omit that row in the clone. TargetClass=0 remains unchanged and
        # represents the F-class eligibility limit, allowing any F-class car.
        restriction_count = dst.execute(
            'SELECT COUNT(*) FROM "EventRestrictions" WHERE "EventID" = ?',
            (TEMPLATE_EVENT_ID,),
        ).fetchone()[0]

        dst.commit()
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]

        print("Created DLC-safe test database:")
        print(DEST)
        print()
        print(f"Template event:       {TEMPLATE_EVENT_ID}")
        print(f"New event ID:         {new_event_id}")
        print(f"CareerTypeId:         {event['CareerTypeId']}")
        print(f"Level:                {event['Level']}")
        print(f"GroupMask:            {event['GroupMask']}")
        print(f"EventOrder:           {next_order}")
        print(f"New race ID:          {race_id} ({race_count} row)")
        print(f"New participant ID:   {participant_id} ({participant_count} row)")
        print(f"Ford Ka restriction:  NOT copied ({restriction_count} source row omitted)")
        print("Eligibility:          TargetClass=0 (F class); no model-specific restriction")
        print(f"Integrity:            {integrity}")
        print()
        print("The original database was not modified.")
        print("All new primary keys are above the official-DLC global maxima.")
        print("The cloned event omits the Ford Ka-specific EventRestrictions row.")

    except Exception:
        dst.rollback()
        dst.close()
        DEST.unlink(missing_ok=True)
        raise
    finally:
        try:
            dst.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
