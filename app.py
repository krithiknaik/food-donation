import hmac
import math
import os
import re
import secrets
import uuid
from datetime import datetime
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for,
)
from sqlalchemy import func, inspect, or_, text, update
from werkzeug.utils import secure_filename

from config import Config
from models import ContentReport, ContactMessage, FoodDonation, FoodRequest, Notification, User, db, utcnow

ALLOWED_CATEGORIES = (
    "Prepared meals",
    "Bakery",
    "Dairy",
    "Produce",
    "Fruit",
    "Vegetables",
    "Pantry",
    "Frozen",
    "Beverages",
    "Snacks",
    "Meat",
    "Seafood",
    "Other",
)
REMOVED_CATEGORIES = ()
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if app.config.get("APP_ENV") == "production" and app.config["SECRET_KEY"] == "local-development-key-change-me":
        raise RuntimeError("Set a unique SECRET_KEY before running FoodShare in production.")
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    database_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if database_uri.startswith("sqlite:///"):
        db_path = database_uri.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db.init_app(app)

    @app.context_processor
    def inject_shared_data():
        current_user = get_current_user()
        unread_count = 0
        if current_user:
            unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        return {
            "current_user": current_user,
            "unread_count": unread_count,
            "current_year": datetime.now().year,
            "csrf_token": csrf_token,
            "categories": ALLOWED_CATEGORIES,
        }

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            expected = session.get("_csrf_token", "")
            supplied = request.form.get("csrf_token", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                abort(400, description="Your form has expired. Refresh the page and try again.")

    @app.before_request
    def refresh_session_user():
        if request.endpoint != "static":
            expire_old_donations()
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user is None or user.account_status != "active":
                session.clear()
                if request.endpoint not in ("login", "register", "static"):
                    flash("Your account is unavailable. Please sign in or contact support.", "error")

    @app.errorhandler(413)
    def upload_too_large(error):
        flash(f"{error.description} The maximum upload size is 5 MB.", "error")
        return redirect(request.referrer or url_for("new_donation")), 303

    @app.route("/")
    def home():
        expire_old_donations()
        donations = FoodDonation.query.join(User).filter(
            FoodDonation.status == "available", User.account_status == "active",
            FoodDonation.expiry_datetime > utcnow()
        ).order_by(FoodDonation.created_at.desc()).limit(6).all()
        stats = {
            "meals": db.session.query(func.coalesce(func.sum(FoodDonation.quantity - FoodDonation.quantity_remaining), 0)).scalar(),
            "donations": FoodDonation.query.filter(FoodDonation.status.in_(("available", "claimed", "completed"))).count(),
            "partners": User.query.filter_by(role="donor", account_status="active").count(),
        }
        return render_template("index.html", donations=donations, stats=stats)

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            message = request.form.get("message", "").strip()
            if not name or not valid_email(email) or not 10 <= len(message) <= 3000:
                flash("Please enter a valid name, email, and message of at least 10 characters.", "error")
            else:
                db.session.add(ContactMessage(name=name[:120], email=email[:254], message=message))
                db.session.commit()
                flash("Thanks for reaching out. Your message has been received.", "success")
                return redirect(url_for("contact"))
        return render_template("contact.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        next_url = request.form.get("next") or request.args.get("next", "")
        if get_current_user():
            return redirect(post_auth_destination(get_current_user(), next_url))
        if request.method == "POST":
            name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            role = request.form.get("role", "")
            address = request.form.get("address", "").strip()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            if not name or len(name) > 120 or not valid_email(email):
                flash("Enter your name and a valid email address.", "error")
            elif not valid_phone(phone):
                flash("Enter a phone number containing exactly 10 digits.", "error")
            elif role not in ("donor", "receiver"):
                flash("Choose whether you are donating or receiving food.", "error")
            elif not address:
                flash("Enter your address.", "error")
            elif len(password) < 10:
                flash("Use a password with at least 10 characters.", "error")
            elif password != confirm:
                flash("The passwords do not match.", "error")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists.", "error")
            else:
                user = User(full_name=name, email=email, phone=phone[:40], role=role, address=address[:300])
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                session.clear()
                session["user_id"] = user.id
                flash("Welcome to FoodShare. Your account is ready.", "success")
                return redirect(post_auth_destination(user, next_url))
        return render_template("auth.html", mode="register", next_url=next_url)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        next_url = request.form.get("next") or request.args.get("next", "")
        if get_current_user():
            return redirect(post_auth_destination(get_current_user(), next_url))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user is None or not user.check_password(password) or user.account_status != "active":
                flash("The email or password is incorrect, or the account is inactive.", "error")
            else:
                session.clear()
                session["user_id"] = user.id
                flash(f"Welcome back, {user.full_name.split()[0]}.", "success")
                return redirect(post_auth_destination(user, next_url))
        return render_template("auth.html", mode="login", next_url=next_url)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("home"))

    @app.route("/account/contact-sharing", methods=["POST"])
    @login_required
    def update_contact_sharing():
        user = get_current_user()
        if user.role not in ("donor", "receiver"):
            abort(403)
        user.contact_sharing_enabled = request.form.get("enabled") == "1"
        db.session.commit()
        if user.contact_sharing_enabled:
            flash("Your phone number can now be shared with opted-in accounts in the other role.", "success")
        else:
            flash("Phone sharing is turned off.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        user = get_current_user()
        if user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        if user.role == "donor":
            return redirect(url_for("donor_dashboard"))
        return redirect(url_for("receiver_dashboard"))

    @app.route("/donor")
    @role_required("donor")
    def donor_dashboard():
        user = get_current_user()
        donations = FoodDonation.query.filter_by(donor_id=user.id).order_by(FoodDonation.created_at.desc()).all()
        donation_ids = [donation.id for donation in donations]
        requests = FoodRequest.query.filter(FoodRequest.donation_id.in_(donation_ids)).order_by(
            FoodRequest.created_at.desc()
        ).all() if donation_ids else []
        counts = {
            "all": len(donations),
            "available": sum(d.status == "available" for d in donations),
            "pending": sum(r.request_status == "pending" for r in requests),
            "completed": sum(d.status == "completed" for d in donations),
        }
        return render_template("dashboard.html", mode="donor", donations=donations, requests=requests, counts=counts)

    @app.route("/receiver")
    @role_required("receiver")
    def receiver_dashboard():
        expire_old_donations()
        query = FoodDonation.query.join(User).filter(
            FoodDonation.status == "available",
            FoodDonation.expiry_datetime > utcnow(),
            User.account_status == "active",
        )
        category = request.args.get("category", "")
        location = request.args.get("location", "").strip()
        search = request.args.get("q", "").strip()
        minimum = request.args.get("min_quantity", "").strip()
        if category in ALLOWED_CATEGORIES:
            query = query.filter(FoodDonation.category == category)
        if location:
            query = query.filter(FoodDonation.pickup_address.ilike(f"%{escape_like(location)}%", escape="\\"))
        if search:
            query = query.filter(FoodDonation.food_name.ilike(f"%{escape_like(search)}%", escape="\\"))
        if minimum:
            try:
                query = query.filter(FoodDonation.quantity_remaining >= float(minimum))
            except ValueError:
                flash("Minimum quantity must be a number.", "error")
        donations = query.order_by(FoodDonation.created_at.desc()).all()
        requests = FoodRequest.query.filter_by(receiver_id=get_current_user().id).order_by(
            FoodRequest.created_at.desc()
        ).limit(8).all()
        return render_template("dashboard.html", mode="receiver", donations=donations, requests=requests,
                               selected_category=category, selected_location=location, search=search, minimum=minimum)

    @app.route("/donations/new", methods=["GET", "POST"])
    @role_required("donor")
    def new_donation():
        if request.method == "POST":
            form = request.form
            name = form.get("food_name", "").strip()
            category = form.get("category", "")
            description = form.get("description", "").strip()
            address = form.get("pickup_address", "").strip()
            unit = form.get("quantity_unit", "").strip()
            availability = form.get("availability_time", "").strip()
            try:
                quantity = float(form.get("quantity", ""))
                people_served = int(form.get("people_served", "")) if form.get("people_served", "").strip() else None
                prepared = parse_datetime(form.get("preparation_datetime", ""))
                expiry = parse_datetime(form.get("expiry_datetime", ""))
                latitude, longitude = parse_coordinates(form.get("latitude", ""), form.get("longitude", ""))
            except (ValueError, TypeError):
                flash("Check the quantity, people served, dates, and map coordinates.", "error")
                return render_template("donation_form.html")
            if not name or len(name) > 120 or category not in ALLOWED_CATEGORIES or not address:
                flash("Food name, a valid category, and pickup address are required.", "error")
            elif quantity <= 0 or quantity > 100000 or (people_served is not None and people_served <= 0):
                flash("Quantity and people served must be positive values.", "error")
            elif expiry <= utcnow() or prepared > expiry:
                flash("The expiry must be in the future and after the preparation time.", "error")
            elif not unit or len(unit) > 24:
                flash("Enter a quantity unit, such as servings, boxes, or kg.", "error")
            else:
                image_name, image_error = save_uploaded_image(request.files.get("image"), app.config["UPLOAD_FOLDER"])
                if image_error:
                    flash(image_error, "error")
                else:
                    donation = FoodDonation(
                        donor_id=get_current_user().id, food_name=name, category=category,
                        description=description[:3000], quantity=quantity, quantity_remaining=quantity,
                        quantity_unit=unit, people_served=people_served, image=image_name,
                        preparation_datetime=prepared, expiry_datetime=expiry, pickup_address=address[:300],
                        latitude=latitude, longitude=longitude, availability_time=availability[:120],
                        status="pending",
                    )
                    db.session.add(donation)
                    db.session.commit()
                    notify_admins(f"New donation awaiting review: {name}.", "moderation")
                    flash("Donation submitted for admin review. It will appear to receivers once approved.", "success")
                    return redirect(url_for("donor_dashboard"))
        return render_template("donation_form.html")

    @app.route("/donations/<int:donation_id>")
    def donation_details(donation_id):
        donation = db.get_or_404(FoodDonation, donation_id)
        user = get_current_user()
        if donation.donor.account_status != "active" and (not user or user.role != "admin"):
            abort(404)
        if donation.status not in ("available", "claimed", "completed"):
            if not user or (user.role != "admin" and user.id != donation.donor_id):
                abort(404)
        return render_template("food_details.html", donation=donation)

    @app.route("/reports/new", methods=["POST"])
    @login_required
    def create_report():
        report_type = request.form.get("target_type")
        reason = request.form.get("reason", "").strip()
        description = request.form.get("description", "").strip()
        if reason not in ("Food safety concern", "Misleading information", "Inappropriate content", "Other"):
            abort(400, description="Choose a valid report reason.")
        if not description or len(description) > 1000:
            flash("Please include a brief description of your concern (up to 1,000 characters).", "error")
            return redirect(request.referrer or url_for("home"))
        report = ContentReport(reporter_id=get_current_user().id, reason=reason, description=description)
        if report_type == "donation":
            report.donation = db.get_or_404(FoodDonation, request.form.get("target_id", type=int))
        elif report_type == "user":
            target = db.get_or_404(User, request.form.get("target_id", type=int))
            if target.id == get_current_user().id:
                abort(400, description="You cannot report your own account.")
            report.target_user = target
        else:
            abort(400, description="Choose a valid report target.")
        db.session.add(report)
        db.session.flush()
        notify_admins(f"A new {report_type} report requires review.", "report")
        db.session.commit()
        flash("Thanks for helping keep FoodShare safe. Your report was sent to an administrator.", "success")
        return redirect(request.referrer or url_for("home"))

    @app.route("/donations/<int:donation_id>/request", methods=["POST"])
    @role_required("receiver")
    def request_donation(donation_id):
        donation = db.get_or_404(FoodDonation, donation_id)
        expire_old_donations()
        quantity_raw = request.form.get("requested_quantity", "").strip()
        try:
            quantity = float(quantity_raw)
        except ValueError:
            flash("Enter a valid quantity.", "error")
            return redirect(url_for("donation_details", donation_id=donation.id))
        pending_total = db.session.query(func.coalesce(func.sum(FoodRequest.requested_quantity), 0)).filter_by(
            donation_id=donation.id, request_status="pending"
        ).scalar()
        receiver_id = get_current_user().id
        if donation.status != "available" or donation.expiry_datetime <= utcnow() \
                or donation.donor.account_status != "active":
            flash("This donation is no longer available.", "error")
        elif quantity <= 0 or quantity > donation.quantity_remaining - pending_total:
            flash("That quantity is unavailable. Check the remaining amount and pending requests.", "error")
        elif FoodRequest.query.filter_by(donation_id=donation.id, receiver_id=receiver_id,
                                         request_status="pending").first():
            flash("You already have a pending request for this donation.", "error")
        else:
            food_request = FoodRequest(
                donation_id=donation.id, receiver_id=receiver_id, requested_quantity=quantity,
                message=request.form.get("message", "").strip()[:500],
            )
            db.session.add(food_request)
            db.session.add(Notification(
                user_id=donation.donor_id, message=f"{get_current_user().full_name} requested {quantity:g} "
                f"{donation.quantity_unit} of {donation.food_name}.", notification_type="request",
            ))
            db.session.commit()
            flash("Your request has been sent to the donor.", "success")
        return redirect(url_for("receiver_dashboard"))

    @app.route("/requests")
    @role_required("receiver")
    def my_requests():
        requests_list = FoodRequest.query.filter_by(receiver_id=get_current_user().id).order_by(
            FoodRequest.created_at.desc()
        ).all()
        return render_template("requests.html", requests=requests_list)

    @app.route("/requests/<int:request_id>/decision", methods=["POST"])
    @role_required("donor")
    def decide_request(request_id):
        food_request = db.get_or_404(FoodRequest, request_id)
        donation = food_request.donation
        if donation.donor_id != get_current_user().id:
            abort(403)
        decision = request.form.get("decision")
        if food_request.request_status != "pending" or decision not in ("accepted", "rejected"):
            abort(400, description="This request can no longer be changed.")
        if decision == "accepted":
            if donation.status != "available" or donation.expiry_datetime <= utcnow() \
                    or donation.donor.account_status != "active":
                flash("This donation is no longer available.", "error")
                return redirect(url_for("donor_dashboard"))
            result = db.session.execute(
                update(FoodDonation)
                .where(FoodDonation.id == donation.id, FoodDonation.status == "available",
                       FoodDonation.quantity_remaining >= food_request.requested_quantity)
                .values(quantity_remaining=FoodDonation.quantity_remaining - food_request.requested_quantity)
            )
            if result.rowcount != 1:
                flash("Not enough food remains to accept this request.", "error")
                return redirect(url_for("donor_dashboard"))
            donation = db.session.get(FoodDonation, donation.id)
            if donation.quantity_remaining <= 0:
                donation.status = "claimed"
        food_request.request_status = decision
        db.session.add(Notification(
            user_id=food_request.receiver_id,
            message=f"Your request for {donation.food_name} was {decision}.",
            notification_type=decision,
        ))
        db.session.commit()
        flash(f"Request {decision}.", "success")
        return redirect(url_for("donor_dashboard"))

    @app.route("/donations/<int:donation_id>/complete", methods=["POST"])
    @role_required("donor")
    def complete_donation(donation_id):
        donation = db.get_or_404(FoodDonation, donation_id)
        if donation.donor_id != get_current_user().id:
            abort(403)
        if donation.status not in ("available", "claimed"):
            abort(400, description="Only active donations can be completed.")
        if any(r.request_status == "pending" for r in donation.requests):
            flash("Resolve pending requests before marking the donation completed.", "error")
        else:
            for accepted in donation.requests:
                if accepted.request_status == "accepted":
                    db.session.add(Notification(
                        user_id=accepted.receiver_id, message=f"Pickup of {donation.food_name} is marked complete.",
                        notification_type="completed",
                    ))
            donation.status = "completed"
            donation.quantity_remaining = 0
            db.session.commit()
            flash("Donation marked as completed.", "success")
        return redirect(url_for("donor_dashboard"))

    @app.route("/notifications")
    @login_required
    def notifications():
        items = Notification.query.filter_by(user_id=get_current_user().id).order_by(
            Notification.created_at.desc()
        ).limit(100).all()
        for item in items:
            item.is_read = True
        db.session.commit()
        return render_template("notifications.html", notifications=items)

    @app.route("/nearby")
    @login_required
    def nearby_food():
        expire_old_donations()
        viewer = get_current_user()
        donations = FoodDonation.query.join(User).filter(
            FoodDonation.status == "available", User.account_status == "active",
            FoodDonation.expiry_datetime > utcnow()
        ).all()
        map_items = [{
            "id": d.id, "name": d.food_name, "category": d.category, "quantity": d.quantity_remaining,
            "unit": d.quantity_unit, "address": d.pickup_address, "lat": d.latitude, "lng": d.longitude,
            "phone": d.donor.phone if (
                viewer.role == "receiver"
                and viewer.contact_sharing_enabled
                and d.donor.contact_sharing_enabled
            ) else "",
            "email": d.donor.email,
            "url": url_for("donation_details", donation_id=d.id),
        } for d in donations]
        return render_template("nearby.html", donations=donations, map_items=map_items)

    @app.route("/api/donations")
    @login_required
    def donation_api():
        expire_old_donations()
        donations = FoodDonation.query.join(User).filter(
            FoodDonation.status == "available", User.account_status == "active",
            FoodDonation.expiry_datetime > utcnow()
        ).all()
        latitude, longitude = request.args.get("lat"), request.args.get("lng")
        radius = request.args.get("radius_km")
        if (latitude is None) != (longitude is None):
            abort(400, description="Provide both latitude and longitude to search by distance.")
        origin = None
        if latitude is not None:
            try:
                origin = parse_coordinates(latitude, longitude)
                radius_value = float(radius) if radius is not None else 25
                if not 0 < radius_value <= 1000:
                    raise ValueError("Invalid radius")
            except ValueError:
                abort(400, description="Coordinates must be valid and radius_km must be between 0 and 1000.")
        result = []
        for donation in donations:
            distance = None
            if origin is not None:
                if donation.latitude is None or donation.longitude is None:
                    continue
                distance = distance_km(origin[0], origin[1], donation.latitude, donation.longitude)
                if distance > radius_value:
                    continue
            result.append({
                "id": donation.id, "name": donation.food_name, "category": donation.category,
                "quantity": donation.quantity_remaining, "unit": donation.quantity_unit,
                "address": donation.pickup_address, "latitude": donation.latitude,
                "longitude": donation.longitude, "distance_km": round(distance, 2) if distance is not None else None,
                "url": url_for("donation_details", donation_id=donation.id),
            })
        if origin is not None:
            result.sort(key=lambda item: item["distance_km"])
        return jsonify(result)

    @app.route("/admin")
    @role_required("admin")
    def admin_dashboard():
        stats = {
            "users": User.query.count(), "donors": User.query.filter_by(role="donor").count(),
            "receivers": User.query.filter_by(role="receiver").count(),
            "donations": FoodDonation.query.count(),
            "pending": FoodDonation.query.filter_by(status="pending").count(),
            "completed": FoodDonation.query.filter_by(status="completed").count(),
            "requests": FoodRequest.query.count(),
        }
        users = User.query.order_by(User.created_at.desc()).all()
        donations = FoodDonation.query.order_by(FoodDonation.created_at.desc()).all()
        requests_list = FoodRequest.query.order_by(FoodRequest.created_at.desc()).limit(50).all()
        messages = ContactMessage.query.order_by(ContactMessage.created_at.desc()).limit(20).all()
        reports = ContentReport.query.order_by(ContentReport.created_at.desc()).limit(100).all()
        donation_status_counts = {
            status: FoodDonation.query.filter_by(status=status).count()
            for status in ("pending", "available", "claimed", "completed", "expired", "rejected")
        }
        return render_template("admin.html", stats=stats, users=users, donations=donations,
                               requests=requests_list, messages=messages, reports=reports,
                               donation_status_counts=donation_status_counts,
                               chart_max=max(donation_status_counts.values(), default=0) or 1)

    @app.route("/admin/reports/<int:report_id>/status", methods=["POST"])
    @role_required("admin")
    def update_report_status(report_id):
        report = db.get_or_404(ContentReport, report_id)
        status = request.form.get("status")
        if report.status != "open" or status not in ("resolved", "dismissed"):
            abort(400, description="Only open reports can be resolved or dismissed.")
        report.status = status
        db.session.commit()
        flash(f"Report {status}.", "success")
        return redirect(url_for("admin_dashboard") + "#reports")

    @app.route("/admin/reports/<int:report_id>/delete", methods=["POST"])
    @role_required("admin")
    def delete_report(report_id):
        report = db.get_or_404(ContentReport, report_id)
        db.session.delete(report)
        db.session.commit()
        flash("Report deleted.", "success")
        return redirect(url_for("admin_dashboard") + "#reports")

    @app.route("/admin/users/<int:user_id>/status", methods=["POST"])
    @role_required("admin")
    def toggle_user(user_id):
        user = db.get_or_404(User, user_id)
        if user.id == get_current_user().id:
            flash("You cannot deactivate your own admin account.", "error")
        elif user.role == "admin":
            flash("Admin account status cannot be changed here.", "error")
        else:
            user.account_status = "inactive" if user.account_status == "active" else "active"
            db.session.commit()
            flash(f"{user.full_name}'s account is now {user.account_status}.", "success")
        return redirect(url_for("admin_dashboard") + "#users")

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @role_required("admin")
    def delete_user(user_id):
        user = db.get_or_404(User, user_id)
        if user.id == get_current_user().id or user.role == "admin":
            abort(400, description="Admin accounts cannot be deleted here.")
        name = user.full_name
        ContentReport.query.filter(or_(
            ContentReport.reporter_id == user.id,
            ContentReport.target_user_id == user.id,
        )).delete(synchronize_session=False)
        db.session.delete(user)
        db.session.commit()
        flash(f"{name}'s account and associated records were deleted.", "success")
        return redirect(url_for("admin_dashboard") + "#users")

    @app.route("/admin/donations/<int:donation_id>/moderate", methods=["POST"])
    @role_required("admin")
    def moderate_donation(donation_id):
        donation = db.get_or_404(FoodDonation, donation_id)
        action = request.form.get("action")
        if action in ("approve", "reject") and donation.status == "pending":
            donation.status = "available" if action == "approve" else "rejected"
            db.session.add(Notification(
                user_id=donation.donor_id, message=f"Your donation '{donation.food_name}' was "
                f"{'approved' if action == 'approve' else 'rejected'}.", notification_type="moderation",
            ))
            db.session.commit()
            flash("Donation approved." if action == "approve" else "Donation rejected.", "success")
        elif action == "remove":
            db.session.delete(donation)
            db.session.commit()
            flash("Donation removed.", "success")
        else:
            abort(400, description="Invalid moderation action.")
        return redirect(url_for("admin_dashboard") + "#donations")

    @app.route("/admin/donations/<int:donation_id>/delete-expired", methods=["POST"])
    @role_required("admin")
    def delete_expired_donation(donation_id):
        donation = db.get_or_404(FoodDonation, donation_id)
        if donation.expiry_datetime > utcnow():
            abort(400, description="Only expired food listings can be deleted here.")
        db.session.delete(donation)
        db.session.commit()
        flash("Expired food listing deleted.", "success")
        return redirect(url_for("admin_dashboard") + "#donations")

    @app.route("/admin/requests/<int:request_id>/status", methods=["POST"])
    @role_required("admin")
    def admin_request_status(request_id):
        food_request = db.get_or_404(FoodRequest, request_id)
        if food_request.request_status != "pending":
            flash("Only pending requests can be rejected from the admin panel.", "error")
        else:
            food_request.request_status = "rejected"
            db.session.add(Notification(
                user_id=food_request.receiver_id, message=f"Your request for {food_request.donation.food_name} "
                "was closed by an administrator.", notification_type="rejected",
            ))
            db.session.commit()
            flash("Request closed.", "success")
        return redirect(url_for("admin_dashboard") + "#requests")

    @app.route("/admin/requests/<int:request_id>/delete", methods=["POST"])
    @role_required("admin")
    def delete_request(request_id):
        food_request = db.get_or_404(FoodRequest, request_id)
        db.session.delete(food_request)
        db.session.commit()
        flash("Request deleted.", "success")
        return redirect(url_for("admin_dashboard") + "#requests")

    @app.route("/admin/messages/<int:message_id>/delete", methods=["POST"])
    @role_required("admin")
    def delete_contact_message(message_id):
        message = db.get_or_404(ContactMessage, message_id)
        db.session.delete(message)
        db.session.commit()
        flash("Message deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/forgot-password")
    def forgot_password():
        return render_template("forgot_password.html")

    with app.app_context():
        db.create_all()
        user_columns = {column["name"] for column in inspect(db.engine).get_columns(User.__table__.name)}
        if "contact_sharing_enabled" not in user_columns:
            table_name = db.engine.dialect.identifier_preparer.quote(User.__table__.name)
            column_name = db.engine.dialect.identifier_preparer.quote("contact_sharing_enabled")
            db.session.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} BOOLEAN NOT NULL DEFAULT 0"
            ))
            db.session.commit()
        removed_donations = FoodDonation.query.filter(FoodDonation.category.in_(REMOVED_CATEGORIES)).all()
        if removed_donations:
            for donation in removed_donations:
                db.session.delete(donation)
            db.session.commit()
    return app


