from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(40), nullable=False, default="")
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="receiver")
    address = db.Column(db.String(300), nullable=False, default="")
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    account_status = db.Column(db.String(16), nullable=False, default="active")
    contact_sharing_enabled = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    donations = db.relationship("FoodDonation", back_populates="donor", cascade="all, delete-orphan")
    requests = db.relationship("FoodRequest", back_populates="receiver", cascade="all, delete-orphan")
    notifications = db.relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    reports = db.relationship("ContentReport", back_populates="reporter", foreign_keys="ContentReport.reporter_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class FoodDonation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    donor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    food_name = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(40), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    quantity = db.Column(db.Float, nullable=False)
    quantity_remaining = db.Column(db.Float, nullable=False)
    quantity_unit = db.Column(db.String(24), nullable=False, default="servings")
    people_served = db.Column(db.Integer)
    image = db.Column(db.String(255))
    preparation_datetime = db.Column(db.DateTime, nullable=False)
    expiry_datetime = db.Column(db.DateTime, nullable=False)
    pickup_address = db.Column(db.String(300), nullable=False)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    availability_time = db.Column(db.String(120), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    donor = db.relationship("User", back_populates="donations")
    requests = db.relationship("FoodRequest", back_populates="donation", cascade="all, delete-orphan")
    reports = db.relationship("ContentReport", back_populates="donation", cascade="all, delete-orphan")


class FoodRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    donation_id = db.Column(db.Integer, db.ForeignKey("food_donation.id"), nullable=False, index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    requested_quantity = db.Column(db.Float, nullable=False)
    message = db.Column(db.String(500), nullable=False, default="")
    request_status = db.Column(db.String(16), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    donation = db.relationship("FoodDonation", back_populates="requests")
    receiver = db.relationship("User", back_populates="requests")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    message = db.Column(db.String(300), nullable=False)
    notification_type = db.Column(db.String(32), nullable=False, default="info")
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    user = db.relationship("User", back_populates="notifications")


class ContactMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class ContentReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    donation_id = db.Column(db.Integer, db.ForeignKey("food_donation.id"), index=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    reason = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(1000), nullable=False, default="")
    status = db.Column(db.String(16), nullable=False, default="open")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    reporter = db.relationship("User", back_populates="reports", foreign_keys=[reporter_id])
    target_user = db.relationship("User", foreign_keys=[target_user_id])
    donation = db.relationship("FoodDonation", back_populates="reports")
