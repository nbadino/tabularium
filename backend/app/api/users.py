"""API gestione utenti (solo amministratore)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..db import connect
from ..schemas import ResetPasswordIn, UserCreate, UserOut, UserUpdate
from ..services import auth as authsvc
from ..services import users as usersvc

router = APIRouter(tags=["users"], dependencies=[Depends(authsvc.get_current_user)])


def _admin(user: dict = Depends(authsvc.get_current_user)) -> dict:
    return authsvc.require_admin(user)


@router.get("/api/users", response_model=list[UserOut])
def list_users(_admin: dict = Depends(_admin)) -> list[UserOut]:
    with connect() as conn:
        return [UserOut(**u) for u in usersvc.list_users(conn)]


@router.post("/api/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, _admin: dict = Depends(_admin)) -> UserOut:
    with connect() as conn:
        user = usersvc.create_user(
            conn,
            payload.username,
            payload.password,
            email=payload.email,
            role=payload.role,
            active=payload.active,
        )
    return UserOut(**user)


@router.patch("/api/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    admin: dict = Depends(_admin),
) -> UserOut:
    with connect() as conn:
        target = usersvc.get_user(conn, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="utente non trovato")
        if admin["id"] == target["id"] and payload.role not in (None, "admin"):
            raise HTTPException(status_code=400, detail="non si può declassare l'ultimo amministratore")
        if payload.active is False and admin["id"] == target["id"]:
            raise HTTPException(status_code=400, detail="non si può disattivare il proprio account")
        if payload.role and payload.role != target["role"] and admin["id"] != target["id"]:
            _guard_demote(conn, target["id"])
        user = usersvc.update_user(
            conn, user_id, email=payload.email, role=payload.role, active=payload.active
        )
    return UserOut(**user)


@router.post("/api/users/{user_id}/reset-password", response_model=UserOut)
def reset_password(
    user_id: int,
    payload: ResetPasswordIn,
    _admin: dict = Depends(_admin),
) -> UserOut:
    with connect() as conn:
        if usersvc.get_user(conn, user_id) is None:
            raise HTTPException(status_code=404, detail="utente non trovato")
        usersvc.set_password(conn, user_id, payload.password)
        # Le sessioni esistenti dell'utente decadono: la password è cambiata.
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        user = usersvc.get_user(conn, user_id)
    return UserOut(**usersvc.user_out(user))


@router.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: dict = Depends(_admin)) -> None:
    with connect() as conn:
        target = usersvc.get_user(conn, user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="utente non trovato")
        if target["id"] == admin["id"]:
            raise HTTPException(status_code=400, detail="non si può eliminare il proprio account")
        if target["role"] == "admin":
            _guard_demote(conn, target["id"])
        # I progetti di proprietà dell'utente restano (owner_id NULL = senza
        # proprietario: l'admin può riassegnarli o eliminarli dalla pagina progetto).
        conn.execute("UPDATE projects SET owner_id=NULL WHERE owner_id=?", (user_id,))
        usersvc.delete_user(conn, user_id)


def _guard_demote(conn, user_id: int) -> None:
    """Impedisce di togliere l'admin all'ultimo amministratore dell'istanza."""
    if usersvc.count_admins(conn) <= 1:
        raise HTTPException(status_code=400, detail="non si può eliminare o declassare l'ultimo amministratore")
