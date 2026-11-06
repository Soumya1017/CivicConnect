import smtplib, os, threading
from email.message import EmailMessage


def send_email(to, subject, body):
    msg = EmailMessage()
    msg["From"] = os.getenv("MAIL_USER")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(os.getenv("MAIL_USER"), os.getenv("MAIL_PASS"))
        s.send_message(msg)


def send_sms(to, body):
    sid = os.getenv("TWILIO_SID")
    token = os.getenv("TWILIO_TOKEN")
    number = os.getenv("TWILIO_NUMBER")
    if not (sid and token and number):
        return  # SMS not set up yet, skip quietly
    from twilio.rest import Client
    Client(sid, token).messages.create(to=to, from_=number, body=body)


def _send_all(messages):
    """messages = list of (email, phone, text). Runs in a background thread."""
    for email, phone, text in messages:
        try:
            if email:
                send_email(email, "Complaint update", text)
            if phone:
                send_sms(phone, text)
        except Exception as e:
            print("Notification failed:", e)


def notify_report(db, r, new_status, note=""):
    label = new_status.replace("_", " ")
    extra = f" Note: {note}" if note else ""
    messages = []

    # the person who owns this report
    messages.append((r["email"], r["phone"],
        f"Your complaint {r['complaint_id']} is now: {label}.{extra}"))

    # everyone who reported the same problem later
    linked = db.execute(
        "SELECT complaint_id, email, phone FROM reports WHERE duplicate_of = ?",
        (r["id"],),
    ).fetchall()
    for d in linked:
        messages.append((d["email"], d["phone"],
            f"Your complaint {d['complaint_id']} is the same problem already "
            f"reported by another citizen. Status: {label}.{extra}"))

    threading.Thread(target=_send_all, args=(messages,)).start()