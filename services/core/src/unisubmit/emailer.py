import smtplib
from email.message import EmailMessage
from pathlib import Path
from .config import settings

def send_submission_email(to_email: str, subject: str, body: str, attachments: list[str]):
    if not settings.smtp_host:
        return False, "SMTP not configured. Email was not sent."
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        p = Path(path)
        if not p.exists():
            continue
        data = p.read_bytes()
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=p.name)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)
    return True, "Email sent."
