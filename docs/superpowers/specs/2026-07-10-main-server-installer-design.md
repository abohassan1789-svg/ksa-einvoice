# Main Server Installer Design

Date: 2026-07-10

## Goal

Build a new first-customer installer for the CRM main server machine. This
installer is separate from earlier developer/build attempts and is intended to
be the first package delivered to the client.

The package must be self-contained. It must not depend on a backup file already
existing on the client machine, a `D:` drive path, or any developer-only local
folder. The current CRM database data must be bundled into the installer and
restored automatically during first installation.

## Installer Type

The installer is the main server installer only. A separate client-machine
installer will be designed later.

Suggested output name:

```text
CRM-Main-Server-Setup.exe
```

The installed product name can remain `CRM`, with the existing `Crm-APP`
shortcut naming unless a later branding decision changes it.

## Data Seeding Decision

The main server installer will include the current PostgreSQL dump inside the
installer package.

Bundled source in the development workspace:

```text
installer/assets/data/crm-current-data.sql
```

At install time, the dump must be copied/extracted by the installer and restored
into the target `crm` database automatically.

The restore operation must be conservative:

- If the target database has no public user tables, restore the bundled dump.
- If the target database already contains public user tables, skip the restore.
- Never drop, truncate, or overwrite an existing client database during normal
  installation.

This makes the first customer install ready to use immediately while protecting
data if the installer is run again later.

## Connection Mode Wizard

The installer must ask how the main server should run.

### Localhost Mode

Use when the CRM will run only on the main machine.

Behavior:

- Server address: `127.0.0.1`
- PostgreSQL listens locally.
- No LAN client access is required.
- The app writes the generated connection profile to ProgramData.

### Server/LAN Mode

Use when other machines will connect to the main machine.

Behavior:

- Detect local IPv4 addresses and show them in a dropdown.
- Allow manual IP entry.
- IP selection is optional until this mode is chosen.
- Validate the selected address and port.
- Configure PostgreSQL and Windows Firewall for LAN access.
- Export a client connection profile for the later client-machine installer.

Default port:

```text
5432
```

The wizard should keep the existing dynamic behavior: no IP address or password
is hard-coded into the application.

## Configuration Output

The installer must generate the server connection profile at:

```text
%ProgramData%\CRM\crm-connection.json
```

The PostgreSQL password must be stored encrypted with Windows DPAPI, not plain
text.

The installer should also generate a portable client profile at:

```text
%ProgramData%\CRM\crm-client-profile.json
```

The client profile should omit the password unless a later approved design
chooses a secure password transfer flow for client installers.

## PostgreSQL Handling

The main server installer should support a self-contained build that includes
PostgreSQL.

Install behavior:

- If PostgreSQL is missing, install the bundled PostgreSQL prerequisite.
- Configure the selected port and listen address, using `5432` as the default
  PostgreSQL port.
- Create the `crm` database if needed.
- Restore the bundled current-data dump only when the database is empty.
- Open the Windows Firewall rule only when LAN/server mode requires it.

## Error Handling

The installer must show clear failures for:

- Missing bundled dump.
- Empty bundled dump.
- PostgreSQL install/configuration failure.
- Invalid or unavailable port.
- Bad PostgreSQL password.
- Restore failure.

Restore failures should stop the installer or show a clear failed state. A first
customer install without the bundled current data is not considered successful.

## Existing Assets And Code To Reuse

Reuse the existing project pieces where possible:

- `app/config/configure_cli.py` for IP detection, port validation, database
  checks, and profile writing.
- `app/config/connection_config.py` for connection profile format and DPAPI
  encryption.
- `app/services/network_service.py` for network checks.
- `installer/scripts/configure_postgres.ps1` for PostgreSQL configuration.
- `installer/scripts/firewall.ps1` for firewall rule management.
- `installer/scripts/restore_database.ps1` for conservative dump restore.
- `installer/assets/data/crm-current-data.sql` as the bundled current database
  seed.

The current workspace is missing the top-level Inno Setup files described in
`installer/README_BUILD.md` (`CRM.iss` and `build.ps1`). Implementation must
either restore/recreate those files or create their replacement as part of the
installer build plan.

## Verification

Minimum verification before delivery:

- Build produces a new main server installer executable.
- Fresh clean VM install in localhost mode succeeds.
- Fresh clean VM install in LAN/server mode succeeds.
- `%ProgramData%\CRM\crm-connection.json` is created.
- The password in the profile is encrypted, not plain text.
- The `crm` database contains the bundled current data after fresh install.
- Re-running the installer does not overwrite existing database tables.
- The CRM app launches and connects using the generated profile.
- Firewall rule is created for LAN/server mode.
- Uninstall removes the app and the installer-managed firewall rule.

## Out Of Scope

The following are intentionally not part of this design:

- The separate client-machine installer.
- Cloud/public server deployment.
- Manual import UI inside the installed app.
- Destructive reset/reseed of an existing client database.
- Changing the CRM application feature set.
