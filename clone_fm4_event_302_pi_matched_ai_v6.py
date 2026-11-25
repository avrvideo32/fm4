import sqlite3
from pathlib import Path

SOURCE = Path(r"D:\code\py\fm4\gamedb.slt")
DEST = SOURCE.with_name("gamedb_f_class_pi_matched_ai_test_v6.slt")

TEMPLATE_EVENT_ID = 302
PLAYER_PI_DEFAULT = 200.0
AI_COUNT = 11

# Global maxima across the base game and official FM4 DLC databases.
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


def ask_player_pi():
    raw = input(
        f"Your car's current PI as shown in FM4 [{PLAYER_PI_DEFAULT:g}]: "
    ).strip()
    if not raw:
        return PLAYER_PI_DEFAULT
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit("Please enter a number, for example 200 or 187.") from exc
    if value <= 0:
        raise SystemExit("PI must be greater than zero.")
    return value


def get_pi_scale(conn, class_id):
    row = conn.execute(
        'SELECT "MaxPerformanceIndex", "MaxDisplayPerformanceIndex" '
        'FROM "CarClasses" WHERE "Id" = ?',
        (class_id,),
    ).fetchone()
    if row is None or row[0] in (None, 0) or row[1] in (None, 0):
        raise RuntimeError(
            f"Could not determine the displayed PI scale for CarClasses.Id={class_id}"
        )

    # Data_Car.PerformanceIndex is stored on the database's normalized scale.
    # CarClasses provides the corresponding maximum displayed PI for this class.
    return float(row[1]) / float(row[0])


def get_ai_roster(conn, class_id, target_pi, pi_scale):
    rows = conn.execute(
        'SELECT "Id", "Year", "MakeName", "MediaName", "ClassID", '
        '"PerformanceIndex", "IsDrivable", "IsInstalled" '
        'FROM "Data_Car" '
        'WHERE "ClassID" = ? AND "IsDrivable" = 1 AND "IsInstalled" = 1',
        (class_id,),
    ).fetchall()

    # If the installed flag is not populated consistently, fall back to all
    # drivable cars in the same class. The report identifies any such fallback.
    used_installed_filter = True
    if len(rows) < AI_COUNT:
        rows = conn.execute(
            'SELECT "Id", "Year", "MakeName", "MediaName", "ClassID", '
            '"PerformanceIndex", "IsDrivable", "IsInstalled" '
            'FROM "Data_Car" '
            'WHERE "ClassID" = ? AND "IsDrivable" = 1',
            (class_id,),
        ).fetchall()
        used_installed_filter = False

    candidates = []
    for row in rows:
        if row[5] is None:
            continue
        displayed_pi = float(row[5]) * pi_scale
        candidates.append({
            "id": int(row[0]),
            "year": row[1],
            "make": row[2],
            "media": row[3],
            "class_id": int(row[4]),
            "stored_pi": float(row[5]),
            "displayed_pi": displayed_pi,
            "distance": abs(displayed_pi - target_pi),
            "installed": int(row[7] or 0),
        })

    candidates.sort(key=lambda car: (car["distance"], car["id"]))
    if len(candidates) < AI_COUNT:
        raise RuntimeError(
            f"Only {len(candidates)} drivable cars were found in class {class_id}; "
            f"{AI_COUNT} are needed for this event."
        )

    # Each AI entry must be a distinct car record.
    selected = []
    seen = set()
    for car in candidates:
        if car["id"] in seen:
            continue
        selected.append(car)
        seen.add(car["id"])
        if len(selected) == AI_COUNT:
            break

    return selected, used_installed_filter, len(candidates)


def main():
    if not SOURCE.is_file():
        raise SystemExit(f"Source database not found: {SOURCE}")
    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or delete the old output before running this script."
        )

    target_pi = ask_player_pi()

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
        class_id = int(event["TargetClass"])
        ai_count = int(event["NumberOfDrivers"])
        if ai_count != AI_COUNT:
            raise RuntimeError(
                f"Template Event {TEMPLATE_EVENT_ID} expects {ai_count} drivers, "
                f"but this script is configured for {AI_COUNT}."
            )

        pi_scale = get_pi_scale(dst, class_id)
        ai_cars, used_installed_filter, candidate_count = get_ai_roster(
            dst, class_id, target_pi, pi_scale
        )

        new_event_id = safe_new_id(dst, "Events")
        next_order = dst.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 '
            'FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (event["CareerTypeId"], event["Level"], event["GroupMask"]),
        ).fetchone()[0]

        event["Id"] = new_event_id
        event["EventOrder"] = int(next_order)
        insert_record(dst, "Events", event)

        race_cols = get_columns(dst, "Races")
        race_rows = dst.execute(
            'SELECT * FROM "Races" WHERE "EventId" = ? ORDER BY "RaceNumber"',
            (TEMPLATE_EVENT_ID,),
        ).fetchall()
        for row in race_rows:
            race = dict(zip(race_cols, row))
            race["Id"] = safe_new_id(dst, "Races")
            race["EventId"] = new_event_id
            insert_record(dst, "Races", race)

        # Reuse the template participant's non-identity settings, but create
        # one participant row per selected AI car instead of repeating the Ka.
        participant_cols = get_columns(dst, "EventParticipants")
        participant_row = dst.execute(
            'SELECT * FROM "EventParticipants" WHERE "EventID" = ? '
            'ORDER BY "Priority" LIMIT 1',
            (TEMPLATE_EVENT_ID,),
        ).fetchone()
        if participant_row is None:
            raise RuntimeError("Template Event 302 has no participant template row")
        participant_template = dict(zip(participant_cols, participant_row))

        for priority, car in enumerate(ai_cars, start=1):
            participant = dict(participant_template)
            participant["ID"] = safe_new_id(dst, "EventParticipants")
            participant["EventID"] = new_event_id
            participant["Priority"] = priority
            participant["Ordinal"] = car["id"]
            insert_record(dst, "EventParticipants", participant)

        # Deliberately do not copy Event 302's Ford Ka-specific restriction.
        # TargetClass still limits the player to F class.

        dst.commit()
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        print()
        print("Created PI-matched F-class event test database:")
        print(DEST)
        print(f"New Event ID:          {new_event_id}")
        print(f"Target player PI:       {target_pi:g}")
        print(f"PI conversion scale:    {pi_scale:g}")
        print(f"Candidate cars checked: {candidate_count}")
        print(
            "Candidate filter:      "
            + ("drivable + installed" if used_installed_filter else "drivable only")
        )
        print()
        print("Selected AI opponents (closest stock PI values):")
        for index, car in enumerate(ai_cars, start=1):
            print(
                f"{index:2}. CarId={car['id']:<5} "
                f"Year={car['year']} "
                f"MediaName={car['media']} "
                f"PI={car['displayed_pi']:.1f} "
                f"Difference={car['distance']:.1f}"
            )

        print()
        print("Ford Ka-specific restriction: NOT copied")
        print(f"SQLite integrity:      {integrity}")
        print("Original gamedb.slt:   NOT modified")
        print()
        print(
            "Note: opponent PI uses each car's stock Data_Car PI. "
            "Your entered PI can include your upgrades."
        )

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
