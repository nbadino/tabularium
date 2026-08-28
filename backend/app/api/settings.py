"""API impostazioni dell'istanza (self-hosted).

`GET` è accessibile a chiunque sia autenticato (la UI la mostra solo agli
admin); `PUT` modifica le impostazioni ed è riservata all'amministratore.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..schemas import SettingsIn, SettingsOut
from ..services import auth as authsvc
from ..services import settings as settingssvc

router = APIRouter(tags=["settings"])


@router.get("/api/settings", response_model=SettingsOut)
def get_settings(_user: dict = Depends(authsvc.get_current_user)) -> SettingsOut:
    return SettingsOut(**settingssvc.get_app_settings())


@router.put("/api/settings", response_model=SettingsOut)
def update_settings(
    payload: SettingsIn,
    _user: dict = Depends(authsvc.get_current_user),
) -> SettingsOut:
    authsvc.require_admin(_user)
    updates = payload.model_dump(exclude_none=True)
    try:
        saved = settingssvc.save_app_settings(updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SettingsOut(**saved)
