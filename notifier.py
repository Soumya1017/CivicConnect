"""
Sends email and SMS notifications in a background thread so pages stay fast.

Modes (decided by settings in your .env file):
  - Email: LIVE if SMTP_HOST, SMTP_USER and SMTP_PASSWORD are set, otherwise SIMULATED.
  - SMS:   LIVE if SMS_PROVIDER=twilio and the TWILIO_* settings are set, otherwise SIMULATED.

SIMULATED means the message is not sent. It is saved in the notification log
(Admin > Notification log) and printed in the terminal, so you can still demo the feature.
"""
import base64
import os
import smtplib
import sqlite3
import ssl
import threading
from email.message import EmailMessage
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


def email_live():
    return all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))


def sms_live():
    return os.environ.get("SMS_PROVIDER", "").lower() == "twilio" and all(
        os.environ.get(k) for k in ("TWILIO_SID", "TWILIO_TOKEN", "TWILIO_FROM")
    )


def send_email(to, subject, body):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM", user)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)


def send_sms(to, text):
    sid = os.environ["TWILIO_SID"]
    token = os.environ["TWILIO_TOKEN"]
    sender = os.environ["TWILIO_FROM"]

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urlparse.urlencode({"To": to, "From": sender, "Body": text}).encode()
    req = urlrequest.Request(url, data=data, method="POST")
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", "Basic " + auth)
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            resp.read()
    except urlerror.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"Twilio error {exc.code}: {detail}") from None


def deliver(job, db_path):
    """Send one notification and record the result in the notifications table."""
    status, err = "simulated", None
    try:
        if job["channel"] == "email":
            if email_live():
                send_email(job["recipient"], job["subject"], job["message"])
                status = "sent"
        else:
            if sms_live():
                send_sms(job["recipient"], job["message"])
                status = "sent"
        if status == "simulated":
            one_line = " ".join(job["message"].split())[:150]
            print(f"[SIMULATED {job['channel'].upper()}] to {job['recipient']}: {one_line}")
    except Exception as exc:  # never crash the app because a message failed
        status, err = "failed", str(exc)[:300]
        print(f"[FAILED {job['channel'].upper()}] to {job['recipient']}: {err}")

    try:
        db = sqlite3.connect(db_path, timeout=10)
        db.execute("UPDATE notifications SET status = ?, error = ? WHERE id = ?", (status, err, job["id"]))
        db.commit()
        db.close()
    except sqlite3.Error as exc:
        print(f"Could not update notification log: {exc}")


def dispatch(jobs, db_path):
    for job in jobs:
        threading.Thread(target=deliver, args=(job, db_path), daemon=True).start()
