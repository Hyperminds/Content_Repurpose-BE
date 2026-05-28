import os
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

# Read directly from os.getenv — env vars are set by Render/deployment platform
# No dotenv load needed here; config.py handles that at startup
SMTP_EMAIL = os.getenv("SMTP_EMAIL", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")


def generate_otp() -> str:
    """Generate a 6-digit OTP."""
    return str(random.randint(100000, 999999))


def get_otp_expiry() -> datetime:
    """OTP expires in 10 minutes."""
    return datetime.now(timezone.utc) + timedelta(minutes=10)


def is_otp_expired(expiry: datetime) -> bool:
    """Check if OTP has expired."""
    if expiry is None:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expiry


async def send_otp_email(to_email: str, otp_code: str, name: str = "User") -> bool:
    """
    Send OTP via Gmail SMTP using aiosmtplib (async, non-blocking).
    Falls back to console log if SMTP not configured.
    """
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"[OTP] No SMTP configured — OTP for {to_email}: {otp_code}")
        return True

    msg = MIMEMultipart()
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email
    msg["Subject"] = "TrendZZo — Verify Your Account"

    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px; background: #0F0F1E; color: #F0EEFF;">
        <div style="max-width: 480px; margin: 0 auto; background: #12121E; border-radius: 16px; padding: 32px; border: 1px solid rgba(124,58,237,0.3);">
            <h2 style="color: #A78BFA; margin-top: 0;">Welcome to TrendZZo</h2>
            <p style="color: #8B8BAA;">Hi {name},</p>
            <p style="color: #8B8BAA;">Your verification code is:</p>
            <div style="background: linear-gradient(135deg, rgba(124,58,237,0.2), rgba(6,182,212,0.1)); padding: 24px; border-radius: 12px; text-align: center; margin: 24px 0; border: 1px solid rgba(124,58,237,0.3);">
                <h1 style="font-size: 40px; letter-spacing: 10px; margin: 0; color: #A78BFA; font-family: monospace;">{otp_code}</h1>
            </div>
            <p style="color: #8B8BAA;">This code expires in <strong style="color: #F0EEFF;">10 minutes</strong>.</p>
            <p style="color: #8B8BAA;">If you didn't request this, please ignore this email.</p>
            <hr style="border-color: rgba(124,58,237,0.2); margin: 24px 0;">
            <p style="color: #4A4A6A; font-size: 12px;">TrendZZo by Hyperminds.tech</p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(body, "html"))

    try:
        # Try async first (aiosmtplib)
        try:
            import aiosmtplib
            await aiosmtplib.send(
                msg,
                hostname="smtp.gmail.com",
                port=587,
                start_tls=True,
                username=SMTP_EMAIL,
                password=SMTP_PASSWORD,
            )
            print(f"[SMTP] ✓ OTP sent to {to_email}")
            return True
        except ImportError:
            # aiosmtplib not installed — fall back to sync in thread
            import asyncio
            import smtplib
            def _send_sync():
                server = smtplib.SMTP("smtp.gmail.com", 587)
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_EMAIL, SMTP_PASSWORD)
                server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
                server.quit()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _send_sync)
            print(f"[SMTP] ✓ OTP sent to {to_email} (sync fallback)")
            return True

    except Exception as e:
        print(f"[SMTP ERROR] {type(e).__name__}: {e}")
        print(f"[OTP FALLBACK] OTP for {to_email}: {otp_code}")
        # Return True so signup doesn't fail — user sees OTP in server logs
        return True
