import math
import os
import re
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.utils import secure_filename

import notifier

# ---------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}



def load_env():
    """Read settings from a .env file next to app.py (optional)."""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB uploads

# Change the admin password before you show this to anyone:
#   Windows (PowerShell):  $env:ADMIN_PASSWORD="mypassword"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# Link used inside emails/SMS. Use your laptop's address if phones must open it.
BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000").rstrip("/")
DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "+91")

# Map starts here (change to your city centre: latitude, longitude)
MAP_CENTER = (12.9716, 77.5946)

# slug: (label, severity weight used in the priority score)
CATEGORIES = {
    "water_leak": ("Water leakage / pipe burst", 35),
    "road_damage": ("Damaged road / pothole", 30),
    "drainage": ("Blocked drain / flooding", 30),
    "streetlight": ("Broken streetlight", 20),
    "garbage": ("Overflowing bin / garbage", 15),
    "other": ("Other", 10),
}

STATUS_LABELS = {
    "submitted": "Submitted",
    "under_review": "Under review",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "rejected": "Rejected",
    "reopened": "Reopened",
}
OPEN_STATUSES = ("submitted", "under_review", "in_progress", "reopened")
REVIEWED_STATUSES = ("under_review", "in_progress", "resolved")
ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike characters


# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            lat REAL,
            lng REAL,
            image TEXT,
            status TEXT NOT NULL DEFAULT 'submitted',
            is_verified INTEGER NOT NULL DEFAULT 0,
            duplicate_of INTEGER,
            duplicate_count INTEGER NOT NULL DEFAULT 0,
            citizen_verified INTEGER,
            contact_email TEXT,
            contact_phone TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS status_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT,
            proof_image TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (report_id) REFERENCES reports(id)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            recipient TEXT NOT NULL,
            subject TEXT,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (report_id) REFERENCES reports(id)
        );
        """
    )
    # Upgrade an older database.db that was created before contact details existed
    columns = {row[1] for row in db.execute("PRAGMA table_info(reports)")}
    if "contact_email" not in columns:
        db.execute("ALTER TABLE reports ADD COLUMN contact_email TEXT")
    if "contact_phone" not in columns:
        db.execute("ALTER TABLE reports ADD COLUMN contact_phone TEXT")
    db.commit()
    db.close()


init_db()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def new_complaint_id(db):
    while True:
        cid = "CC-" + "".join(secrets.choice(ID_ALPHABET) for _ in range(6))
        if not db.execute("SELECT 1 FROM reports WHERE complaint_id = ?", (cid,)).fetchone():
            return cid


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def save_image(file_storage):
    """Save an uploaded image with a random name. Returns filename or None."""
    if not file_storage or not file_storage.filename:
        return None
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        return None
    name = secrets.token_hex(12) + "." + ext
    file_storage.save(os.path.join(UPLOAD_DIR, name))
    return name


def priority(report):
    """Return (score, human-readable reason). Score = weight + 5/duplicate + days open."""
    label, weight = CATEGORIES.get(report["category"], ("Other", 10))
    created = parse_time(report["created_at"])
    days = max((datetime.now() - created).days, 0) if created else 0
    dups = min(report["duplicate_count"], 10)
    score = weight + dups * 5 + min(days, 30)
    reason = f"{label}: {weight} + {dups} duplicate(s) x 5 + {days} day(s) open"
    return score, reason


def find_duplicates(db, category, lat, lng, radius_m=100):
    rows = db.execute(
        """SELECT id, title, lat, lng, status FROM reports
           WHERE category = ? AND lat IS NOT NULL AND duplicate_of IS NULL
             AND status IN ('submitted','under_review','in_progress','reopened')""",
        (category,),
    ).fetchall()
    found = []
    for r in rows:
        d = haversine_m(lat, lng, r["lat"], r["lng"])
        if d <= radius_m:
            found.append({
                "id": r["id"],
                "title": r["title"],
                "distance": round(d),
                "status_label": STATUS_LABELS[r["status"]],
            })
    found.sort(key=lambda x: x["id"])  # first raised comes first
    return found[:5]


def add_history(db, report_id, status, note, proof_image=None):
    db.execute(
        "INSERT INTO status_updates (report_id, status, note, proof_image, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (report_id, status, note, proof_image, now_str()),
    )


def parse_location(lat_raw, lng_raw):
    """Both or neither. Returns (lat, lng, error)."""
    lat_raw, lng_raw = (lat_raw or "").strip(), (lng_raw or "").strip()
    if not lat_raw and not lng_raw:
        return None, None, None
    try:
        lat, lng = float(lat_raw), float(lng_raw)
    except ValueError:
        return None, None, "Location is not valid. Tap the map again."
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None, "Location is outside the valid range."
    return lat, lng, None


EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def normalise_phone(raw):
    """Return (phone, error). Accepts 9876543210 or +919876543210 style numbers."""
    p = re.sub(r"[\s\-()]", "", raw or "")
    if not p:
        return None, None
    if re.fullmatch(r"\d{10}", p):
        return DEFAULT_COUNTRY_CODE + p, None
    if re.fullmatch(r"\+\d{8,15}", p):
        return p, None
    return None, "Enter a valid mobile number, for example 9876543210 or +919876543210."


@app.template_filter("mask_email")
def mask_email(value):
    if not value or "@" not in value:
        return ""
    name, domain = value.split("@", 1)
    return name[:1] + "***@" + domain


@app.template_filter("mask_phone")
def mask_phone(value):
    if not value:
        return ""
    return value[:3] + "******" + value[-2:] if len(value) > 6 else "***"


def short(text, limit=80):
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------
# Notifications (email + SMS)
# ---------------------------------------------------------------------
STATUS_PHRASES = {
    "under_review": "has been reviewed by our team",
    "in_progress": "is now in progress",
    "resolved": "has been marked as resolved",
    "rejected": "could not be accepted",
    "reopened": "has been reopened and will be looked at again",
}
OPEN_PHRASES = {
    "submitted": "is waiting for review",
    "under_review": "has already been reviewed by our team",
    "in_progress": "has already been reviewed and is in progress",
    "reopened": "is being looked at again",
}


def send_notifications(db, report, subject, email_text, sms_text):
    """Save a log row for each contact the person gave. Sent after the request finishes."""
    jobs = g.setdefault("jobs", [])
    for channel, recipient, text in (
        ("email", report["contact_email"], email_text),
        ("sms", report["contact_phone"], sms_text),
    ):
        if not recipient:
            continue
        cur = db.execute(
            "INSERT INTO notifications (report_id, channel, recipient, subject, message, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (report["id"], channel, recipient, subject, text, now_str()),
        )
        jobs.append({"id": cur.lastrowid, "channel": channel, "recipient": recipient,
                     "subject": subject, "message": text})


def notify_received(db, r):
    link = f"{BASE_URL}/track/{r['complaint_id']}"
    email = (
        f"Hello,\n\nWe received your report \"{r['title']}\".\n\n"
        f"Your complaint ID: {r['complaint_id']}\nTrack it here: {link}\n\n"
        "It shows as Unverified until an administrator reviews it. "
        "We will contact you when that happens.\n\nCivicConnect"
    )
    sms = f"CivicConnect: report received. Your ID is {r['complaint_id']}. We will update you after review."
    send_notifications(db, r, f"CivicConnect: report {r['complaint_id']} received", email, sms)


def notify_linked_on_submit(db, r, original):
    """Someone reported a problem that was already raised: tell them it is being handled."""
    phrase = OPEN_PHRASES.get(original["status"], "is being handled")
    link = f"{BASE_URL}/track/{r['complaint_id']}"
    email = (
        f"Hello,\n\nThank you for reporting \"{r['title']}\" (your ID: {r['complaint_id']}).\n\n"
        f"The same problem was already reported by someone else earlier, and it {phrase}. "
        "We have linked your report to it, so the problem is not handled twice and your report "
        "adds to its priority.\n\n"
        f"You will receive an update whenever its status changes. Track it here: {link}\n\nCivicConnect"
    )
    sms = (f"CivicConnect: the problem you reported ({r['complaint_id']}) was already raised by someone "
           f"else and {phrase}. Your report is linked. You will get updates.")
    send_notifications(db, r, f"CivicConnect: your report {r['complaint_id']} is linked to an existing issue",
                       email, sms)


def notify_status_change(db, r, new_status, note):
    """Tell the first reporter AND everyone whose report was linked to it."""
    phrase = STATUS_PHRASES.get(new_status)
    if not phrase:
        return
    note_line = f"\n\nNote from the team: {note}" if note else ""
    sms_note = f" Note: {short(note)}" if note else ""
    resolved = new_status == "resolved"

    link = f"{BASE_URL}/track/{r['complaint_id']}"
    email = f"Hello,\n\nYour report \"{r['title']}\" (ID {r['complaint_id']}) {phrase}.{note_line}"
    if resolved:
        email += f"\n\nPlease confirm the problem is really fixed: {link}"
    email += f"\n\nTrack it here: {link}\n\nCivicConnect"
    sms = f"CivicConnect: your report {r['complaint_id']} {phrase}.{sms_note}"
    if resolved:
        sms += " Please confirm the fix on the Track page."
    send_notifications(db, r, f"CivicConnect: update on report {r['complaint_id']}", email, sms)

    linked = db.execute("SELECT * FROM reports WHERE duplicate_of = ?", (r["id"],)).fetchall()
    for l in linked:
        l_link = f"{BASE_URL}/track/{l['complaint_id']}"
        l_email = (
            f"Hello,\n\nThe problem you reported (ID {l['complaint_id']}) was already raised earlier by "
            f"someone else, and it {phrase}.{note_line}\n\nTrack it here: {l_link}\n\nCivicConnect"
        )
        l_sms = f"CivicConnect: the problem you reported ({l['complaint_id']}) {phrase}.{sms_note}"
        send_notifications(db, l, f"CivicConnect: update on report {l['complaint_id']}", l_email, l_sms)


@app.after_request
def send_queued_notifications(response):
    jobs = g.pop("jobs", None)
    if jobs:
        notifier.dispatch(jobs, DB_PATH)
    return response


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_globals():
    return {"STATUS_LABELS": STATUS_LABELS, "CATEGORIES": CATEGORIES}


# ---------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/report", methods=["GET", "POST"])
def report():
    form = {}
    if request.method == "POST":
        form = request.form
        title = form.get("title", "").strip()
        description = form.get("description", "").strip()
        category = form.get("category", "")
        lat, lng, loc_error = parse_location(form.get("lat"), form.get("lng"))
        email = form.get("contact_email", "").strip() or None
        phone, phone_error = normalise_phone(form.get("contact_phone", ""))
        consent = form.get("consent") == "on"

        error = None
        if not 5 <= len(title) <= 120:
            error = "Title must be between 5 and 120 characters."
        elif not description or len(description) > 2000:
            error = "Description is required (maximum 2000 characters)."
        elif category not in CATEGORIES:
            error = "Choose a category."
        elif loc_error:
            error = loc_error
        elif email and (not EMAIL_RE.fullmatch(email) or len(email) > 254):
            error = "Enter a valid email address, or leave it empty."
        elif phone_error:
            error = phone_error
        elif (email or phone) and not consent:
            error = "Tick the box to confirm you agree to receive updates about this report."

        if error:
            flash(error, "error")
            return render_template("report.html", form=form, center=MAP_CENTER)

        db = get_db()
        dup_id = None
        original = None
        dup_raw = form.get("duplicate_of")
        if dup_raw is None and lat is not None:
            # The person was not shown a choice: link to the FIRST report raised nearby, if any.
            nearby = find_duplicates(db, category, lat, lng)
            if nearby:
                dup_raw = str(nearby[0]["id"])
        if dup_raw and dup_raw.strip().isdigit():
            original = db.execute(
                "SELECT * FROM reports WHERE id = ? AND duplicate_of IS NULL "
                "AND status IN ('submitted','under_review','in_progress','reopened')",
                (int(dup_raw),),
            ).fetchone()
            if original:
                dup_id = original["id"]

        image = save_image(request.files.get("image"))
        complaint_id = new_complaint_id(db)
        stamp = now_str()
        cur = db.execute(
            """INSERT INTO reports (complaint_id, title, description, category, lat, lng,
                                    image, duplicate_of, contact_email, contact_phone,
                                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (complaint_id, title, description, category, lat, lng, image, dup_id,
             email, phone, stamp, stamp),
        )
        add_history(db, cur.lastrowid, "submitted", "Report received. Not yet reviewed by an administrator.")
        if dup_id:
            db.execute(
                "UPDATE reports SET duplicate_count = duplicate_count + 1, updated_at = ? WHERE id = ?",
                (stamp, dup_id),
            )
        new_row = db.execute("SELECT * FROM reports WHERE id = ?", (cur.lastrowid,)).fetchone()
        if original:
            notify_linked_on_submit(db, new_row, original)
        else:
            notify_received(db, new_row)
        db.commit()
        return redirect(url_for("submitted", complaint_id=complaint_id))

    return render_template("report.html", form=form, center=MAP_CENTER)


