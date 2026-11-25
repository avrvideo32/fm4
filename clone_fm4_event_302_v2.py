import sqlite3
from pathlib import Path

SOURCE = Path(r"D:\code\py\fm4\gamedb.slt")
DEST = SOURCE.with_name("gamedb_ford_ka_clone_test_v2.slt")
TEMPLATE_EVENT_ID = 302


def get_columns(conn, table):
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def next_id(conn, table, id_column):
    value = conn.execute(
        f'SELECT COALESCE(MAX("{id_column}"), 0) + 1 FROM "{table}"'
    ).fetchone()[0]
    return int(value)


def copy_linked_rows(conn, table, event_column, template_event_id,
                     new_event_id, id_column):
    column_names = get_columns(conn, table)
    rows = conn.execute(
        f'SELECT * FROM "{table}" WHERE "{event_column}" = ?',
        (template_event_id,),
    ).fetchall()

    inserted = 0
    new_row_id = next_id(conn, table, id_column)

    for row in rows:
        record = dict(zip(column_names, row))
        record[id_column] = new_row_id
        record[event_column] = new_event_id
        new_row_id += 1

        names = list(record.keys())
        quoted_names = ", ".join(f'"{name}"' for name in names)
        placeholders = ", ".join("?" for _ in names)
        conn.execute(
            f'INSERT INTO "{table}" ({quoted_names}) '
            f'VALUES ({placeholders})',
            [record[name] for name in names],
        )
        inserted += 1

    return inserted


def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Source database not found: {SOURCE}")

    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or delete that output before running this script again."
        )

    source_uri = f"file:{SOURCE.as_posix()}?mode=ro"
    src = sqlite3.connect(source_uri, uri=True)
    dst = sqlite3.connect(DEST)

    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        dst.execute("BEGIN IMMEDIATE")

        event_columns = get_columns(dst, "Events")
        source_row = dst.execute(
            'SELECT * FROM "Events" WHERE "Id" = ?',
            (TEMPLATE_EVENT_ID,),
        ).fetchone()
        if source_row is None:
            raise RuntimeError(f"Template Event {TEMPLATE_EVENT_ID} not found")

        event = dict(zip(event_columns, source_row))
        new_event_id = next_id(dst, "Events", "Id")

        # EventOrder must be chosen within the same career type, level,
        # and group mask. Using only GroupMask placed the previous test clone
        # at order 310 because unrelated career event types share that mask.
        next_order = dst.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 '
            'FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (event["CareerTypeId"], event["Level"], event["GroupMask"]),
        ).fetchone()[0]

        event["Id"] = new_event_id
        event["EventOrder"] = int(next_order)

        names = list(event.keys())
        quoted_names = ", ".join(f'"{name}"' for name in names)
        placeholders = ", ".join("?" for _ in names)
        dst.execute(
            f'INSERT INTO "Events" ({quoted_names}) VALUES ({placeholders})',
            [event[name] for name in names],
        )

        races = copy_linked_rows(
            dst, "Races", "EventId", TEMPLATE_EVENT_ID, new_event_id, "Id"
        )
        participants = copy_linked_rows(
            dst, "EventParticipants", "EventID",
            TEMPLATE_EVENT_ID, new_event_id, "ID"
        )
        restrictions = copy_linked_rows(
            dst, "EventRestrictions", "EventID",
            TEMPLATE_EVENT_ID, new_event_id, "ID"
        )

        dst.commit()

        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        print("Created a separate test database:")
        print(DEST)
        print()
        print(f"Template event: {TEMPLATE_EVENT_ID}")
        print(f"New event ID:   {new_event_id}")
        print(f"CareerTypeId:   {event['CareerTypeId']}")
        print(f"Level:          {event['Level']}")
        print(f"GroupMask:      {event['GroupMask']}")
        print(f"EventOrder:     {next_order}")
        print(f"Races copied:   {races}")
        print(f"Participants:   {participants}")
        print(f"Restrictions:   {restrictions}")
        print(f"Integrity:      {integrity}")
        print()
        print("The original source database was not modified.")

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
