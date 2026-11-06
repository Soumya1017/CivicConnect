"""
Fill the database with demo reports so the dashboard and map look real.

Run once:   python seed.py
Reset:      delete database.db, then run python seed.py again

CHANGE the two numbers below to your own city centre (right-click on Google Maps
and click the coordinates to copy them).
"""
import random
import sqlite3
from datetime import datetime, timedelta

from app import CATEGORIES, DB_PATH, init_db, new_complaint_id

CENTRE_LAT = 12.9716
CENTRE_LNG = 77.5946

TITLES = {
    "water_leak": "Water pipe leaking on the road",
    "road_damage": "Large pothole near the bus stop",
    "drainage": "Drain blocked, water logging after rain",
    "streetlight": "Streetlight not working for a week",
    "garbage": "Garbage bin overflowing",
    "other": "Broken footpath tiles",
}
STAMP = "%Y-%m-%d %H:%M:%S"


def add(db, category, lat, lng, status, days_ago, dup_of=None, title=None):
    created = datetime.now() - timedelta(days=days_ago, hours=random.randint(0, 10))
    cid = new_complaint_id(db)
    reviewed = status in ("under_review", "in_progress", "resolved")
    resolved_at = None
    if status == "resolved":
        resolved_at = (created + timedelta(days=random.randint(1, max(2, int(days_ago))))).strftime(STAMP)

    cur = db.execute(
        """INSERT INTO reports (complaint_id, title, description, category, lat, lng, status,
                                is_verified, duplicate_of, created_at, updated_at, resolved_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (cid, title or TITLES[category], "Demo report. A resident asked for this to be inspected.",
         category, lat, lng, status, int(reviewed), dup_of,
         created.strftime(STAMP), created.strftime(STAMP), resolved_at),
    )
    rid = cur.lastrowid

    def hist(st, note, when):
        db.execute(
            "INSERT INTO status_updates (report_id, status, note, created_at) VALUES (?, ?, ?, ?)",
            (rid, st, note, when.strftime(STAMP)),
        )

    hist("submitted", "Report received. Not yet reviewed by an administrator.", created)
    if status in ("under_review", "in_progress", "resolved"):
        hist("under_review", "An administrator is reviewing this report.", created + timedelta(hours=6))
    if status in ("in_progress", "resolved"):
        hist("in_progress", "Work has been scheduled with the responsible team.", created + timedelta(days=1))
    if status == "resolved":
        hist("resolved", "Issue fixed by the municipal crew.", datetime.strptime(resolved_at, STAMP))
    return rid


def main():
    init_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    random.seed(7)
    slugs = list(CATEGORIES)
    statuses = ["submitted", "submitted", "under_review", "in_progress", "in_progress", "resolved", "resolved"]

    # 30 scattered reports
    for _ in range(30):
        cat = random.choice(slugs)
        add(db, cat,
            CENTRE_LAT + random.uniform(-0.03, 0.03),
            CENTRE_LNG + random.uniform(-0.03, 0.03),
            random.choice(statuses), random.randint(1, 25))

    # Recurring hotspot 1: garbage at the same junction
    for i in range(5):
        add(db, "garbage",
            CENTRE_LAT + 0.0101 + random.uniform(-0.0002, 0.0002),
            CENTRE_LNG - 0.0153 + random.uniform(-0.0002, 0.0002),
            "under_review", 4 + i * 3, title="Garbage dumped at the junction")

    # Recurring hotspot 2: a pothole that keeps coming back
    for i in range(4):
        add(db, "road_damage",
            CENTRE_LAT - 0.0202 + random.uniform(-0.0002, 0.0002),
            CENTRE_LNG + 0.0107 + random.uniform(-0.0002, 0.0002),
            "in_progress", 5 + i * 4, title="Pothole keeps coming back")

    # An urgent issue with many merged duplicates (shows the priority score)
    lat, lng = CENTRE_LAT + 0.004, CENTRE_LNG + 0.006
    original = add(db, "water_leak", lat, lng, "under_review", 6, title="Main water pipe burst")
    for i in range(6):
        add(db, "water_leak", lat + random.uniform(-0.0003, 0.0003), lng + random.uniform(-0.0003, 0.0003),
            "submitted", 5 - min(i, 4), dup_of=original, title="Water gushing from road")
    db.execute("UPDATE reports SET duplicate_count = 6 WHERE id = ?", (original,))

    db.commit()
    total = db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    db.close()
    print(f"Done. {total} demo reports added.")


if __name__ == "__main__":
    main()