import os, smtplib, socket, ssl, json, urllib.request, urllib.error
from email.mime.text import MIMEText
import certifi

APP_NAME = os.getenv("APP_NAME", "Changuito Beta")
MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@example.com")

# ---- SMTP config ----
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_TLS  = os.getenv("SMTP_TLS", "1") == "1"   # STARTTLS (587 / 2525)
SMTP_SSL  = os.getenv("SMTP_SSL", "0") == "1"   # SSL directo (465)
SMTP_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "10"))
SMTP_DRY_RUN = os.getenv("SMTP_DRY_RUN", "0") == "1"

# ---- SendGrid Web API config ----
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY") or os.getenv("SMTP_PASS")
USE_SENDGRID_API = os.getenv("USE_SENDGRID_API", "0") == "1"

def _console_mail(to_email: str, subject: str, body: str):
    print(f"[MAIL->console] To:{to_email} | Subject:{subject}\n{body}")

def _cert_context() -> ssl.SSLContext:
    # Contexto TLS usando el bundle de certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    return ctx

def _send_via_sendgrid_api(to_email: str, subject: str, body: str):
    if not SENDGRID_API_KEY:
        raise RuntimeError("Falta SENDGRID_API_KEY para SendGrid API")

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": MAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # Usa contexto TLS con certifi
    with urllib.request.urlopen(req, timeout=15, context=_cert_context()) as resp:
        if resp.status not in (200, 202):
            raise RuntimeError(f"SendGrid API status {resp.status}")
    print(f"[MAIL->sendgrid API OK] To:{to_email} From:{MAIL_FROM}")

def _send_via_smtp(to_email: str, subject: str, body: str):
    if SMTP_DRY_RUN or not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        reason = "DRY_RUN" if SMTP_DRY_RUN else "MISSING_CREDS"
        print(f"[MAIL] Usando consola ({reason}). HOST={SMTP_HOST!r} USER={SMTP_USER!r}")
        _console_mail(to_email, subject, body)
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = to_email

    if SMTP_SSL:
        with smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT, context=_cert_context()
        ) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(MAIL_FROM, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as s:
            s.ehlo()
            if SMTP_TLS:
                s.starttls(context=_cert_context())
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(MAIL_FROM, [to_email], msg.as_string())

    print(f"[MAIL->smtp OK] To:{to_email} From:{MAIL_FROM} Host:{SMTP_HOST}:{SMTP_PORT} TLS={SMTP_TLS} SSL={SMTP_SSL}")

def send_email(to_email: str, subject: str, body: str, strict: bool = False):
    if not to_email:
        if strict: raise ValueError("to_email vacío")
        print("[MAIL][WARN] to_email vacío"); return
    if not MAIL_FROM:
        if strict: raise ValueError("MAIL_FROM vacío")
        print("[MAIL][WARN] MAIL_FROM vacío"); return

    try:
        if USE_SENDGRID_API:
            _send_via_sendgrid_api(to_email, subject, body)
            return
        _send_via_smtp(to_email, subject, body)
    except (socket.timeout, OSError, smtplib.SMTPException, urllib.error.URLError, RuntimeError) as e:
        print(f"[MAIL][ERROR] Canal primario falló: {type(e).__name__}: {e}")
        try:
            if USE_SENDGRID_API:
                _send_via_smtp(to_email, subject, body)
            else:
                _send_via_sendgrid_api(to_email, subject, body)
            return
        except Exception as e2:
            print(f"[MAIL][ERROR] Fallback falló: {type(e2).__name__}: {e2}")
            if strict:
                raise
            _console_mail(to_email, subject, body)

def send_otp_email(to_email: str, code: str, purpose: str, strict: bool = False):
    subject = f"[{APP_NAME}] Código OTP para {('registro' if purpose=='verify' else 'ingreso')}"
    body = (
        f"Tu código OTP es: {code}\n\n"
        f"Es válido por 10 minutos. Si no fuiste vos, ignorá este mensaje.\n\n"
        f"{APP_NAME}"
    )
    send_email(to_email, subject, body, strict=strict)