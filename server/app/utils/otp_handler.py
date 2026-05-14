import os
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the server directory explicitly
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

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
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expiry


async def send_otp_email(to_email: str, otp_code: str, name: str = "User") -> bool:
    """Send OTP via Gmail SMTP."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"[DEV MODE - No SMTP configured] OTP for {to_email}: {otp_code}")
        return True

    try:
        print(f"[SMTP] Sending OTP to {to_email} from {SMTP_EMAIL}...")

        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = to_email
        msg["Subject"] = "Content Repurposer - Verify Your Account"

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #333;">Welcome to Content Repurposer!</h2>
            <p>Hi {name},</p>
            <p>Your verification code is:</p>
            <div style="background: #f8da95; padding: 20px; border-radius: 12px; text-align: center; margin: 20px 0;">
                <h1 style="font-size: 36px; letter-spacing: 8px; margin: 0; color: #000;">{otp_code}</h1>
            </div>
            <p>This code expires in <strong>10 minutes</strong>.</p>
            <p>If you didn't request this, please ignore this email.</p>
            <br>
            <p style="color: #888; font-size: 12px;">Content Repurposer Team</p>
        </body>
        </html>
        """

        msg.attach(MIMEText(body, "html"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        server.quit()

        print(f"[SMTP] ✓ OTP sent successfully to {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"[SMTP ERROR] Authentication failed. Check your Gmail App Password.")
        print(f"  Email: {SMTP_EMAIL}")
        print(f"  Password length: {len(SMTP_PASSWORD)} chars")
        print(f"  Error: {e}")
        print(f"[DEV MODE] OTP for {to_email}: {otp_code}")
        return True

    except Exception as e:
        print(f"[SMTP ERROR] Failed to send email: {type(e).__name__}: {e}")
        print(f"[DEV MODE] OTP for {to_email}: {otp_code}")
        return True
