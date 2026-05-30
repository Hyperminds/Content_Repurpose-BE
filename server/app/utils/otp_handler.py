"""
OTP Handler for TrendZZo.
Sends verification emails via Resend API (HTTPS, works on all hosting platforms).
Falls back to Gmail SMTP if Resend is not configured.
"""

import os
import random
import asyncio
import httpx
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


def _build_email_html(name: str, otp_code: str) -> str:
    return f"""
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


async def send_otp_email(to_email: str, otp_code: str, name: str = "User") -> bool:
    """
    Send OTP email. Tries methods in order:
    1. Resend API (HTTPS — works on Render, Vercel, Railway, etc.)
    2. Gmail SMTP via aiosmtplib (works locally, blocked on some hosts)
    3. Gmail SMTP via smtplib in thread (last resort)
    4. Console log fallback (always succeeds)
    """
    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    smtp_email = os.getenv("SMTP_EMAIL", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    resend_from = os.getenv("RESEND_FROM_EMAIL", "TrendZZo <onboarding@resend.dev>")

    html = _build_email_html(name, otp_code)

    # ── Method 1: Resend API (recommended for deployment) ─────────────────────
    if resend_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {resend_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": resend_from,
                        "to": [to_email],
                        "subject": "TrendZZo — Verify Your Account",
                        "html": html,
                    },
                )
                if response.status_code in (200, 201):
                    print(f"[EMAIL] ✓ OTP sent to {to_email} via Resend")
                    return True
                else:
                    print(f"[EMAIL] Resend failed ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"[EMAIL] Resend error: {type(e).__name__}: {e}")

    # ── Method 2: aiosmtplib (async SMTP) ─────────────────────────────────────
    if smtp_email and smtp_password:
        msg = MIMEMultipart()
        msg["From"] = smtp_email
        msg["To"] = to_email
        msg["Subject"] = "TrendZZo — Verify Your Account"
        msg.attach(MIMEText(html, "html"))

        try:
            import aiosmtplib
            await aiosmtplib.send(
                msg,
                hostname="smtp.gmail.com",
                port=587,
                start_tls=True,
                username=smtp_email,
                password=smtp_password,
                timeout=10,
            )
            print(f"[EMAIL] ✓ OTP sent to {to_email} via aiosmtplib")
            return True
        except ImportError:
            pass
        except Exception as e:
            print(f"[EMAIL] aiosmtplib failed: {type(e).__name__}: {e}")

        # ── Method 3: smtplib in thread (try port 465 SSL — some hosts block 587 but allow 465) ──
        try:
            import smtplib

            def _send():
                try:
                    # Try port 465 (SSL) first — works on more hosting platforms
                    server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
                    server.login(smtp_email, smtp_password)
                    server.sendmail(smtp_email, to_email, msg.as_string())
                    server.quit()
                except Exception:
                    # Fall back to port 587 (STARTTLS)
                    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(smtp_email, smtp_password)
                    server.sendmail(smtp_email, to_email, msg.as_string())
                    server.quit()

            await asyncio.get_event_loop().run_in_executor(None, _send)
            print(f"[EMAIL] ✓ OTP sent to {to_email} via smtplib")
            return True
        except Exception as e:
            print(f"[EMAIL] smtplib failed: {type(e).__name__}: {e}")

    # ── Method 4: Console fallback ────────────────────────────────────────────
    print(f"[EMAIL] ⚠ All methods failed. OTP for {to_email}: {otp_code}")
    return True
