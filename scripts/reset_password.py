#!/usr/bin/env python3
"""Reset della password di un utente Tabularium dal terminale.

Serve quando l'amministratore non riesce più ad accedere (password dimenticata)
e non può quindi usare la pagina Utenti. Lo strumento usa le stesse funzioni
dell'applicazione (PBKDF2 via ``services/security.py`` + invalidazione delle
sessioni aperte), esattamente come l'endpoint admin.

Uso:
    python scripts/reset_password.py                # elenca gli utenti e chiede
    python scripts/reset_password.py badino         # reset interattivo (input nascosto)
    python scripts/reset_password.py badino "nuova" # password da argomento

Richiede l'ambiente del backend: ``backend/.venv/bin/python scripts/reset_password.py``
oppure, dopo ``source backend/.venv/bin/activate``, semplicemente ``python``.
La radice dati segue ``TABULARIUM_ROOT`` (default ``<repo>/data``).
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.db import connect  # noqa: E402
from app.services import users as usersvc  # noqa: E402


def list_users() -> None:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, username, role, active FROM users ORDER BY id"
        ).fetchall()
    if not rows:
        print("Nessun utente nel database.")
        return
    print(f"{'ID':>4}  {'username':<20} {'ruolo':<8} attivo")
    for row in rows:
        print(f"{row['id']:>4}  {row['username']:<20} {row['role']:<8} {'sì' if row['active'] else 'no'}")


def main() -> int:
    args = [a for a in sys.argv[1:] if a not in ("-h", "--help")]
    if "-h" in sys.argv or "--help" in sys.argv or not args:
        print(__doc__)
        list_users()
        if not args:
            return 0
        args = args or []

    username = args[0]
    with connect() as conn:
        user = conn.execute(
            "SELECT id, username FROM users WHERE username=?", (username,)
        ).fetchone()
        if user is None:
            print(f"Errore: utente «{username}» non trovato.", file=sys.stderr)
            list_users()
            return 1
        if len(args) >= 2:
            password = args[1]
        else:
            password = getpass.getpass(f"Nuova password per «{username}»: ")
            confirm = getpass.getpass("Conferma password: ")
            if password != confirm:
                print("Errore: le due password non coincidono.", file=sys.stderr)
                return 1
        if len(password) < 8:
            print("Errore: la password deve avere almeno 8 caratteri.", file=sys.stderr)
            return 1
        usersvc.set_password(conn, user["id"], password)
        # Come l'endpoint admin: le sessioni aperte decadono subito.
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    print(f"Fatto: password di «{username}» aggiornata e sessioni chiuse.")
    print("Accedi di norma dall'app; cambia la password temporanea appena entro.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