def get_current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def post_auth_destination(user, target):
    if target:
        parsed = urlsplit(target)
        if (not parsed.scheme and not parsed.netloc and parsed.path.startswith("/")
                and not parsed.path.startswith("//") and "\\" not in target
                and not any(ord(character) < 32 for character in target)):
            if parsed.path == url_for("new_donation") and user.role != "donor":
                return url_for("dashboard")
            if parsed.path == url_for("receiver_dashboard") and user.role != "receiver":
                return url_for("dashboard")
            return target
    return url_for("dashboard")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            flash("Sign in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(role):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if get_current_user().role != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def valid_email(value):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


def valid_phone(value):
    return bool(re.fullmatch(r"[0-9]{10}", value))


def parse_datetime(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def parse_coordinates(latitude, longitude):
    if not latitude and not longitude:
        return None, None
    lat, lng = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError("Coordinates out of range")
    return lat, lng


def escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def distance_km(latitude_a, longitude_a, latitude_b, longitude_b):
    radius = 6371
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = math.radians(latitude_b - latitude_a)
    delta_lng = math.radians(longitude_b - longitude_a)
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lng / 2) ** 2
    haversine = min(1.0, max(0.0, haversine))
    return radius * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def save_uploaded_image(upload, upload_folder):
    if upload is None or not upload.filename:
        return None, None
    filename = secure_filename(upload.filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return None, "Upload a JPG, PNG, or WebP image."
    content = upload.stream.read()
    upload.stream.seek(0)
    valid_signature = (
        (extension in ("jpg", "jpeg") and content.startswith(b"\xff\xd8\xff"))
        or (extension == "png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
        or (extension == "webp" and len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP")
    )
    if not valid_signature:
        return None, "The uploaded file does not appear to be a valid JPG, PNG, or WebP image."
    safe_name = f"{uuid.uuid4().hex}.{extension}"
    upload.save(os.path.join(upload_folder, safe_name))
    return safe_name, None


def expire_old_donations():
    expired = FoodDonation.query.filter(
        FoodDonation.status.in_(("pending", "available", "claimed")),
        FoodDonation.expiry_datetime <= utcnow(),
    ).all()
    if not expired:
        return
    for donation in expired:
        donation.status = "expired"
        for pending in donation.requests:
            if pending.request_status == "pending":
                pending.request_status = "rejected"
                db.session.add(Notification(
                    user_id=pending.receiver_id, message=f"The request for {donation.food_name} closed because "
                    "the donation expired.", notification_type="expired",
                ))
    db.session.commit()


def notify_admins(message, notification_type):
    for admin in User.query.filter_by(role="admin", account_status="active").all():
        db.session.add(Notification(user_id=admin.id, message=message, notification_type=notification_type))


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1")
