import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_EMAIL = "devanshugarad1@gmail.com"
SMTP_PASSWORD = "vtodcbqbrudoahyr"
TO_EMAIL = "devanshugarad1@gmail.com"  # Send to yourself as test

msg = MIMEMultipart()
msg["From"] = SMTP_EMAIL
msg["To"] = TO_EMAIL
msg["Subject"] = "Content Repurposer - Test OTP"

body = """
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2>Test OTP Email</h2>
    <p>Your verification code is:</p>
    <div style="background: #f8da95; padding: 20px; border-radius: 12px; text-align: center;">
        <h1 style="font-size: 36px; letter-spacing: 8px;">123456</h1>
    </div>
    <p>If you received this, email sending works!</p>
</body>
</html>
"""

msg.attach(MIMEText(body, "html"))

try:
    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(SMTP_EMAIL, SMTP_PASSWORD)
    server.sendmail(SMTP_EMAIL, TO_EMAIL, msg.as_string())
    server.quit()
    print(f"✓ Email sent successfully to {TO_EMAIL}")
except Exception as e:
    print(f"✗ Failed: {e}")
