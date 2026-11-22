import argparse
import getpass

from app import create_app
from models import FoodDonation, User, db, utcnow


def main():
    parser = argparse.ArgumentParser(description="Initialize the food donation database.")
    parser.add_argument("--demo", action="store_true", help="Create local donor and receiver demo accounts.")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        db.create_all()
        admin_email = input("Admin email [admin@foodshare.local]: ").strip().lower() or "admin@foodshare.local"
        admin = User.query.filter_by(email=admin_email).first()
        if admin is None:
            password = getpass.getpass("Choose an admin password (at least 12 characters): ")
            confirmation = getpass.getpass("Confirm admin password: ")
            if len(password) < 12 or password != confirmation:
                raise SystemExit("Passwords must match and contain at least 12 characters.")
            admin = User(full_name="Platform Admin", email=admin_email, role="admin")
            admin.set_password(password)
            db.session.add(admin)
        elif admin.role != "admin":
            raise SystemExit("That email already belongs to a non-admin account.")

        if args.demo:
            if app.config.get("APP_ENV") == "production":
                raise SystemExit("Demo accounts are disabled in production.")
            for email, name, role, password in (
                ("donor@foodshare.local", "Demo Donor", "donor", "DonorDemo2026!"),
                ("receiver@foodshare.local", "Demo Receiver", "receiver", "ReceiverDemo2026!"),
            ):
                user = User.query.filter_by(email=email).first()
                if user is None:
                    user = User(full_name=name, email=email, role=role, phone="555-0100",
                                address="Central Market, New York")
                    user.set_password(password)
                    db.session.add(user)
                    db.session.flush()
                if role == "donor" and not user.donations:
                    db.session.add(FoodDonation(
                        donor_id=user.id, food_name="Fresh bread boxes", category="Bakery",
                        description="Fresh bread, packed this morning and ready for pickup.",
                        quantity=24, quantity_remaining=24, quantity_unit="boxes", people_served=48,
                        preparation_datetime=utcnow(),
                        expiry_datetime=utcnow().replace(hour=23, minute=59),
                        pickup_address="Central Market, New York", latitude=40.7580, longitude=-73.9855,
                        availability_time="Today, 4:00-7:00 PM", status="available",
                    ))
            print("Demo accounts created: donor@foodshare.local / DonorDemo2026! and receiver@foodshare.local / ReceiverDemo2026!")
        db.session.commit()
        print(f"Database ready. Admin account: {admin_email}")


if __name__ == "__main__":
    main()
