import sqlite3
from pathlib import Path

DB_PATH = Path(r"D:\code\py\fm4\gamedb.slt")
TEMPLATE_EVENT_ID = 302
KEYWORDS = (
    "event", "career", "unlock", "hub", "group", "series",
    "champ", "reward", "recommended", "showroom", "challenge",
    "race", "menu", "progress", "playlist", "cup", "tournament",
)


def q(name):
    return '"' + name.replace('"', '""') + '"'


def main():
    if not DB_PATH.is_file():
        raise SystemExit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        print("FM4 FULL DATABASE SCHEMA / CAREER REGISTRATION AUDIT")
        print(f"Database: {DB_PATH}")
        print("READ ONLY — no writes will be performed.")
        print()

        objects = conn.execute("""
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
        """).fetchall()

        tables = [r["name"] for r in objects if r["type"] == "table"]
        views = [r for r in objects if r["type"] == "view"]
        triggers = [r for r in objects if r["type"] == "trigger"]
        indexes = [r for r in objects if r["type"] == "index"]

        print("=== DATABASE OBJECT COUNTS ===")
        print(f"Tables: {len(tables)}")
        print(f"Views: {len(views)}")
        print(f"Triggers: {len(triggers)}")
        print(f"Indexes: {len(indexes)}")
        print()

        print("=== ALL TABLE NAMES ===")
        for name in tables:
            print(name)
        print()

        print("=== CAREER / EVENT / PROGRESSION-RELATED TABLE SCHEMAS ===")
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info({q(table)})").fetchall()
            combined = (table + " " + " ".join(c["name"] for c in cols)).lower()
            if any(word in combined for word in KEYWORDS):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {q(table)}"
                ).fetchone()[0]
                col_text = ", ".join(
                    f'{c["name"]}:{c["type"] or "?"}'
                    + (" PK" if c["pk"] else "")
                    for c in cols
                )
                print(f"{table} [{count} rows]")
                print(f"  {col_text}")
        print()

        print("=== FOREIGN KEY DEFINITIONS ===")
        found_fk = False
        for table in tables:
            refs = conn.execute(
                f"PRAGMA foreign_key_list({q(table)})"
            ).fetchall()
            for ref in refs:
                found_fk = True
                print(
                    f'{table}.{ref["from"]} -> '
                    f'{ref["table"]}.{ref["to"]}'
                )
        if not found_fk:
            print("No declared foreign keys.")
        print()

        print("=== EVENT 302 LOOKUP ACROSS EVENT-RELATED TABLES ===")
        target = TEMPLATE_EVENT_ID
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info({q(table)})").fetchall()
            col_names = [c["name"] for c in cols]
            relevant = (
                any(word in table.lower() for word in KEYWORDS)
                or any(any(word in c.lower() for word in KEYWORDS)
                       for c in col_names)
            )
            if not relevant:
                continue

            for col in cols:
                col_name = col["name"]
                if not (
                    "INT" in (col["type"] or "").upper()
                    or col["pk"]
                ):
                    continue
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {q(table)} WHERE {q(col_name)} = ?",
                    (target,),
                ).fetchone()[0]
                if count:
                    samples = conn.execute(
                        f"SELECT * FROM {q(table)} WHERE {q(col_name)} = ? LIMIT 3",
                        (target,),
                    ).fetchall()
                    print(f"{table}.{col_name}: {count} row(s) equal 302")
                    for row in samples:
                        print(" ", dict(row))
        print()

        print("=== EVENT 302 AND NEIGHBOURING EVENTS ===")
        if "Events" in tables:
            event_rows = conn.execute("""
                SELECT Id, Name, Level, CareerTypeId, NumberOfRaces,
                       EventOrder, GroupMask, TargetClass, IsDLC
                FROM Events
                WHERE CareerTypeId = (
                    SELECT CareerTypeId FROM Events WHERE Id = ?
                )
                ORDER BY Level, EventOrder, Id
            """, (TEMPLATE_EVENT_ID,)).fetchall()
            for row in event_rows:
                print(dict(row))
        print()

        print("=== EVENT-RELATED VIEWS / TRIGGERS ===")
        relevant_objects = [
            r for r in objects
            if r["type"] in ("view", "trigger")
            and any(word in (r["name"] + " " + (r["sql"] or "")).lower()
                    for word in KEYWORDS)
        ]
        if not relevant_objects:
            print("None found by name or SQL text.")
        else:
            for obj in relevant_objects:
                print(f'[{obj["type"]}] {obj["name"]} on {obj["tbl_name"]}')
                print(obj["sql"] or "")
        print()
        print("=== END OF AUDIT ===")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
