# app/routers/search_config.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import get_db
from app.dependencies.auth import get_current_user, User
from app.auth.models import ScrapePrefs
from app.models.search_item import ScrapePrefsIn, ScrapePrefsOut

router = APIRouter(prefix="/search/config", tags=["search-config"], dependencies=[Depends(get_current_user)])

def _get_or_create_prefs(db: Session, user_id: int) -> ScrapePrefs:
    prefs = db.query(ScrapePrefs).filter(ScrapePrefs.user_id == user_id).first()
    if not prefs:
        prefs = ScrapePrefs(user_id=user_id)  # defaults = True en todos
        db.add(prefs); db.commit(); db.refresh(prefs)
    return prefs

@router.get("", response_model=ScrapePrefsOut)
def get_scrape_prefs(current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = _get_or_create_prefs(db, int(current.id))
    return ScrapePrefsOut(
        include_coto=prefs.include_coto,
        include_carrefour=prefs.include_carrefour,
        include_dia=prefs.include_dia,
        include_jumbo=prefs.include_jumbo,
    )

@router.put("", response_model=ScrapePrefsOut)
def update_scrape_prefs(data: ScrapePrefsIn, current: User = Depends(get_current_user), db: Session = Depends(get_db)):
    prefs = _get_or_create_prefs(db, int(current.id))
    prefs.include_coto = data.include_coto
    prefs.include_carrefour = data.include_carrefour
    prefs.include_dia = data.include_dia
    prefs.include_jumbo = data.include_jumbo
    db.commit(); db.refresh(prefs)
    return ScrapePrefsOut(
        include_coto=prefs.include_coto,
        include_carrefour=prefs.include_carrefour,
        include_dia=prefs.include_dia,
        include_jumbo=prefs.include_jumbo,
    )
