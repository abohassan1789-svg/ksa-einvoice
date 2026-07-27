# KSA e-Invoice

A Windows desktop application for **Saudi Arabia (ZATCA / Phase-2) e-invoicing**
and customer management, built with **PySide6** and **PostgreSQL**.

It provides sales invoices with ZATCA-compliant QR codes, receipt vouchers,
customer statements, a cashier/POS screen, product and company master data,
customer CRM with daily follow-ups, a users/roles/permissions system, and
Arabic (RTL) printable/PDF templates.

> **Language:** the user interface is primarily Arabic (right-to-left). The code,
> configuration and this documentation are in English.

---

## Table of contents

- [Quick install (Windows installer)](#quick-install-windows-installer)
- [Requirements](#requirements)
- [What the installer does](#what-the-installer-does)
- [Manual / developer setup](#manual--developer-setup)
- [Database & migrations](#database--migrations)
- [Configuration (`.env`)](#configuration-env)
- [Running the app](#running-the-app)
- [Running the tests](#running-the-tests)
- [Building the installer](#building-the-installer)
- [Project layout](#project-layout)
- [License](#license)

---

## Quick install (Windows installer)

1. Install **PostgreSQL** (14 or newer) and note the host, port, username and
   password. *(The installer does **not** install PostgreSQL.)*
2. Download **`KSA-eInvoice-Setup.exe`** from the
   [latest release](https://github.com/abohassan1789-svg/ksa-einvoice/releases/latest).
3. Run it. When asked, enter your PostgreSQL **host, port, database name, username
   and password**. The database is created automatically if it does not exist.
4. Launch **KSA e-Invoice** from the Desktop or Start Menu shortcut.
5. Sign in with the default administrator account:

   | Username | Password |
   |----------|----------|
   | `Admin`  | `1`      |

   > Change this password after the first sign-in.

An internet connection is required **during installation** so the required Python
packages (and Python itself, if missing) can be downloaded.

---

## Requirements

| Component  | Requirement |
|------------|-------------|
| OS         | Windows 10 / 11 (64-bit) |
| Database   | PostgreSQL 14+ (installed separately, running and reachable) |
| Python     | 3.11 (64-bit). The installer downloads and installs it automatically if it is missing. Any 3.9–3.13 works for a manual setup. |
| Internet   | Required during install (to download Python packages) |

---

## What the installer does

The installer (`installer/KSA-eInvoice.iss`, source-based — no frozen binary) runs
these steps on the target machine:

1. Copies the application source into the install folder.
2. Creates **Desktop** and **Start Menu** shortcuts.
3. Detects Python; **installs Python 3.11 silently** if none is found.
4. Creates a **virtual environment** (`.venv`).
5. `pip install -r requirements.txt` into that venv.
6. Prompts for the **database host / port / name / user / password**.
7. Generates the **`.env`** file from those values.
8. **Creates the database** if it does not exist and **tests the connection**.
9. Runs **all pending migrations** and provisions the default admin/permissions.
10. Writes the app's encrypted connection profile and **verifies the app starts**.

It does **not** install PostgreSQL, and it never touches an existing database's
data (database creation is skipped if it already exists; migrations are tracked
and only-once).

Silent/unattended install (used for CI/validation):

```bash
KSA-eInvoice-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /DBHOST=localhost /DBPORT=5432 /DBNAME=ksa_einvoice /DBUSER=postgres /DBPASS=yourpassword
```

The application is installed to a freely writable folder (by default
`C:\KSA-eInvoice`) — **not** Program Files — so its source stays editable.

---

## Editing the UI after installation

The installed app runs its own source from the install folder
(`C:\KSA-eInvoice\app\...`) via `run_app.vbs` → `.venv\Scripts\pythonw.exe -m app.main`.
To change the UI on an installed machine:

1. Edit the `.py` files **inside the install folder**, e.g. `C:\KSA-eInvoice\app\ui\...`.
   (Editing a copy elsewhere has no effect on the installed app.)
2. Fully **close** the app (make sure no `pythonw.exe` is still running).
3. Relaunch from the shortcut — Python recompiles the changed files automatically.
   If a change still doesn't show, delete the `__pycache__` folders under
   `C:\KSA-eInvoice\app` and relaunch.

> Do not install into `C:\Program Files\...`: Windows redirects unelevated edits
> there to a hidden `VirtualStore`, so the running app keeps reading the original
> unedited files. The default install location avoids this.

---

## Manual / developer setup

Clone the repository and set up a virtual environment. Use **Python 3.11 (64-bit)**.

```powershell
git clone https://github.com/abohassan1789-svg/ksa-einvoice.git
cd ksa-einvoice

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create your configuration from the template and edit the values:

```powershell
copy .env.example .env
notepad .env
```

Create the database and apply the schema:

```powershell
# create the database (once) - adjust name/user as needed
psql -h localhost -U postgres -c "CREATE DATABASE ksa_einvoice;"

# apply all migrations, then seed the admin account + permissions
python -m app.database.migrate
python -m app.config.configure_cli provision --host localhost --port 5432 --db ksa_einvoice --user postgres --password YOURPASSWORD
```

Run the app:

```powershell
python -m app.main
```

---

## Database & migrations

This project does **not** use Alembic. The schema is defined as numbered SQL files
in [`app/database/migrations/`](app/database/migrations) and applied by a small
runner:

```powershell
python -m app.database.migrate            # apply all pending migrations
python -m app.database.migrate --status   # show applied / pending, change nothing
```

- The runner records applied files in a `schema_migrations` table, so it is safe
  to run repeatedly.
- It applies only forward migrations; `*_downgrade_*.sql` and `*_verify_*.sql`
  files are ignored.
- Connection settings come from `.env` by default, or from explicit
  `--host/--port/--db/--user/--password` flags.

`app.config.configure_cli` provides the database bootstrap sub-commands the
installer uses: `create-db`, `test-db`, `provision`, and `write` (connection
profile). Run `python -m app.config.configure_cli --help` for details.

---

## Configuration (`.env`)

| Variable      | Meaning                         | Example        |
|---------------|---------------------------------|----------------|
| `DB_NAME`     | PostgreSQL database name         | `ksa_einvoice` |
| `DB_USER`     | PostgreSQL user                  | `postgres`     |
| `DB_PASSWORD` | PostgreSQL password              | `secret`       |
| `DB_HOST`     | Database host                    | `localhost`    |
| `DB_PORT`     | Database port                    | `5432`         |
| `APP_ENV`     | `development` / `production`     | `development`  |
| `LOG_LEVEL`   | Logging level                    | `INFO`         |

On an installed machine the app also reads an encrypted **connection profile**
(`config/connection.json`), which the installer writes. `.env` is the primary
source for development and for the command-line tools.

> **Never commit `.env`** — it is git-ignored. Only `.env.example` (placeholders)
> is tracked.

---

## Running the app

```powershell
python -m app.main
```

The entry point is [`app/main.py`](app/main.py) → `app.ui.review_window.run_app`.
Default login: **`Admin` / `1`**.

---

## Running the tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

Some tests connect to a live PostgreSQL and self-skip if it is unreachable; the
rest run without a database.

---

## Building the installer

Prerequisites: **Inno Setup 6** (<https://jrsoftware.org/isdl.php>).

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
```

The single-file installer is written to **`dist\KSA-eInvoice-Setup.exe`**.
All installer sources live under [`installer/`](installer):

| File | Purpose |
|------|---------|
| `KSA-eInvoice.iss`        | Inno Setup script (UI, files, shortcuts) |
| `build_installer.ps1`     | Compiles the `.iss` with ISCC |
| `scripts/setup_env.ps1`   | Post-install: Python, venv, pip, `.env`, DB, verify |
| `scripts/make_env.py`     | Writes `.env` safely from the entered values |
| `scripts/verify_app.py`   | Headless start-up smoke test |
| `assets/app.ico`          | Application / installer icon |

---

## Project layout

```
app/
  main.py                 # entry point
  config/                 # settings, connection profile, DB bootstrap CLI
  database/               # db helpers, migrate.py runner, migrations/*.sql
  models/ schemas/        # data models
  repositories/           # SQL data access
  services/               # business logic (invoices, backups, permissions, ...)
  security/               # password hashing, session context
  ui/                     # PySide6 screens, dialogs, print templates
installer/                # Inno Setup script + build/bootstrap scripts
tests/                    # pytest suite
requirements.txt          # pinned runtime dependencies
requirements-dev.txt      # test/dev dependencies
```

---

## License

Released under the [MIT License](LICENSE).
