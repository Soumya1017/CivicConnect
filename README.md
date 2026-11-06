# CivicConnect

CivicConnect is a web app that lets citizens report local civic issues (such as water leakage) and track them until they are fixed. Administrators review reports, update their status with notes, and the people involved are notified at every step.

## Features

- **Report an issue**: category, location pin, email, 10-digit mobile number and consent.
- **Duplicate linking**: if someone reports the same problem at the same spot, they can link their report to an existing one, so everyone affected is kept informed.
- **Admin dashboard**: review reports and move them through Under review, In progress and Resolved, with a note at each step.
- **Track page**: citizens look up a report by its ID and, once resolved, confirm with "Yes, it is fixed".
- **Notifications**: email and SMS messages sent at each status change to the original reporter and to everyone whose report is linked to it.
- **Notification log** at `/admin/notifications`, showing every message sent and whether email is Live or Simulated.

## How to run

1. Install the dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the project folder and set the admin password:

   ```
   ADMIN_PASSWORD=your_password_here
   ```

3. (Optional) Enable real email by adding your SMTP details to `.env`:

   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=yourname@gmail.com
   SMTP_PASSWORD=your_app_password
   SMTP_FROM=CivicConnect <yourname@gmail.com>
   ```

   Without these, email runs in simulated mode.

4. Start the server:

   ```
   python app.py
   ```

## Configuration notes

- The admin password is set in `.env` and is never stored in the code.
- `.env` and `database.db` are not committed to the repository.
- SMS is currently simulated. Sending real SMS in India requires DLT registration with a provider.

## Future work

- Staff accounts with individual logins and roles, replacing the single admin password.
- A real SMS provider (with DLT registration) for live SMS delivery.