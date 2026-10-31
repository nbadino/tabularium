# Security policy

Tabularium is self-hosted and handles user accounts, so security reports are
taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability. Report it privately
through GitHub's [private vulnerability reporting](https://github.com/nbadino/tabularium/security/advisories/new).
Include the affected version (`VERSION`), steps to reproduce and the impact.

You can expect an acknowledgement within a week and an assessment within two.
Fixes are released as a patch version, and the advisory is published once a fixed
release is available.

## Supported versions

| Version | Supported |
|---|---|
| latest release | yes |
| older releases | no: upgrade to the latest |

## Deploying safely

These defaults matter for any instance reachable by more than one person:

- **Bind to loopback and put a TLS proxy in front.** Until the first administrator
  exists, `POST /api/auth/setup` is open to whoever reaches it first: create the
  admin before exposing the port (`docker run -p 127.0.0.1:8787:8787 ...`).
- **Behind HTTPS set `TABULARIUM_SESSION_COOKIE_SECURE=1`** so the session cookie is
  never sent in clear. It defaults to off for plain-HTTP localhost use.
- **Never run `TABULARIUM_AUTH=off` beyond localhost.** Every request then runs as
  administrator; the server logs a warning at startup.
- **Protect the data volume.** `TABULARIUM_VAULT_KEY` (in `<root>/.env`) decrypts the
  provider credentials stored in the same volume: a backup of the volume contains
  both. Keep backups as private as the instance.
- **Registration is closed by default**; open it only if every reachable person may
  hold an account.
