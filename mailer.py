"""Sends the 6-digit login code. Real email only if SMTP_USER/SMTP_PASSWORD are set; otherwise demo mode."""

import smtplib
from email.message import EmailMessage

from config import SMTP_PASSWORD, SMTP_USER


def send_code(to: str, code: str) -> bool:
    """Returns True if an email was sent, False if running in demo mode."""
    if not (SMTP_USER and SMTP_PASSWORD):
        print(f"[demo mode] login code for {to}: {code}")
        return False
    msg = EmailMessage()
    msg["Subject"] = f"Your Bulldog Circles code: {code}"
    msg["From"] = SMTP_USER
    msg["To"] = to
    msg.set_content(f"Your Bulldog Circles login code is {code}. It expires in 10 minutes.\n\n"
                    "If you did not ask for this, you can ignore this email.")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)
    return True