@app.route("/submitted/<complaint_id>")
def submitted(complaint_id):
    row = get_db().execute(
        "SELECT complaint_id, duplicate_of, contact_email, contact_phone FROM reports WHERE complaint_id = ?",
        (complaint_id,)
    ).fetchone()
    if not row:
        abort(404)
    return render_template("submitted.html", r=row)


@app.route("/track")
def track():
    cid = request.args.get("id", "").strip().upper()
    if cid:
        return redirect(url_for("track_detail", complaint_id=cid))
    return render_template("track.html")


@app.route("/track/<complaint_id>")
def track_detail(complaint_id):
    db = get_db()
    r = db.execute("SELECT * FROM reports WHERE complaint_id = ?", (complaint_id.upper(),)).fetchone()
    if not r:
        flash("No report found with that ID. Check it and try again.", "error")
        return redirect(url_for("track"))
    original = None
    if r["duplicate_of"]:
        original = db.execute("SELECT * FROM reports WHERE id = ?", (r["duplicate_of"],)).fetchone()
    shown = original or r  # a linked report shows the progress of the report raised first
    rows = db.execute(
        "SELECT * FROM status_updates WHERE report_id = ? ORDER BY id", (shown["id"],)
    ).fetchall()
    # Never show another person's comments to a linked reporter
    history = [h for h in rows if not (original and (h["note"] or "").startswith("Citizen"))]
    return render_template("track_detail.html", r=r, shown=shown, original=original, history=history)


