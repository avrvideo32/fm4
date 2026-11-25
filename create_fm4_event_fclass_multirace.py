#!/usr/bin/env python3
"""Convert the existing three-race Event 750 test entry into an early F-class event.

Source database:
    gamedb_multirace_test.slt   (must contain Event 750 from the previous script)

Output database:
    gamedb_multirace_fclass_test.slt

This uses Event 200 as the complete, proven early F-class road-race template:
- Level 0, standard road-race mode, TargetClass 0 (F class)
- its own AI participant roster and restriction
- first three races from its existing 20-race schedule

Event 750 keeps its ID but is updated in the OUTPUT copy only. The source
database is never modified.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "gamedb_multirace_test.slt"
DEST = BASE / "gamedb_multirace_fclass_test.slt"

TARGET_EVENT_ID = 750
F_CLASS_TEMPLATE_ID = 200
RACE_COUNT = 3
EXPECTED_TARGET_CLASS = 0
EXPECTED_RESTRICTION = "000800000402"

ID_FLOORS = {
    "Races": ("Id", 3842),
    "EventParticipants": ("ID", 76007),
    "EventRestrictions": ("ID", 439),
}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({q(table)})")]


def get_event(conn: sqlite3.Connection, event_id: int) -> dict:
    names = columns(conn, "Events")
    row = conn.execute(
        'SELECT * FROM "Events" WHERE "Id" = ?', (event_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Event {event_id} does not exist")
    return dict(zip(names, row))


def rows_for_event(
    conn: sqlite3.Connection, table: str, event_id: int
) -> list[dict]:
    names = columns(conn, table)
    key = "EventID" if "EventID" in names else "EventId" if "EventId" in names else None
    if key is None:
        raise RuntimeError(f"{table} has no EventID/EventId column")
    data = conn.execute(
        f"SELECT * FROM {q(table)} WHERE {q(key)} = ? ORDER BY 1",
        (event_id,),
    ).fetchall()
    return [dict(zip(names, row)) for row in data]


def insert_row(conn: sqlite3.Connection, table: str, row: dict) -> None:
    names = list(row)
    conn.execute(
        f"INSERT INTO {q(table)} ({', '.join(q(n) for n in names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        [row[n] for n in names],
    )


def next_id(conn: sqlite3.Connection, table: str) -> int:
    column, floor = ID_FLOORS[table]
    maximum = conn.execute(
        f"SELECT COALESCE(MAX({q(column)}), 0) FROM {q(table)}"
    ).fetchone()[0]
    candidate = max(int(maximum), floor) + 1
    if conn.execute(
        f"SELECT 1 FROM {q(table)} WHERE {q(column)} = ? LIMIT 1",
        (candidate,),
    ).fetchone():
        raise RuntimeError(f"ID collision in {table}.{column}: {candidate}")
    return candidate


def main() -> None:
    if not SOURCE.is_file():
        raise SystemExit(f"Source database not found: {SOURCE}")
    if DEST.exists():
        raise SystemExit(
            f"Output already exists: {DEST}\n"
            "Rename or remove the old output before running this script again."
        )

    src = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    try:
        target_before = get_event(src, TARGET_EVENT_ID)
        template = get_event(src, F_CLASS_TEMPLATE_ID)

        if int(target_before["NumberOfRaces"]) != RACE_COUNT:
            raise RuntimeError(
                f"Event {TARGET_EVENT_ID} should currently be the three-race test; "
                f"NumberOfRaces={target_before['NumberOfRaces']}"
            )
        target_old_races = rows_for_event(src, "Races", TARGET_EVENT_ID)
        if len(target_old_races) != RACE_COUNT:
            raise RuntimeError(
                f"Event {TARGET_EVENT_ID} has {len(target_old_races)} race rows, expected 3"
            )

        if int(template["TargetClass"]) != EXPECTED_TARGET_CLASS:
            raise RuntimeError(
                f"Event {F_CLASS_TEMPLATE_ID} is not F class: "
                f"TargetClass={template['TargetClass']}"
            )
        if int(template["Level"]) != 0:
            raise RuntimeError(
                f"Event {F_CLASS_TEMPLATE_ID} is not an early Level 0 event: "
                f"Level={template['Level']}"
            )
        if int(template["NumberOfRaces"]) < RACE_COUNT:
            raise RuntimeError("F-class template has fewer than three races")

        template_races = rows_for_event(src, "Races", F_CLASS_TEMPLATE_ID)
        template_races.sort(key=lambda r: int(r["RaceNumber"]))
        template_races = template_races[:RACE_COUNT]
        if [int(r["RaceNumber"]) for r in template_races] != [1, 2, 3]:
            raise RuntimeError("Event 200 does not begin with race numbers 1, 2, 3")

        participants = rows_for_event(
            src, "EventParticipants", F_CLASS_TEMPLATE_ID
        )
        restrictions = rows_for_event(
            src, "EventRestrictions", F_CLASS_TEMPLATE_ID
        )
        showroom = rows_for_event(
            src, "EventShowroomChallenges", F_CLASS_TEMPLATE_ID
        )
        if int(template["NumberOfDrivers"]) != len(participants):
            raise RuntimeError(
                f"Event 200 declares {template['NumberOfDrivers']} drivers but has "
                f"{len(participants)} participant rows"
            )
        if not participants:
            raise RuntimeError("Event 200 has no AI participant roster")
        if len(restrictions) != 1 or restrictions[0]["Value"] != EXPECTED_RESTRICTION:
            raise RuntimeError(
                f"Unexpected Event 200 restriction: "
                f"{[r.get('Value') for r in restrictions]}"
            )
        if showroom:
            raise RuntimeError(
                "Event 200 has showroom challenge rows; this script does not clone them"
            )

        participant_ordinals = [int(p["Ordinal"]) for p in participants]
        if len(set(participant_ordinals)) != len(participant_ordinals):
            raise RuntimeError("Event 200 contains duplicate participant car IDs")
        for car_id in participant_ordinals:
            car = src.execute(
                'SELECT "ClassID", "IsDrivable" FROM "Data_Car" WHERE "Id" = ?',
                (car_id,),
            ).fetchone()
            if car is None or int(car[0]) != EXPECTED_TARGET_CLASS or int(car[1]) != 1:
                raise RuntimeError(
                    f"Event 200 participant car {car_id} is not a drivable F-class car: {car}"
                )

        race_special_cols = columns(src, "RacesSpecial")
        race_ids = [int(r["Id"]) for r in template_races]
        marks = ",".join("?" for _ in race_ids)
        special_data = src.execute(
            f'SELECT * FROM "RacesSpecial" WHERE "RaceId" IN ({marks})',
            race_ids,
        ).fetchall()
        special_rows = [
            dict(zip(race_special_cols, row)) for row in special_data
        ]

        # Calculate a new display order in Event 200's early F-class career section.
        next_order = src.execute(
            'SELECT COALESCE(MAX("EventOrder"), -1) + 1 FROM "Events" '
            'WHERE "CareerTypeId" = ? AND "Level" = ? AND "GroupMask" = ? '
            'AND "Id" <> ?',
            (
                template["CareerTypeId"],
                template["Level"],
                template["GroupMask"],
                TARGET_EVENT_ID,
            ),
        ).fetchone()[0]
    finally:
        src.close()

    src = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(DEST)
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        dst.execute("BEGIN IMMEDIATE")

        # Remove Event 750's old C-class linked records and special race data.
        old_race_ids = [
            int(row["Id"]) for row in rows_for_event(dst, "Races", TARGET_EVENT_ID)
        ]
        if old_race_ids:
            old_marks = ",".join("?" for _ in old_race_ids)
            dst.execute(
                f'DELETE FROM "RacesSpecial" WHERE "RaceId" IN ({old_marks})',
                old_race_ids,
            )
        dst.execute('DELETE FROM "Races" WHERE "EventId" = ?', (TARGET_EVENT_ID,))
        dst.execute(
            'DELETE FROM "EventParticipants" WHERE "EventID" = ?',
            (TARGET_EVENT_ID,),
        )
        dst.execute(
            'DELETE FROM "EventRestrictions" WHERE "EventID" = ?',
            (TARGET_EVENT_ID,),
        )
        dst.execute(
            'DELETE FROM "EventShowroomChallenges" WHERE "EventID" = ?',
            (TARGET_EVENT_ID,),
        )

        # Reuse Event 200's complete early F-class event setup, keeping ID 750.
        new_event = dict(template)
        new_event["Id"] = TARGET_EVENT_ID
        new_event["EventOrder"] = int(next_order)
        new_event["NumberOfRaces"] = RACE_COUNT
        update_names = [name for name in new_event if name != "Id"]
        dst.execute(
            "UPDATE Events SET "
            + ", ".join(f"{q(name)} = ?" for name in update_names)
            + ' WHERE "Id" = ?',
            [new_event[name] for name in update_names] + [TARGET_EVENT_ID],
        )

        new_race_ids = []
        race_id_map = {}
        for original in template_races:
            row = dict(original)
            old_id = int(row["Id"])
            row["Id"] = next_id(dst, "Races")
            row["EventId"] = TARGET_EVENT_ID
            insert_row(dst, "Races", row)
            new_race_ids.append(row["Id"])
            race_id_map[old_id] = row["Id"]

        new_participant_ids = []
        for original in participants:
            row = dict(original)
            row["ID"] = next_id(dst, "EventParticipants")
            row["EventID"] = TARGET_EVENT_ID
            insert_row(dst, "EventParticipants", row)
            new_participant_ids.append(row["ID"])

        new_restriction_ids = []
        for original in restrictions:
            row = dict(original)
            row["ID"] = next_id(dst, "EventRestrictions")
            row["EventID"] = TARGET_EVENT_ID
            insert_row(dst, "EventRestrictions", row)
            new_restriction_ids.append(row["ID"])

        new_special_count = 0
        for original in special_rows:
            row = dict(original)
            row["RaceId"] = race_id_map[int(row["RaceId"])]
            insert_row(dst, "RacesSpecial", row)
            new_special_count += 1

        # Verify Event 750 now uses the exact F-class template values.
        final_event = get_event(dst, TARGET_EVENT_ID)
        final_races = rows_for_event(dst, "Races", TARGET_EVENT_ID)
        final_participants = rows_for_event(
            dst, "EventParticipants", TARGET_EVENT_ID
        )
        final_restrictions = rows_for_event(
            dst, "EventRestrictions", TARGET_EVENT_ID
        )
        if (
            int(final_event["TargetClass"]) != EXPECTED_TARGET_CLASS
            or int(final_event["Level"]) != 0
            or int(final_event["NumberOfRaces"]) != RACE_COUNT
            or len(final_races) != RACE_COUNT
            or len(final_participants) != len(participants)
            or len(final_restrictions) != 1
            or final_restrictions[0]["Value"] != EXPECTED_RESTRICTION
        ):
            raise RuntimeError("Final Event 750 verification failed")

        for row in final_participants:
            car = dst.execute(
                'SELECT "ClassID", "IsDrivable" FROM "Data_Car" WHERE "Id" = ?',
                (row["Ordinal"],),
            ).fetchone()
            if car is None or int(car[0]) != EXPECTED_TARGET_CLASS or int(car[1]) != 1:
                raise RuntimeError(
                    f"Final participant {row['Ordinal']} is not a drivable F-class car"
                )

        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        dst.commit()

        print("SUCCESS: converted Event 750 to an early F-class three-race event")
        print(f"Source database:          {SOURCE}")
        print(f"Output database:           {DEST}")
        print(f"Event ID retained:         {TARGET_EVENT_ID}")
        print(f"Template:                  Event {F_CLASS_TEMPLATE_ID} (existing F-class road race)")
        print(f"CareerTypeId:              {final_event['CareerTypeId']}")
        print(f"Level:                     {final_event['Level']}")
        print(f"GroupMask:                 {final_event['GroupMask']}")
        print(f"EventOrder:                {final_event['EventOrder']}")
        print(f"TargetClass:               {final_event['TargetClass']} (F class)")
        print(f"NumberOfRaces:              {final_event['NumberOfRaces']}")
        print(f"NumberOfDrivers:            {final_event['NumberOfDrivers']}")
        print(f"Race IDs:                  {new_race_ids}")
        print(f"AI participant rows:       {len(new_participant_ids)}")
        print(f"Restriction:               {final_restrictions[0]['Value']}")
        print(f"Race-special rows copied:   {new_special_count}")
        print(f"SQLite integrity_check:    {integrity}")
        print("Source database was not modified.")
        print("Textures.zip and poster files were not changed.")
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
