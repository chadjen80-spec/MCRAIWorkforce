"""Loading logos from the organization's logo library (shared by poster jobs and the live preview)."""
from __future__ import annotations

import io
import uuid
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BrandAsset
from app.services.storage import Storage


def poster_logo_ids(poster: dict[str, Any]) -> list[str]:
    """Every library image the layout needs (logos, plus the tournament template's venue photo)."""
    return [str(i) for i in [poster.get("event_logo_id"), poster.get("game_logo_id"), poster.get("venue_photo_id"),
                             poster.get("hero_left_id"), poster.get("hero_right_id"),
                             *poster.get("sponsor_logo_ids", []), *poster.get("presenter_logo_ids", [])] if i]


def load_artwork(session: Session, storage: Storage, org_id: uuid.UUID, asset_id: str | None) -> Image.Image | None:
    """The user's own poster artwork from the library, or None when it is not there (any more)."""
    if not asset_id:
        return None
    a = session.scalar(select(BrandAsset).where(BrandAsset.organization_id == org_id,
                                                BrandAsset.id == uuid.UUID(str(asset_id)), BrandAsset.kind == "artwork"))
    if a is None:
        return None
    try:
        with Image.open(io.BytesIO(storage.read_bytes(a.storage_key))) as im:
            return im.convert("RGB")
    except (OSError, ValueError):
        return None


def load_logos(session: Session, storage: Storage, org_id: uuid.UUID,
               poster: dict[str, Any]) -> tuple[dict[str, Image.Image], int]:
    """The poster's logos from this organization's library: (id -> image, number that no longer exist)."""
    ids = set(poster_logo_ids(poster))
    if not ids:
        return {}, 0
    rows = session.scalars(select(BrandAsset).where(BrandAsset.organization_id == org_id,
                                                    BrandAsset.id.in_([uuid.UUID(i) for i in ids])))
    logos: dict[str, Image.Image] = {}
    for a in rows:
        try:
            with Image.open(io.BytesIO(storage.read_bytes(a.storage_key))) as im:
                logos[str(a.id)] = im.convert("RGBA")
        except (OSError, ValueError):
            continue
    return logos, len(ids - set(logos))


MAX_SAVED_SIDE = 3000
MAX_LIBRARY = 200  # same limit as uploads (app.api.assets.MAX_ASSETS)


def artwork_source(session: Session, org_id: uuid.UUID, asset_id: str | None) -> str | None:
    """Where a library artwork came from ("upload" or "ai"), or None when it is not there."""
    if not asset_id:
        return None
    return session.scalar(select(BrandAsset.source).where(BrandAsset.organization_id == org_id,
                                                          BrandAsset.id == uuid.UUID(str(asset_id))))


def save_ai_artwork(session: Session, storage: Storage, org_id: uuid.UUID, art: Image.Image,
                    name: str) -> BrandAsset | None:
    """Keep the artwork of an AI-made poster in the artwork library, so the poster can be laid out again
    (new text, logos, QR code) for free. Returns None when the library is full (the poster is unaffected)."""
    count = len(session.scalars(select(BrandAsset.id).where(BrandAsset.organization_id == org_id)).all())
    if count >= MAX_LIBRARY:
        return None
    rgb = art.convert("RGB")
    rgb.thumbnail((MAX_SAVED_SIDE, MAX_SAVED_SIDE))
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=92, optimize=True)
    blob = buf.getvalue()
    asset_id = uuid.uuid4()
    key = f"org/{org_id}/assets/{asset_id}.jpg"
    storage.put_bytes(key, blob, "image/jpeg")
    asset = BrandAsset(id=asset_id, organization_id=org_id, name=" ".join(name.split())[:120], kind="artwork",
                       source="ai", storage_key=key, content_type="image/jpeg", width=rgb.width,
                       height=rgb.height, size_bytes=len(blob))
    session.add(asset)
    session.flush()
    return asset
