#!/usr/bin/env python3
"""Create a three-race F-class career event using Event 106's race schedule.

Race schedule and event placement are cloned from Event 106.
F-class AI cars and the generic F-class restriction are cloned from Event 600.

The source database is never modified. A separate test copy is written to:
    gamedb_multirace_fclass_test.slt
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEST = BASE / "gamedb_multirace_fclass_test.slt"

RACE_TEMPLATE_ID = 106       # Confirmed three-race event
FCLASS_ROSTER_EVENT_ID = 600 # Confirmed F-class AI roster and generic restriction
EXPECTED_RACES = 3
EXPECTED_FCLASS_DRIVERS = 11
EXPECTED_FCLASS_RESTRICTION = "0000000004"

ID_FLOORS = {
    "Events": ("Id", 749),
    "Races": ("Id", 3842),
    "EventParticipants": ("ID", 76007),
    "EventRestrictions": ("ID", 439),
}


def ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def rows_for_event(conn: sqlite3.Connection, table: str, event_id: int) -> list[dict]:
    columns = table_columns(conn, table)
    key = "EventID" if "EventID" in columns else "EventId" if "EventId" in columns else None
    if key is None:
        raise RuntimeError(f"{table} has no EventID/EventId column")
    data = conn.execute(
        f'SELECT * FROM "{table}" WHERE "{key}" = ? ORDER BY 1', (event_id,)
    ).fetchall()
    return [dict(zip(columns, row)) for row in data]


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    names = list(row)
    conn.execute(
        f'INSERT INTO "{table}" ({", ".join(fchr(n) for n in names)}) '
        f'VALUES ({", ".join("?" for _ in names)})',
        [row[n] for n in names],
    )


def fchr(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def next_id(conn: sqlite3.Connection, table: str) -> int:
    column, floor = ID_FLOORS[table]
    maximum = conn.execute(
        f'SELECT COALESCE(MAX("{column}"), 0) FROM "{table}"'
    ).fetchone()[0]
    value = max(int(maximum), floor) + 1
    if conn.execute(
        f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1', (value,)
    ).fetchone():
        raise RuntimeError(f"Unexpected ID collision: {table}.{column}={value}")
    return value


def select_source() -> Path:
    preferred = [
        BASE / "gamedb_multirace_test.slt",
        BASE / "gamedb.slt",
    ]
    candidates: list[Path] = []
    for p in preferred:
        if p.is_file() and p not in candidates:
            candidates.append(p)
    candidates += [
        p for p in sorted(BASE.glob("gamedb*.slt"),
                          key=lambda x: x.stat().st_mtime, reverse=True)
        if p != DEST and p not in candidates
    ]
    if not candidates:
        raise SystemExit(f"No gamedb*.slt found in {BASE}")

    # Prefer the previous test database so its Event 750 is retained.
    for p in candidates:
        try:
            with ro(p) as conn:
                if conn.execute(
                    'SELECT 1 FROM sqlite_master WHERE type="table" AND name="Events"'
                ).fetchone() and conn.execute(
                    'SELECT 1 FROM "Events" WHERE "Id" = ? LIMIT 1',
                    (RACE_TEMPLATE_ID,),
                ).fetchone():
                    return p
        except sqlite3.Error:
            continue
    raise RuntimeError("No candidate database contains the required Events table/template")


def main() -> None:
    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or remove this test output before running again."
        )

    source = select_source()
    src = ro(source)
    try:
        event_cols = table_columns(src, "Events")
        template_data = src.execute(
            'SELECT * FROM "Events" WHERE "Id" = ?', (RACE_TEMPLATE_ID,)
        ).fetchone()
        roster_event_data = src.execute(
            'SELECT * FROM "Events" WHERE "Id" = ?', (FCLASS_ROSTER_EVENT_ID,)
        ).fetchone()
        if template_data is None or roster_event_data is None:
            raise RuntimeError("Race template Event 106 or F-class source Event 600 is missing")

        template = dict(zip(event_cols, template_data))
        roster_event = dict(zip(event_cols, roster_event_data))

        if int(template["NumberOfRaces"]) != EXPECTED_RACES:
            raise RuntimeError(
                f"Event 106 declares {template['NumberOfRaces']} races, expected 3"
            )
        if int(template["TargetClass"]) != 3:
            raise RuntimeError(
                f"Event 106 is no longer the expected C-class template: "
                f"TargetClass={template['TargetClass']}"
            )
        if int(roster_event["TargetClass"]) != 0:
            raise RuntimeError(
                f"Event 600 is not F class: TargetClass={roster_event['TargetClass']}"
            )
        if int(roster_event["NumberOfDrivers"]) != EXPECTED_FCLASS_DRIVERS:
            raise RuntimeError(
                f"Event 600 declares {roster_event['NumberOfDrivers']} drivers, expected 11"
            )

        race_cols = table_columns(src, "Races")
        race_data = src.execute(
            'SELECT * FROM "Races" WHERE "EventId" = ? ORDER BY "RaceNumber"',
            (RACE_TEMPLATE_ID,),
        ).fetchall()
        races = [dict(zip(race_cols, row)) for row in race_data]
        if len(races) != EXPECTED_RACES or [
            int(r["RaceNumber"]) for r in races
        ] != [1, 2, 3]:
            raise RuntimeError("Event 106 does not contain the expected RaceNumber 1, 2, 3")

        participants = rows_for_event(src, "EventParticipants", FCLASS_ROSTER_EVENT_ID)
        if len(participants) != EXPECTED_FCLASS_DRIVERS:
            raise RuntimeError(
                f"Event 600 has {len(participants)} AI roster rows, expected 11"
            )
        ordinals = [int(p["Ordinal"]) for p in participants]
        priorities = [int(p["Priority"]) for p in participants]
        if len(set(ordinals)) != len(ordinals):
            raise RuntimeError("Event 600's F-class roster contains duplicate car IDs")
        if len(set(priorities)) != len(priorities):
            raise RuntimeError("Event 600's F-class roster contains duplicate priorities")

        for car_id in ordinals:
            car = src.execute(
                'SELECT "ClassID", "IsDrivable" FROM "Data_Car" WHERE "Id" = ?',
                (car_id,),
            ).fetchone()
            if car is None or int(car[0]) != 0 or int(car[1]) != 1:
                raise RuntimeError(
                    f"Roster car {car_id} is missing, not F class, or not drivable: {car}"
                )

        restrictions = rows_for_event(src, "EventRestrictions", FCLASS_ROSTER_EVENT_ID)
        if len(restrictions) != 1 or restrictions[0]["Value"] != EXPECTED_FCLASS_RESTRICTION:
            raise RuntimeError(
                "Event 600 restriction changed; expected exactly one "
                f"{EXPECTED_FCLASS_RESTRICTION!r} row, got "
                f"{[r.get('Value') for r in restrictions]}"
            )

        showroom = rows_for_event(src, "EventShowroomChallenges", RACE_TEMPLATE_ID)
        if showroom:
            raise RuntimeError(
                "Event 106 has showroom challenge dependencies; refusing to omit them"
            )

        # Copy race-special metadata, remapping old Race IDs to new Race IDs.
        special_cols = table_columns(src, "RacesSpecial")
        old_race_ids = [int(r["Id"]) for r in races]
        marks = ",".join("?" for _ in old_race_ids)
        special_data = src.execute(
            f'SELECT * FROM "RacesSpecial" WHERE "RaceId" IN ({marks})',
            old_race_ids,
        ).fetchall()
        special_rows = [dict(zip(special_cols, row)) for row in special_data]
    finally:
        src.close()

    src = ro(source)
    dst = sqlite3.connect(DEST)
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        dst.execute("BEGIN IMMEDIATE")

        new_event_id = next_id(dst, "Events")
        next_order = dst.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ?',
            (template["CareerTypeId"], template["Level"], template["GroupMask"]),
        ).fetchone()[0]

        new_event = dict(template)
        new_event["Id"] = new_event_id
        new_event["EventOrder"] = int(next_order)
        new_event["TargetClass"] = 0        # F class
        new_event["NumberOfDrivers"] = EXPECTED_FCLASS_DRIVERS
        insert(dst, "Events", new_event)

        race_id_map: dict[int, int] = {}
        new_race_ids: list[int] = []
        for original in races:
            race = dict(original)
            old_id = int(race["Id"])
            new_id = next_id(dst, "Races")
            race["Id"] = new_id
            race["EventId"] = new_event_id
            insert(dst, "Races", race)
            race_id_map[old_id] = new_id
            new_race_ids.append(new_id)

        new_participant_ids = []
        for original in participants:
            row = dict(original)
            row["ID"] = next_id(dst, "EventParticipants")
            row["EventID"] = new_event_id
            insert(dst, "EventParticipants", row)
            new_participant_ids.append(row["ID"])

        new_restriction_ids = []
        for original in restrictions:
            row = dict(original)
            row["ID"] = next_id(dst, "EventRestrictions")
            row["EventID"] = new_event_id
            insert(dst, "EventRestrictions", row)
            new_restriction_ids.append(row["ID"])

        for original in special_rows:
            row = dict(original)
            row["RaceId"] = race_id_map[int(row["RaceId"])]
            insert(dst, "RacesSpecial", row)

        # Verify the final event's linked content and class eligibility.
        checks = {
            "races": dst.execute(
                'SELECT COUNT(*) FROM "Races" WHERE "EventId" = ?', (new_event_id,)
            ).fetchone()[0],
            "participants": dst.execute(
                'SELECT COUNT(*) FROM "EventParticipants" WHERE "EventID" = ?',
                (new_event_id,),
            ).fetchone()[0],
            "restrictions": dst.execute(
                'SELECT COUNT(*) FROM "EventRestrictions" WHERE "EventID" = ?',
                (new_event_id,),
            ).fetchone()[0],
        }
        if checks != {"races": 3, "participants": 11, "restrictions": 1}:
            raise RuntimeError(f"Final row-count verification failed: {checks}")

        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")
        dst.commit()

        print("SUCCESS: created a three-race F-class event")
        print(f"Source database:         {source}")
        print(f"Output database:          {DEST}")
        print(f"Template race event:      {RACE_TEMPLATE_ID} (three races)")
        print(f"F-class roster source:    {FCLASS_ROSTER_EVENT_ID}")
        print(f"New event ID:             {new_event_id}")
        print(f"CareerTypeId / Level:     {new_event['CareerTypeId']} / {new_event['Level']}")
        print(f"GroupMask / EventOrder:   {new_event['GroupMask']} / {next_order}")
        print("TargetClass:              0 (F class)")
        print(f"NumberOfDrivers:          {EXPECTED_FCLASS_DRIVERS}")
        print(f"Race IDs:                 {new_race_ids}")
        print(f"AI car rows copied:       {len(new_participant_ids)}")
        print(f"Restriction:              {EXPECTED_FCLASS_RESTRICTION}")
        print(f"Race-special rows copied: {len(special_rows)}")
        print(f"SQLite integrity_check:   {integrity}")
        print("Original source database was not modified.")
        print("Textures.zip and all poster files were left untouched.")
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
