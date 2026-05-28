import os
import random
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone


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


def _get_smtp_creds():
    """Read SMTP credentials at call time (not import time) to ensure env is loaded."""
    email = os.getenv("SMTP_EMAIL", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    return email, password


async def send_otp_email(to_email: str, otp_code: str, name: str = "User") -> bool:
    """
    Send OTP via Gmail SMTP.
    Uses aiosmtplib if available, falls back to sync smtplib in thread.
    Always returns True so auth flow doesn't break — logs OTP to console as fallback.
    """
    smtp_email, smtp_password = _get_smtp_creds()

    if not smtp_email or not smtp_password:
        print(f"[OTP] ⚠ SMTP not configured (SMTP_EMAIL={repr(smtp_email)}, password_len={len(smtp_password)})")
        print(f"[OTP] OTP for {to_email}: {otp_code}")
        return True

    msg = MIMEMultipart()
    msg["From"] = smtp_email
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

    print(f"[SMTP] Attempting to send OTP to {to_email} via {smtp_email}...")

    # Method 1: aiosmtplib (async, preferred)
    try:
        import aiosmtplib
        await aiosmtplib.send(
            msg,
            hostname="smtp.gmail.com",
            port=587,
            start_tls=True,
            username=smtp_email,
            password=smtp_password,
            timeout=15,
        )
        print(f"[SMTP] ✓ OTP sent to {to_email} (aiosmtplib)")
        return True
    except ImportError:
        pass  # aiosmtplib not installed, try sync
    except Exception as e:
        print(f"[SMTP] aiosmtplib failed: {type(e).__name__}: {e}")
        # Fall through to sync method

    # Method 2: smtplib in thread (sync fallback)
    try:
        import smtplib

        def _send_sync():
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_email, smtp_password)
            server.sendmail(smtp_email, to_email, msg.as_string())
            server.quit()

        await asyncio.get_event_loop().run_in_executor(None, _send_sync)
        print(f"[SMTP] ✓ OTP sent to {to_email} (smtplib sync)")
        return True
    except Exception as e:
        print(f"[SMTP ERROR] {type(e).__name__}: {e}")
        print(f"[OTP FALLBACK] OTP for {to_email}: {otp_code}")
        return True
