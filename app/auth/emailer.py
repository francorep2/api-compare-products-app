# app/auth/emailer.py
import os, smtplib
from email.mime.text import MIMEText

APP_NAME = os.getenv("APP_NAME", "Comparador Super")
MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@example.com")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_TLS  = os.getenv("SMTP_TLS", "1") == "1"

def send_email(to_email: str, subject: str, body: str):
    # Modo desarrollo: sin SMTP, imprime en consola
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print(f"[MAIL->console] To:{to_email} | Subject:{subject}\n{body}")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if SMTP_TLS:
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(MAIL_FROM, [to_email], msg.as_string())

def send_otp_email(to_email: str, code: str, purpose: str):
    subject = f"[{APP_NAME}] Código OTP para {('registro' if purpose=='verify' else 'ingreso')}"
    body = (
        f"Tu código OTP es: {code}\n\n"
        f"Es válido por 10 minutos. Si no fuiste vos, ignorá este mensaje.\n\n"
        f"{APP_NAME}"
    )
    send_email(to_email, subject, body)
