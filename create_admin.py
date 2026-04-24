import os
from app import app, db, User
from dotenv import load_dotenv

load_dotenv()

email = os.getenv("ADMIN_EMAIL")
password = os.getenv("ADMIN_PASSWORD")

with app.app_context():   # 🔥 THIS FIXES YOUR ERROR

    if not email or not password:
        print("Missing admin credentials in environment variables")
        exit()

    existing = User.query.filter_by(email=email).first()

    if existing:
        print("Admin already exists")
        exit()

    admin = User(
        email=email,
        name="Admin",
        is_admin=True
    )

    admin.set_password(password)

    db.session.add(admin)
    db.session.commit()

    print("Admin created successfully")