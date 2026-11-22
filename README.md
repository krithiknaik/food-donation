# FoodShare — Food Waste Reduction and Food Donation Platform

FoodShare is a responsive Flask application that connects food donors with local people and organizations. It includes account registration, role-based dashboards, moderated food listings, quantity-aware requests, notifications, an administrator console, contact messages, and a MapLibre/OpenStreetMap map.

## Requirements

- Python 3.10 or newer
- pip
- A modern browser
- Internet access for the MapLibre map tiles and OpenStreetMap place search
- Optional: MySQL server and the included PyMySQL driver

## Run locally on Windows

From the project directory in the VS Code terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set a unique `SECRET_KEY`. You can create one with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Create the database and your administrator account:

```powershell
python init_db.py
```

The script asks for an administrator email and a password of at least 12 characters. The password is hashed before it is stored. To create local demonstration donor and receiver accounts and a sample listing, run `python init_db.py --demo` instead. Demo credentials (for a local development database only):

- Donor: `donor@foodshare.local` / `DonorDemo2026!`
- Receiver: `receiver@foodshare.local` / `ReceiverDemo2026!`

Start the application:

```powershell
python app.py
```

Visit <http://127.0.0.1:5000/>. The database is created in `instance/food_donation.db` by default. Flask creates its tables on startup; `init_db.py` is where the first administrator and optional demonstration data are created.

For the PowerShell activation policy alternative, run `.\.venv\Scripts\activate.bat` from Command Prompt, or use `.\.venv\Scripts\python.exe` directly without activating.

## Maps and OpenStreetMap search

The Nearby page uses MapLibre GL JS and OpenStreetMap tiles, so it does not need a Google Maps key. Donors can find mapped NGOs, charities, and food banks within 5 km; receivers can find hotels and restaurants within 5 km. Search results include addresses when available, map coordinates, direct map pins, and OpenStreetMap directions. A building's pin may show its mapped center rather than its entrance when no entrance point is recorded. Browser geolocation can be used instead of typing an area.

Map tiles come from the standard OpenStreetMap tile service; place names are looked up with Nominatim and nearby features with Overpass. The entered area is sent to those services when you search. Results depend on community mapping and may be incomplete. Searches are user-triggered and cached for the current page session. The public services are best-effort and rate-limited, so for a high-traffic deployment configure an appropriate hosted/self-managed tile, geocoding, and places provider. Keep the visible OpenStreetMap attribution on the map.

Donation latitude and longitude can be entered in the listing form or populated with browser geolocation. The authenticated `/api/donations?lat=40.71&lng=-74.00&radius_km=25` endpoint filters coordinate-bearing donations by geographic distance and sorts them nearest-first. Exact pickup details are shown only to signed-in users.

The receiver dashboard supports category, food name, pickup-area, and minimum-quantity filters. The authenticated `/api/donations?lat=40.71&lng=-74.00&radius_km=25` endpoint filters coordinate-bearing donations by geographic distance and sorts them nearest-first. Exact pickup details are shown only to signed-in users.

## Database configuration

SQLite is used by default. To use MySQL, install and configure a database, then set `DATABASE_URL` in `.env` using a SQLAlchemy URL:

```dotenv
DATABASE_URL=mysql+pymysql://username:password@localhost/food_donation
```

Use a URL-encoded password if it contains reserved URL characters. The same SQLAlchemy models and relationships are used by both databases. Production schema changes should be handled with a migration tool before deployment; `db.create_all()` is intended for initial local setup, not schema migration.

## Main features

- Public landing, mission, and contact pages; submitted contact messages are persisted and visible to administrators.
- Donor and receiver registration with Werkzeug password hashing and Flask sessions. Admin accounts are provisioned only through `init_db.py`.
- Donor listings with optional JPG/PNG/WebP photo uploads, quantity/unit, preparation and expiry dates, pickup availability, and coordinates. New listings remain pending until an administrator approves them.
- Receiver listing search and filters; requests reserve pending quantities and are checked again atomically when a donor accepts. Expired listings are automatically marked unavailable when the application is accessed.
- Donor acceptance/rejection, receiver request tracking, pickup completion, and in-app notifications.
- Admin overview and status chart, registered user name/email/phone/address/role/status details, user activation/deactivation, listing moderation/removal, pending request closure, reported-user/listing review, and contact inbox.
- CSRF tokens on state-changing forms, role checks, upload extension/signature validation, a 5 MB upload limit, and server-side form validation.

## Project layout

```text
app.py                  Flask routes and application factory
config.py               Environment and application settings
models.py               SQLAlchemy models and relationships
init_db.py              Database and administrator setup CLI
requirements.txt        Runtime dependencies
.env.example            Environment variable template
templates/              Jinja pages
static/css/style.css    Responsive styling
static/js/main.js       Navigation, form helpers, browser geolocation
static/js/maps.js       MapLibre map, donation markers, and OpenStreetMap search
static/uploads/         Uploaded donation images (ignored by Git)
instance/               Local SQLite database (ignored by Git)
tests/test_workflows.py Focused application workflow tests
```

## Run tests

With the project's Python environment active:

```powershell
python -m unittest discover -s tests -v
```

## Deployment notes

Before deploying, set a strong `SECRET_KEY`, set `APP_ENV=production`, set `COOKIE_SECURE=1` when using HTTPS, configure a production database and restricted Maps key if needed, and run behind a production WSGI server with HTTPS. Do not use the documented demo credentials outside a local development database. Password-reset email delivery and automated food-safety verification are intentionally not represented as working features.
