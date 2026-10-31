"""API gestione utenti (solo amministratore)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..adapters import users as userstore
from ..db import connect  # noqa: F401  (the user service still takes a connection)
from ..schemas import ResetPasswordIn, UserCreate, UserCreatedOut, UserOut, UserUpdate
from ..services import auth as authsvc
from ..services import security
from ..services import users as usersvc
from ..services import audit as auditsvc

router = APIRouter(tags=["users"], dependencies=[Depends(authsvc.get_current_user)])


def _admin(user: dict = Depends(authsvc.get_current_user)) -> dict:
    return authsvc.require_admin(user)


@router.get("/api/users", response_model=list[UserOut])
def list_users(_admin: dict = Depends(_admin)) -> list[UserOut]:
    with connect() as conn:
        return [UserOut(**u) for u in usersvc.list_users(conn)]


@router.post("/api/users", response_model=UserCreatedOut, status_code=201)
def create_user(payload: UserCreate, _admin: dict = Depends(_admin)) -> UserCreatedOut:
    """Crea un account. Senza password (o con `invite=true`) genera un link
    d'invito: l'utente sceglie la propria password, l'admin non la conosce.

    In ogni caso l'account nasce con la password da cambiare al primo accesso.
    """
    with connect() as conn:
        if payload.invite or not payload.password:
            # Placeholder casuale: l'utente non lo vedrà mai, entra dal link.
            placeholder = security.new_token()
            user = usersvc.create_user(
                conn,
                payload.username,
                placeholder,
                email=payload.email,
                role=payload.role,
                active=payload.active,
                must_change_password=True,
            )
            token, _expires = usersvc.generate_invite(conn, user["id"])
            auditsvc.record(conn, _admin, "user.invited", resource_type="user", resource_id=user["id"], payload={"role": user["role"]})
            return UserCreatedOut(**user, invite_url=f"/invito?token={token}")
        user = usersvc.create_user(
            conn,
            payload.username,
            payload.password,
            email=payload.email,
            role=payload.role,
            active=payload.active,
            must_change_password=True,
        )
        auditsvc.record(conn, _admin, "user.created", resource_type="user", resource_id=user["id"], payload={"role": user["role"]})
    return UserCreatedOut(**user)


@router.post("/api/users/{user_id}/invite", response_model=UserCreatedOut)
def invite_user(user_id: int, _admin: dict = Depends(_admin)) -> UserCreatedOut:
    """Genera (o rinnova) il link d'invito di un utente esistente."""
    with connect() as conn:
        user = usersvc.get_user(conn, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="utente non trovato")
        token, _expires = usersvc.generate_invite(conn, user_id)
        auditsvc.record(conn, _admin, "user.invited", resource_type="user", resource_id=user_id)
        out = usersvc.user_out(usersvc.get_user(conn, user_id))
    return UserCreatedOut(**out, invite_url=f"/invito?token={token}")


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
        if payload.active is False:
            if userstore.owned_projects(user_id, conn):
                raise HTTPException(
                    status_code=409,
                    detail="trasferisci prima la proprietà dei progetti dell'utente",
                )
        if payload.role and payload.role != target["role"] and admin["id"] != target["id"]:
            _guard_demote(conn, target["id"])
        user = usersvc.update_user(
            conn, user_id, email=payload.email, role=payload.role, active=payload.active
        )
        auditsvc.record(conn, admin, "user.updated", resource_type="user", resource_id=user_id, payload=payload.model_dump(exclude_unset=True))
    return UserOut(**user)


@router.post("/api/users/{user_id}/reset-password", response_model=UserOut)
def reset_password(
    user_id: int,
    payload: ResetPasswordIn,
    _admin: dict = Depends(_admin),
) -> UserOut:
    # R12: no self reset. Your own password goes through change-password, which
    # asks for the current one.
    if user_id == _admin["id"]:
        raise HTTPException(status_code=400, detail="usa il cambio password per il tuo account")
    with connect() as conn:
        if usersvc.get_user(conn, user_id) is None:
            raise HTTPException(status_code=404, detail="utente non trovato")
        # Password temporanea scelta dall'admin: l'utente dovrà cambiarla al
        # primo accesso, così l'admin non resta a conoscere la password vera.
        usersvc.set_password(conn, user_id, payload.password, must_change=True)
        # Le sessioni esistenti dell'utente decadono: la password è cambiata.
        userstore.delete_sessions(user_id, conn)
        auditsvc.record(conn, _admin, "user.password_reset", resource_type="user", resource_id=user_id)
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
        if userstore.owned_projects(user_id, conn):
            raise HTTPException(
                status_code=409,
                detail="trasferisci prima la proprietà dei progetti dell'utente",
            )
        usersvc.delete_user(conn, user_id)
        auditsvc.record(conn, admin, "user.deleted", resource_type="user", resource_id=user_id)


def _guard_demote(conn, user_id: int) -> None:
    """Impedisce di togliere l'admin all'ultimo amministratore dell'istanza."""
    if usersvc.count_admins(conn) <= 1:
        raise HTTPException(status_code=400, detail="non si può eliminare o declassare l'ultimo amministratore")