@app.route("/track/<complaint_id>/verify", methods=["POST"])
def verify_fix(complaint_id):
    db = get_db()
    r = db.execute("SELECT * FROM reports WHERE complaint_id = ?", (complaint_id.upper(),)).fetchone()
    if not r or r["status"] != "resolved" or r["duplicate_of"]:
        abort(400)
    comment = request.form.get("comment", "").strip()[:500]
    stamp = now_str()
    if request.form.get("fixed") == "yes":
        db.execute("UPDATE reports SET citizen_verified = 1, updated_at = ? WHERE id = ?", (stamp, r["id"]))
        add_history(db, r["id"], "resolved", "Citizen confirmed the issue is fixed. " + comment)
        flash("Thanks for confirming the fix.", "success")
    else:
        db.execute(
            "UPDATE reports SET status = 'reopened', citizen_verified = NULL, resolved_at = NULL, "
            "updated_at = ? WHERE id = ?",
            (stamp, r["id"]),
        )
        add_history(db, r["id"], "reopened", "Citizen reported the issue is NOT fixed. " + comment)
        flash("Report reopened. An administrator will look at it again.", "success")
    db.commit()
    return redirect(url_for("track_detail", complaint_id=r["complaint_id"]))


@app.route("/map")
def public_map():
    return render_template("map.html", center=MAP_CENTER)


