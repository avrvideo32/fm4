import sqlite3
from pathlib import Path

SOURCE = Path(r"D:\code\py\fm4\gamedb.slt")
DEST = SOURCE.with_name("gamedb_f_class_varied_ai_test_v6.slt")

TEMPLATE_EVENT_ID = 302          # Ford Ka Spec Series: known working one-race event
F_CLASS_ROSTER_EVENT_ID = 600    # Existing 11-driver F-class event: source of AI cars

# Global maxima across the base game AND official FM4 DLC databases.
DLC_GLOBAL_MAX = {
    "Events": ("Id", 748),
    "Races": ("Id", 3842),
    "EventParticipants": ("ID", 76007),
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


def copy_races(conn, template_event_id, new_event_id):
    cols = get_columns(conn, "Races")
    rows = conn.execute(
        'SELECT * FROM "Races" WHERE "EventId" = ? ORDER BY "RaceNumber"',
        (template_event_id,),
    ).fetchall()

    new_ids = []
    for row in rows:
        record = dict(zip(cols, row))
        record["Id"] = safe_new_id(conn, "Races")
        record["EventId"] = new_event_id
        new_ids.append(record["Id"])
        insert_record(conn, "Races", record)

    return new_ids


def copy_f_class_ai_roster(conn, new_event_id, expected_class, expected_count):
    cols = get_columns(conn, "EventParticipants")
    rows = conn.execute(
        'SELECT * FROM "EventParticipants" WHERE "EventID" = ? ORDER BY "Priority"',
        (F_CLASS_ROSTER_EVENT_ID,),
    ).fetchall()

    if len(rows) != expected_count:
        raise RuntimeError(
            f"Expected {expected_count} AI roster rows from Event "
            f"{F_CLASS_ROSTER_EVENT_ID}, found {len(rows)}"
        )

    seen_ordinals = set()
    copied = []
    for row in rows:
        record = dict(zip(cols, row))
        car_id = int(record["Ordinal"])

        if car_id in seen_ordinals:
            raise RuntimeError(f"Duplicate AI car ordinal in source roster: {car_id}")
        seen_ordinals.add(car_id)

        car = conn.execute(
            'SELECT "Id", "Year", "MakeName", "DisplayName", "MediaName", '
            '"ClassID", "PerformanceIndex", "IsDrivable" '
            'FROM "Data_Car" WHERE "Id" = ?',
            (car_id,),
        ).fetchone()

        if car is None:
            raise RuntimeError(
                f"Event {F_CLASS_ROSTER_EVENT_ID} participant Ordinal "
                f"{car_id} has no matching Data_Car row"
            )

        if int(car[5]) != expected_class or int(car[7]) != 1:
            raise RuntimeError(
                f"Source AI car {car_id} is not a drivable F-class car: "
                f"ClassID={car[5]}, IsDrivable={car[7]}"
            )

        record["ID"] = safe_new_id(conn, "EventParticipants")
        record["EventID"] = new_event_id
        insert_record(conn, "EventParticipants", record)

        copied.append({
            "id": car[0],
            "year": car[1],
            "make": car[2],
            "display": car[3],
            "media": car[4],
            "class_id": car[5],
            "pi": car[6],
            "priority": record["Priority"],
        })

    return copied


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

        # Match the working v4 event's section and place it at the next order.
        next_order = dst.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 '
            'FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (event["CareerTypeId"], event["Level"], event["GroupMask"]),
        ).fetchone()[0]

        event["Id"] = new_event_id
        event["EventOrder"] = int(next_order)
        insert_record(dst, "Events", event)

        race_ids = copy_races(dst, TEMPLATE_EVENT_ID, new_event_id)

        # Event 302's Ford Ka-specific restriction is deliberately not copied.
        # TargetClass=0 keeps player eligibility limited to F class.
        # For the AI field, use the 11 explicit car entries from existing
        # Event 600, then verify each referenced car is a drivable ClassID 0 car.
        ai_cars = copy_f_class_ai_roster(
            dst,
            new_event_id,
            expected_class=int(event["TargetClass"]),
            expected_count=int(event["NumberOfDrivers"]),
        )

        dst.commit()
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        print("Created F-class event with varied F-class AI opponents:")
        print(DEST)
        print()
        print(f"Template event:       {TEMPLATE_EVENT_ID}")
        print(f"AI roster source:     Event {F_CLASS_ROSTER_EVENT_ID}")
        print(f"New event ID:         {new_event_id}")
        print(f"CareerTypeId:         {event['CareerTypeId']}")
        print(f"Level:                {event['Level']}")
        print(f"GroupMask:            {event['GroupMask']}")
        print(f"EventOrder:           {next_order}")
        print(f"New race IDs:         {race_ids}")
        print(f"AI participant cars:  {len(ai_cars)}")
        for car in ai_cars:
            print(
                f"  priority={car['priority']:>2}  Data_Car.Id={car['id']}  "
                f"year={car['year']}  class={car['class_id']}  "
                f"PI={car['pi']}  media={car['media']}"
            )
        print("Ford Ka-specific restriction: NOT copied")
        print(f"SQLite integrity:     {integrity}")
        print()
        print("The original database was not modified.")

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
