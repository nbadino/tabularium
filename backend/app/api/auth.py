"""API di autenticazione (self-hosted).

Pubblica (raggiungibile senza login): status, setup del primo amministratore,
login/logout, registrazione se aperta. `/me` richiede login.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import config
from ..db import connect
from ..schemas import (
    AuthStatusOut,
    LoginIn,
    RegisterIn,
    SetupIn,
    UserOut,
)
from ..services import auth as authsvc
from ..services import settings as settingssvc
from ..services import users as usersvc

router = APIRouter(tags=["auth"])


def _user_out(user: dict | None) -> UserOut | None:
    if user is None or user.get("is_local"):
        return None
    return UserOut(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        role=user["role"],
        active=bool(user["active"]),
        created_at=user["created_at"],
        last_login_at=user["last_login_at"],
    )


@router.get("/api/auth/status", response_model=AuthStatusOut)
def auth_status(request: Request) -> AuthStatusOut:
    """Stato del gate: usato dal frontend per decidere setup/login/ingresso."""
    settings = settingssvc.get_app_settings()
    enabled = authsvc.auth_enabled()
    user = authsvc.resolve_user(request)
    needs_setup = False
    if enabled:
        with connect() as conn:
            needs_setup = usersvc.count_users(conn) == 0
    return AuthStatusOut(
        auth_enabled=enabled,
        needs_setup=needs_setup,
        allow_registration=bool(settings["allow_registration"]),
        instance_name=settings["instance_name"],
        user=_user_out(user),
    )


@router.post("/api/auth/setup", response_model=UserOut, status_code=201)
def setup_first_admin(payload: SetupIn, response: Response) -> UserOut:
    """Crea l'amministratore al primo avvio (solo se non esistono utenti)."""
    if not authsvc.auth_enabled():
        raise HTTPException(status_code=400, detail="autenticazione disattivata")
    with connect() as conn:
        if usersvc.count_users(conn) > 0:
            raise HTTPException(status_code=409, detail="il primo avvio è già stato completato")
        admin = usersvc.create_user(
            conn, payload.username, payload.password, email=payload.email, role="admin"
        )
        # I progetti esistenti (creati prima dell'auth) vanno al nuovo admin.
        conn.execute("UPDATE projects SET owner_id=? WHERE owner_id IS NULL", (admin["id"],))
    # Sessione dopo il commit della scrittura: create_session apre una seconda
    # connessione, che su SQLite resterebbe bloccata con la transazione aperta.
    token, expires = authsvc.create_session(admin["id"])
    authsvc.set_session_cookie(response, token, expires)
    return UserOut(**admin)


@router.post("/api/auth/login", response_model=UserOut)
def login(payload: LoginIn, response: Response) -> UserOut:
    if not authsvc.auth_enabled():
        raise HTTPException(status_code=400, detail="autenticazione disattivata")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username=?", (payload.username.strip(),)
        ).fetchone()
    if row is None or not row["active"]:
        raise HTTPException(status_code=401, detail="credenziali non valide")
    if not authsvc.check_password(row, payload.password):
        raise HTTPException(status_code=401, detail="credenziali non valide")
    with connect() as conn:
        token, expires = authsvc.create_session(row["id"])
    authsvc.set_session_cookie(response, token, expires)
    return UserOut(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        role=row["role"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


@router.post("/api/auth/register", response_model=UserOut, status_code=201)
def register(payload: RegisterIn, response: Response) -> UserOut:
    """Registrazione pubblica, solo se aperta dall'amministratore."""
    if not authsvc.auth_enabled():
        raise HTTPException(status_code=400, detail="autenticazione disattivata")
    settings = settingssvc.get_app_settings()
    if not settings["allow_registration"]:
        raise HTTPException(status_code=403, detail="la registrazione è chiusa dall'amministratore")
    role = settings["default_new_user_role"]
    with connect() as conn:
        user = usersvc.create_user(
            conn, payload.username, payload.password, email=payload.email, role=role
        )
    # Come per lo setup: sessione dopo il commit (niente connessione nidificata).
    token, expires = authsvc.create_session(user["id"])
    authsvc.set_session_cookie(response, token, expires)
    return UserOut(**user)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        from ..services import security

        with connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash=?", (security.hash_token(token),)
            )
    authsvc.clear_session_cookie(response)
    return {"ok": True}


@router.get("/api/auth/me", response_model=UserOut)
def me(user: dict = Depends(authsvc.get_current_user)) -> UserOut:
    return _user_out(user)  # type: ignore[return-value]
