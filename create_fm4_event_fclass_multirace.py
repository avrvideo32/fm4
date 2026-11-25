#!/usr/bin/env python3
"""Create a new early-career, three-race F-class event directly from gamedb.slt.

Uses:
- Event 200 for the early F-class road-racing event settings, first 3 races,
  and 7-car F-class AI roster.
- Event 600 only for its verified generic F-class restriction: 0000000004.

Creates a separate test database; the source database is never modified.
Output: gamedb_multirace_fclass_test.slt
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEST = BASE / "gamedb_multirace_fclass_test.slt"

EVENT_TEMPLATE_ID = 200
FCLASS_RESTRICTION_SOURCE_ID = 600
MIN_NEW_EVENT_ID = 750
RACE_COUNT = 3
F_CLASS = 0
EXPECTED_FCLASS_RESTRICTION = "0000000004"

ID_FLOORS = {
    "Races": 3842,
    "EventParticipants": 76007,
    "EventRestrictions": 439,
}


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def has_event(path: Path, event_id: int) -> bool:
    try:
        with open_readonly(path) as conn:
            return conn.execute(
                'SELECT 1 FROM "Events" WHERE "Id" = ? LIMIT 1',
                (event_id,),
            ).fetchone() is not None
    except (sqlite3.Error, OSError):
        return False


def choose_source() -> Path:
    main_db = BASE / "gamedb.slt"
    candidates = ([main_db] if main_db.is_file() else [])
    candidates += [
        p for p in sorted(
            BASE.glob("gamedb*.slt"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if p != DEST and p not in candidates
    ]
    if not candidates:
        raise SystemExit(f"No gamedb.slt or gamedb*.slt found in {BASE}")

    # Prefer the main database if it already contains the previous Event 749.
    if main_db.is_file() and has_event(main_db, 749):
        return main_db

    # Otherwise use a local database copy that contains Event 749, if present.
    for path in candidates:
        if has_event(path, 749):
            return path

    # Event 750 can also be created from a clean database; Event 749 is not
    # a dependency. The ID allocator reserves IDs below 750.
    if main_db.is_file():
        return main_db
    return candidates[0]


def get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({quote(table)})")]


def get_event(conn: sqlite3.Connection, event_id: int) -> dict:
    cols = get_columns(conn, "Events")
    row = conn.execute(
        'SELECT * FROM "Events" WHERE "Id" = ?', (event_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Required template Event {event_id} is missing")
    return dict(zip(cols, row))


def rows_for_event(
    conn: sqlite3.Connection, table: str, event_id: int
) -> list[dict]:
    cols = get_columns(conn, table)
    key = "EventID" if "EventID" in cols else "EventId" if "EventId" in cols else None
    if key is None:
        raise RuntimeError(f"{table} has no EventID/EventId column")
    values = conn.execute(
        f"SELECT * FROM {quote(table)} WHERE {quote(key)} = ? ORDER BY 1",
        (event_id,),
    ).fetchall()
    return [dict(zip(cols, row)) for row in values]


def insert_row(conn: sqlite3.Connection, table: str, row: dict) -> None:
    names = list(row)
    conn.execute(
        f"INSERT INTO {quote(table)} "
        f"({', '.join(quote(name) for name in names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        [row[name] for name in names],
    )


def allocate_id(conn: sqlite3.Connection, table: str) -> int:
    column = "Id" if table == "Events" or table == "Races" else "ID"
    floor = MIN_NEW_EVENT_ID - 1 if table == "Events" else ID_FLOORS[table]
    maximum = conn.execute(
        f"SELECT COALESCE(MAX({quote(column)}), 0) FROM {quote(table)}"
    ).fetchone()[0]
    candidate = max(int(maximum), floor) + 1
    if conn.execute(
        f"SELECT 1 FROM {quote(table)} WHERE {quote(column)} = ? LIMIT 1",
        (candidate,),
    ).fetchone():
        raise RuntimeError(f"ID collision: {table}.{column}={candidate}")
    return candidate


def main() -> None:
    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or remove that test output before running this script again."
        )

    source = choose_source()
    src = open_readonly(source)
    try:
        template = get_event(src, EVENT_TEMPLATE_ID)
        restriction_source_event = get_event(src, FCLASS_RESTRICTION_SOURCE_ID)

        if int(template["Level"]) != 0 or int(template["TargetClass"]) != F_CLASS:
            raise RuntimeError(
                "Event 200 is no longer the expected Level 0 F-class template: "
                f"Level={template['Level']}, TargetClass={template['TargetClass']}"
            )
        if int(template["NumberOfRaces"]) < RACE_COUNT:
            raise RuntimeError("Event 200 does not have at least three races")
        if int(restriction_source_event["TargetClass"]) != F_CLASS:
            raise RuntimeError("Event 600 is no longer an F-class restriction source")

        # Event 200's first three road races.
        all_template_races = rows_for_event(src, "Races", EVENT_TEMPLATE_ID)
        all_template_races.sort(key=lambda row: int(row["RaceNumber"]))
        template_races = all_template_races[:RACE_COUNT]
        if [int(row["RaceNumber"]) for row in template_races] != [1, 2, 3]:
            raise RuntimeError("Event 200 does not begin with races 1, 2, 3")

        # Keep the roster paired with the road-race template.
        participants = rows_for_event(
            src, "EventParticipants", EVENT_TEMPLATE_ID
        )
        if len(participants) != int(template["NumberOfDrivers"]):
            raise RuntimeError(
                f"Event 200 declares {template['NumberOfDrivers']} drivers, "
                f"but has {len(participants)} participant rows"
            )
        if not participants:
            raise RuntimeError("Event 200 has no AI participant roster")

        # Confirm every AI entry really points to a drivable F-class car.
        participant_car_ids = [int(row["Ordinal"]) for row in participants]
        if len(set(participant_car_ids)) != len(participant_car_ids):
            raise RuntimeError("Event 200 has duplicate AI car ordinals")
        for car_id in participant_car_ids:
            car = src.execute(
                'SELECT "ClassID", "IsDrivable" FROM "Data_Car" WHERE "Id" = ?',
                (car_id,),
            ).fetchone()
            if car is None or int(car[0]) != F_CLASS or int(car[1]) != 1:
                raise RuntimeError(
                    f"Event 200 AI car {car_id} is not a drivable F-class car: {car}"
                )

        # Use the generic F-class restriction from Event 600, not Event 200's
        # more specific restriction value.
        restrictions = rows_for_event(
            src, "EventRestrictions", FCLASS_RESTRICTION_SOURCE_ID
        )
        if (
            len(restrictions) != 1
            or restrictions[0]["Value"] != EXPECTED_FCLASS_RESTRICTION
        ):
            raise RuntimeError(
                "Event 600 no longer has the expected generic F-class "
                f"restriction {EXPECTED_FCLASS_RESTRICTION!r}"
            )

        if rows_for_event(src, "EventShowroomChallenges", EVENT_TEMPLATE_ID):
            raise RuntimeError(
                "Event 200 has showroom challenge dependencies; refusing to omit them"
            )

        # Preserve per-race supplemental data, remapping RaceId after cloning.
        race_special_cols = get_columns(src, "RacesSpecial")
        old_race_ids = [int(row["Id"]) for row in template_races]
        marks = ",".join("?" for _ in old_race_ids)
        special_values = src.execute(
            f'SELECT * FROM "RacesSpecial" WHERE "RaceId" IN ({marks})',
            old_race_ids,
        ).fetchall()
        special_rows = [
            dict(zip(race_special_cols, value)) for value in special_values
        ]

        next_order = src.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (template["CareerTypeId"], template["Level"], template["GroupMask"]),
        ).fetchone()[0]
    finally:
        src.close()

    src = open_readonly(source)
    dst = sqlite3.connect(DEST)
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        dst.execute("BEGIN IMMEDIATE")

        new_event_id = allocate_id(dst, "Events")
        new_event = dict(template)
        new_event["Id"] = new_event_id
        new_event["EventOrder"] = int(next_order)
        new_event["NumberOfRaces"] = RACE_COUNT
        insert_row(dst, "Events", new_event)

        race_id_map: dict[int, int] = {}
        new_race_ids = []
        for original in template_races:
            row = dict(original)
            old_id = int(row["Id"])
            new_id = allocate_id(dst, "Races")
            row["Id"] = new_id
            row["EventId"] = new_event_id
            insert_row(dst, "Races", row)
            race_id_map[old_id] = new_id
            new_race_ids.append(new_id)

        new_participant_ids = []
        for original in participants:
            row = dict(original)
            row["ID"] = allocate_id(dst, "EventParticipants")
            row["EventID"] = new_event_id
            insert_row(dst, "EventParticipants", row)
            new_participant_ids.append(row["ID"])

        new_restriction_ids = []
        for original in restrictions:
            row = dict(original)
            row["ID"] = allocate_id(dst, "EventRestrictions")
            row["EventID"] = new_event_id
            insert_row(dst, "EventRestrictions", row)
            new_restriction_ids.append(row["ID"])

        new_special_count = 0
        for original in special_rows:
            row = dict(original)
            row["RaceId"] = race_id_map[int(row["RaceId"])]
            insert_row(dst, "RacesSpecial", row)
            new_special_count += 1

        # Final structural and eligibility checks.
        final_event = get_event(dst, new_event_id)
        final_races = rows_for_event(dst, "Races", new_event_id)
        final_participants = rows_for_event(
            dst, "EventParticipants", new_event_id
        )
        final_restrictions = rows_for_event(
            dst, "EventRestrictions", new_event_id
        )

        if int(final_event["TargetClass"]) != F_CLASS:
            raise RuntimeError("New event is not F class")
        if int(final_event["Level"]) != 0:
            raise RuntimeError("New event is not at Level 0")
        if int(final_event["NumberOfRaces"]) != RACE_COUNT or len(final_races) != RACE_COUNT:
            raise RuntimeError("New event does not have exactly three races")
        if len(final_participants) != len(participants):
            raise RuntimeError("AI participant count changed during insert")
        if len(final_restrictions) != 1 or (
            final_restrictions[0]["Value"] != EXPECTED_FCLASS_RESTRICTION
        ):
            raise RuntimeError("Generic F-class restriction verification failed")

        for row in final_participants:
            car = dst.execute(
                'SELECT "ClassID", "IsDrivable" FROM "Data_Car" WHERE "Id" = ?',
                (row["Ordinal"],),
            ).fetchone()
            if car is None or int(car[0]) != F_CLASS or int(car[1]) != 1:
                raise RuntimeError(
                    f"New event participant {row['Ordinal']} is not a drivable F-class car"
                )

        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        dst.commit()

        print("SUCCESS: created a new early-career three-race F-class event")
        print(f"Source database:           {source}")
        print(f"Output database:            {DEST}")
        print(f"New event ID:                {new_event_id}")
        print(f"Template:                    Event {EVENT_TEMPLATE_ID} (Level 0 F-class road race)")
        print(f"Restriction source:          Event {FCLASS_RESTRICTION_SOURCE_ID}")
        print(f"CareerTypeId:                {final_event['CareerTypeId']}")
        print(f"Level:                       {final_event['Level']}")
        print(f"GroupMask:                   {final_event['GroupMask']}")
        print(f"EventOrder:                  {final_event['EventOrder']}")
        print(f"TargetClass:                 {final_event['TargetClass']} (F class)")
        print(f"NumberOfRaces:                {final_event['NumberOfRaces']}")
        print(f"NumberOfDrivers / AI rows:    {final_event['NumberOfDrivers']} / {len(final_participants)}")
        print(f"Race IDs:                    {new_race_ids}")
        print(f"Generic F-class restriction:  {final_restrictions[0]['Value']}")
        print(f"Race-special rows copied:     {new_special_count}")
        print(f"SQLite integrity_check:       {integrity}")
        print("Source database was not modified.")
        print("Textures.zip and poster files were not touched.")
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