# ---------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------
@app.route("/api/duplicates")
def api_duplicates():
    try:
        lat, lng = float(request.args["lat"]), float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify([])
    category = request.args.get("category", "")
    if category not in CATEGORIES:
        return jsonify([])
    return jsonify(find_duplicates(get_db(), category, lat, lng))


@app.route("/api/reports")
def api_reports():
    """Public map data: no description, no image, no ID, coordinates rounded (about 110 m)."""
    rows = get_db().execute(
        """SELECT title, category, status, is_verified, duplicate_count, lat, lng
           FROM reports
           WHERE lat IS NOT NULL AND duplicate_of IS NULL AND status != 'rejected'"""
    ).fetchall()
    return jsonify([
        {
            "title": r["title"],
            "category": CATEGORIES[r["category"]][0],
            "status": r["status"],
            "status_label": STATUS_LABELS[r["status"]],
            "verified": bool(r["is_verified"]),
            "duplicates": r["duplicate_count"],
            "lat": round(r["lat"], 3),
            "lng": round(r["lng"], 3),
        }
        for r in rows
    ])


def hotspot_rows(db):
    rows = db.execute(
        """SELECT category, ROUND(lat, 3) AS lat, ROUND(lng, 3) AS lng, COUNT(*) AS n
           FROM reports WHERE lat IS NOT NULL
           GROUP BY category, ROUND(lat, 3), ROUND(lng, 3)
           HAVING COUNT(*) >= 3 ORDER BY n DESC"""
    ).fetchall()
    return [
        {"category": CATEGORIES[r["category"]][0], "lat": r["lat"], "lng": r["lng"], "count": r["n"]}
        for r in rows
    ]


@app.route("/api/hotspots")
def api_hotspots():
    return jsonify(hotspot_rows(get_db()))


