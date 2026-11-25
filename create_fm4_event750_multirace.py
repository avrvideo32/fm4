#!/usr/bin/env python3
"""Create Event 750 by cloning the working three-race C-class Event 106.

The script never modifies its source database. It writes:
    gamedb_multirace_test.slt

It prefers gamedb.slt when that database already contains the user's Event 749.
Otherwise it searches other local gamedb*.slt files for Event 749, then falls
back to gamedb.slt. All source selection is printed before the result details.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEST = BASE / "gamedb_multirace_test.slt"
TEMPLATE_EVENT_ID = 106
EXPECTED_RACES = 3

# Highest IDs observed across the base game and official DLC databases.
ID_FLOORS = {
    "Events": ("Id", 749),
    "Races": ("Id", 3842),
    "EventParticipants": ("ID", 76007),
    "EventRestrictions": ("ID", 439),
}


def readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def contains_event(path: Path, event_id: int) -> bool:
    try:
        with readonly(path) as conn:
            return conn.execute(
                'SELECT 1 FROM "Events" WHERE "Id" = ? LIMIT 1', (event_id,)
            ).fetchone() is not None
    except sqlite3.Error:
        return False


def select_source() -> Path:
    standard = BASE / "gamedb.slt"
    candidates = [standard] if standard.is_file() else []
    candidates += [
        p for p in sorted(
            BASE.glob("gamedb*.slt"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if p != DEST and p not in candidates
    ]

    if not candidates:
        raise SystemExit(f"No gamedb*.slt file found in {BASE}")

    # The expected main database takes priority when it already has Event 749.
    if standard.is_file() and contains_event(standard, 749):
        return standard

    # Otherwise retain the earlier custom event by using a local DB that has it.
    for candidate in candidates:
        if contains_event(candidate, 749):
            return candidate

    if standard.is_file():
        return standard
    return candidates[0]


def get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def allocate_id(conn: sqlite3.Connection, table: str) -> int:
    column, floor = ID_FLOORS[table]
    maximum = conn.execute(
        f'SELECT COALESCE(MAX("{column}"), 0) FROM "{table}"'
    ).fetchone()[0]
    candidate = max(int(maximum), floor) + 1
    exists = conn.execute(
        f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1',
        (candidate,),
    ).fetchone()
    if exists:
        raise RuntimeError(f"ID collision in {table}.{column}: {candidate}")
    return candidate


def insert_row(conn: sqlite3.Connection, table: str, row: dict) -> None:
    names = list(row)
    columns_sql = ", ".join(f'"{name}"' for name in names)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f'INSERT INTO "{table}" ({columns_sql}) VALUES ({placeholders})',
        [row[name] for name in names],
    )


def event_rows(conn: sqlite3.Connection, table: str, event_id: int) -> list[dict]:
    names = get_columns(conn, table)
    if "EventID" not in names:
        raise RuntimeError(f"Expected {table} to contain EventID")
    values = conn.execute(
        f'SELECT * FROM "{table}" WHERE "EventID" = ? ORDER BY 1',
        (event_id,),
    ).fetchall()
    return [dict(zip(names, value)) for value in values]


def main() -> None:
    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or remove this test output before running the script again."
        )

    source = select_source()
    source_has_749 = contains_event(source, 749)

    src = readonly(source)
    try:
        event_columns = get_columns(src, "Events")
        template_values = src.execute(
            'SELECT * FROM "Events" WHERE "Id" = ?',
            (TEMPLATE_EVENT_ID,),
        ).fetchone()
        if template_values is None:
            raise RuntimeError(f"Template event {TEMPLATE_EVENT_ID} is missing")
        template = dict(zip(event_columns, template_values))

        race_columns = get_columns(src, "Races")
        race_values = src.execute(
            'SELECT * FROM "Races" WHERE "EventId" = ? ORDER BY "RaceNumber"',
            (TEMPLATE_EVENT_ID,),
        ).fetchall()
        source_races = [dict(zip(race_columns, value)) for value in race_values]

        if len(source_races) != EXPECTED_RACES:
            raise RuntimeError(
                f"Event {TEMPLATE_EVENT_ID} should have {EXPECTED_RACES} races; "
                f"found {len(source_races)}"
            )
        numbers = [int(row["RaceNumber"]) for row in source_races]
        if numbers != [1, 2, 3]:
            raise RuntimeError(f"Unexpected template RaceNumber sequence: {numbers}")
        if int(template["NumberOfRaces"]) != EXPECTED_RACES:
            raise RuntimeError(
                f"Template declares NumberOfRaces={template['NumberOfRaces']}"
            )

        source_participants = event_rows(src, "EventParticipants", TEMPLATE_EVENT_ID)
        source_restrictions = event_rows(src, "EventRestrictions", TEMPLATE_EVENT_ID)
        source_showroom = event_rows(
            src, "EventShowroomChallenges", TEMPLATE_EVENT_ID
        )
        if not source_participants:
            raise RuntimeError("Template event has no AI participant rows")
        if not source_restrictions:
            raise RuntimeError("Template event has no restriction rows")
        if source_showroom:
            raise RuntimeError(
                "Template has showroom challenge rows; refusing to omit them"
            )

        for participant in source_participants:
            car_id = participant["Ordinal"]
            if src.execute(
                'SELECT 1 FROM "Data_Car" WHERE "Id" = ? LIMIT 1',
                (car_id,),
            ).fetchone() is None:
                raise RuntimeError(
                    f"Participant Ordinal {car_id} has no Data_Car record"
                )

        special_columns = get_columns(src, "RacesSpecial")
        source_race_ids = [int(row["Id"]) for row in source_races]
        placeholders = ",".join("?" for _ in source_race_ids)
        special_values = src.execute(
            f'SELECT * FROM "RacesSpecial" WHERE "RaceId" IN ({placeholders})',
            source_race_ids,
        ).fetchall()
        source_special = [
            dict(zip(special_columns, value)) for value in special_values
        ]
    finally:
        src.close()

    # Back up the selected source into a separate test database.
    src = readonly(source)
    dst = sqlite3.connect(DEST)
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        dst.execute("BEGIN IMMEDIATE")

        new_event_id = allocate_id(dst, "Events")
        next_order = dst.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (template["CareerTypeId"], template["Level"], template["GroupMask"]),
        ).fetchone()[0]

        new_event = dict(template)
        new_event["Id"] = new_event_id
        new_event["EventOrder"] = int(next_order)
        insert_row(dst, "Events", new_event)

        new_race_ids: list[int] = []
        old_to_new_race: dict[int, int] = {}
        for original in source_races:
            race = dict(original)
            old_id = int(race["Id"])
            race["Id"] = allocate_id(dst, "Races")
            race["EventId"] = new_event_id
            insert_row(dst, "Races", race)
            new_race_ids.append(race["Id"])
            old_to_new_race[old_id] = race["Id"]

        new_participant_ids = []
        for original in source_participants:
            participant = dict(original)
            participant["ID"] = allocate_id(dst, "EventParticipants")
            participant["EventID"] = new_event_id
            insert_row(dst, "EventParticipants", participant)
            new_participant_ids.append(participant["ID"])

        new_restriction_ids = []
        for original in source_restrictions:
            restriction = dict(original)
            restriction["ID"] = allocate_id(dst, "EventRestrictions")
            restriction["EventID"] = new_event_id
            insert_row(dst, "EventRestrictions", restriction)
            new_restriction_ids.append(restriction["ID"])

        for original in source_special:
            special = dict(original)
            old_race_id = int(special["RaceId"])
            special["RaceId"] = old_to_new_race[old_race_id]
            insert_row(dst, "RacesSpecial", special)

        # Verify cloned rows before committing.
        actual_races = dst.execute(
            'SELECT COUNT(*) FROM "Races" WHERE "EventId" = ?', (new_event_id,)
        ).fetchone()[0]
        actual_participants = dst.execute(
            'SELECT COUNT(*) FROM "EventParticipants" WHERE "EventID" = ?',
            (new_event_id,),
        ).fetchone()[0]
        actual_restrictions = dst.execute(
            'SELECT COUNT(*) FROM "EventRestrictions" WHERE "EventID" = ?',
            (new_event_id,),
        ).fetchone()[0]

        if actual_races != 3:
            raise RuntimeError(f"Expected 3 new races; found {actual_races}")
        if actual_participants != len(source_participants):
            raise RuntimeError("EventParticipants verification failed")
        if actual_restrictions != len(source_restrictions):
            raise RuntimeError("EventRestrictions verification failed")

        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        dst.commit()

        print("SUCCESS: created a separate three-race career event database")
        print(f"Source DB:              {source}")
        print(f"Source contains Event 749: {source_has_749}")
        print(f"Output DB:              {DEST}")
        print(f"Template event:         {TEMPLATE_EVENT_ID}")
        print(f"New event ID:           {new_event_id}")
        print(f"CareerTypeId:            {new_event['CareerTypeId']}")
        print(f"Level:                   {new_event['Level']}")
        print(f"GroupMask:               {new_event['GroupMask']}")
        print(f"TargetClass:             {new_event['TargetClass']} (C class)")
        print(f"EventOrder:              {next_order}")
        print(f"Race IDs:                {new_race_ids}")
        print(f"Participant rows copied: {len(new_participant_ids)}")
        print(f"Restriction rows copied: {len(new_restriction_ids)}")
        print(f"RacesSpecial rows copied: {len(source_special)}")
        print(f"SQLite integrity_check:  {integrity}")
        print("Source database was not modified.")
        print("No texture archive or poster files were changed.")
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