# ---------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
            session["admin"] = True
            nxt = request.args.get("next", "")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("dashboard")
            return redirect(nxt)
        flash("Wrong password.", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    flash("Logged out.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard")
@admin_required
def dashboard():
    db = get_db()
    status_filter = request.args.get("status", "")
    all_rows = db.execute("SELECT * FROM reports").fetchall()
    primary = [r for r in all_rows if r["duplicate_of"] is None]

    open_rows = [r for r in primary if r["status"] in OPEN_STATUSES]
    resolved_rows = [r for r in primary if r["status"] == "resolved"]

    days = []
    for r in resolved_rows:
        created, done = parse_time(r["created_at"]), parse_time(r["resolved_at"])
        if created and done:
            days.append((done - created).total_seconds() / 86400)
    avg_days = round(sum(days) / len(days), 1) if days else None

    queue = []
    for r in primary:
        if status_filter and r["status"] != status_filter:
            continue
        if not status_filter and r["status"] not in OPEN_STATUSES:
            continue
        score, reason = priority(r)
        queue.append({"r": r, "score": score, "reason": reason})
    queue.sort(key=lambda x: x["score"], reverse=True)

    by_category = {}
    for r in primary:
        label = CATEGORIES[r["category"]][0]
        by_category[label] = by_category.get(label, 0) + 1
    max_cat = max(by_category.values()) if by_category else 1

    stats = {
        "total": len(all_rows),
        "open": len(open_rows),
        "resolved": len(resolved_rows),
        "unverified": sum(1 for r in primary if not r["is_verified"] and r["status"] == "submitted"),
        "avg_days": avg_days,
    }
    return render_template(
        "dashboard.html",
        stats=stats,
        queue=queue,
        by_category=sorted(by_category.items(), key=lambda kv: kv[1], reverse=True),
        max_cat=max_cat,
        hotspots=hotspot_rows(db),
        status_filter=status_filter,
    )


@app.route("/admin/report/<int:report_id>", methods=["GET", "POST"])
@admin_required
def admin_report(report_id):
    db = get_db()
    r = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not r:
        abort(404)

    if request.method == "POST":
        new_status = request.form.get("status", "")
        note = request.form.get("note", "").strip()
        if new_status not in STATUS_LABELS or new_status == "submitted":
            flash("Choose a valid status.", "error")
        elif new_status in ("resolved", "rejected") and not note:
            flash("Add a note explaining the outcome before marking a report resolved or rejected.", "error")
        else:
            proof = save_image(request.files.get("proof"))
            stamp = now_str()
            add_history(db, r["id"], new_status, note or "Status updated.", proof)
            db.execute(
                """UPDATE reports SET status = ?, updated_at = ?,
                          is_verified = CASE WHEN is_verified = 1 OR ? IN ('under_review','in_progress','resolved')
                                             THEN 1 ELSE 0 END,
                          resolved_at = CASE WHEN ? = 'resolved' THEN ? ELSE NULL END,
                          citizen_verified = CASE WHEN ? = 'resolved' THEN NULL ELSE citizen_verified END
                   WHERE id = ?""",
                (new_status, stamp, new_status, new_status, stamp, new_status, r["id"]),
            )
            notify_status_change(db, r, new_status, note)
            db.commit()
            flash("Status updated, recorded in the timeline, and people were notified.", "success")
            return redirect(url_for("admin_report", report_id=r["id"]))

    history = db.execute("SELECT * FROM status_updates WHERE report_id = ? ORDER BY id", (r["id"],)).fetchall()
    linked = db.execute(
        "SELECT complaint_id, created_at FROM reports WHERE duplicate_of = ? ORDER BY id", (r["id"],)
    ).fetchall()
    original = None
    if r["duplicate_of"]:
        original = db.execute("SELECT id, title FROM reports WHERE id = ?", (r["duplicate_of"],)).fetchone()
    score, reason = priority(r)
    sent = db.execute(
        """SELECT n.*, rp.complaint_id FROM notifications n
           JOIN reports rp ON rp.id = n.report_id
           WHERE n.report_id = ? OR rp.duplicate_of = ?
           ORDER BY n.id DESC LIMIT 30""",
        (r["id"], r["id"]),
    ).fetchall()
    return render_template(
        "admin_report.html", r=r, history=history, linked=linked, original=original,
        score=score, reason=reason, sent=sent,
    )


@app.route("/admin/notifications")
@admin_required
def admin_notifications():
    rows = get_db().execute(
        """SELECT n.*, rp.complaint_id FROM notifications n
           JOIN reports rp ON rp.id = n.report_id
           ORDER BY n.id DESC LIMIT 100"""
    ).fetchall()
    return render_template(
        "admin_notifications.html", rows=rows,
        email_live=notifier.email_live(), sms_live=notifier.sms_live(),
    )


@app.errorhandler(404)
def not_found(_e):
    return render_template("message.html", title="Page not found",
                           text="That page does not exist. Check the link or go back home."), 404


@app.errorhandler(413)
def too_large(_e):
    return render_template("message.html", title="Image too large",
                           text="Photos must be 5 MB or smaller. Go back and choose a smaller photo."), 413


if __name__ == "__main__":
    app.run(debug=True)
