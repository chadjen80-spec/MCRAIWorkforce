"""Brand asset library: logos uploaded once and reused on posters (event, sponsor and presenter logos),
and poster artwork the user made elsewhere (e.g. Adobe Firefly or Photoshop) to use instead of AI artwork."""
from __future__ import annotations

import io
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.agents import poster as poster_layout
from app.db import get_db
from app.deps import Principal, get_principal, require_admin
from app.errors import AppError, not_found
from app.models import BrandAsset
from app.security import sign_asset_url, verify_asset_signature
from app.services.brand_assets import artwork_source, load_artwork, load_logos
from app.services.orchestrator import audit
from app.services.storage import get_storage

router = APIRouter(prefix="/v1", tags=["assets"])
public_router = APIRouter(prefix="/v1", tags=["assets"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_ARTWORK_BYTES = 25 * 1024 * 1024  # Firefly / Photoshop exports at 2048-4096 px
MAX_SIDE = 2000  # logos are stored at most this large; plenty for a 1080x1920 poster
MAX_ART_SIDE = 3000
MAX_ASSETS = 200


def asset_out(a: BrandAsset) -> schemas.AssetOut:
    return schemas.AssetOut(id=a.id, name=a.name, kind=a.kind, source=a.source or "upload",
                            content_type=a.content_type, width=a.width, height=a.height,
                            size_bytes=a.size_bytes, url=sign_asset_url(a.id), created_at=a.created_at)


def _decode(data: bytes, mode: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 40_000_000:
                raise AppError(422, "IMAGE_TOO_LARGE", "The image has too many pixels")
            im.load()
            return im.convert(mode)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise AppError(422, "IMAGE_INVALID", "The file is not a PNG, JPEG or WebP image") from exc


def normalize_logo(data: bytes) -> tuple[bytes, int, int]:
    """Decode an uploaded image and re-encode it as PNG (keeps transparency, drops anything that is not pixels)."""
    rgba = _decode(data, "RGBA")
    rgba.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    rgba.save(buf, "PNG", optimize=True)
    return buf.getvalue(), rgba.width, rgba.height


def normalize_artwork(data: bytes) -> tuple[bytes, int, int]:
    """Artwork is a full background: re-encoded as a high-quality JPEG (no transparency needed)."""
    rgb = _decode(data, "RGB")
    if min(rgb.size) < 512:
        raise AppError(422, "IMAGE_TOO_SMALL", "Artwork must be at least 512 px on its shorter side "
                       "(a 9:16 export such as 1440x2560 works best)")
    rgb.thumbnail((MAX_ART_SIDE, MAX_ART_SIDE))
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue(), rgb.width, rgb.height


@router.post("/assets", response_model=schemas.AssetOut, status_code=201, summary="Upload a logo or poster artwork")
async def upload_asset(
    file: UploadFile = File(...),
    name: str = Form(default="", max_length=120),
    kind: Literal["logo", "artwork"] = Form(default="logo"),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    org = principal.organization
    limit = MAX_ARTWORK_BYTES if kind == "artwork" else MAX_UPLOAD_BYTES
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(413, "FILE_TOO_LARGE", f"{'Artwork' if kind == 'artwork' else 'Logos'} must be at most "
                       f"{limit // (1024 * 1024)} MB")
    count = len(session.scalars(select(BrandAsset.id).where(BrandAsset.organization_id == org.id)).all())
    if count >= MAX_ASSETS:
        raise AppError(409, "TOO_MANY_ASSETS", f"The logo library holds at most {MAX_ASSETS} files; delete some first")
    if kind == "artwork":
        blob, width, height = normalize_artwork(data)
        ext, content_type = "jpg", "image/jpeg"
    else:
        blob, width, height = normalize_logo(data)
        ext, content_type = "png", "image/png"
    asset_id = uuid.uuid4()
    key = f"org/{org.id}/assets/{asset_id}.{ext}"
    get_storage().put_bytes(key, blob, content_type)
    label = " ".join((name or (file.filename or kind).rsplit(".", 1)[0]).split())[:120] or kind
    asset = BrandAsset(id=asset_id, organization_id=org.id, name=label, kind=kind, storage_key=key,
                       content_type=content_type, width=width, height=height, size_bytes=len(blob))
    session.add(asset)
    audit(session, org.id, "asset.uploaded", principal.actor,
          detail={"asset_id": str(asset_id), "name": label, "kind": kind})
    session.commit()
    return asset_out(asset)


@router.get("/assets", response_model=list[schemas.AssetOut], summary="List the logo and artwork library")
def list_assets(kind: Literal["logo", "artwork"] | None = Query(default=None),
                principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    query = select(BrandAsset).where(BrandAsset.organization_id == principal.organization.id)
    if kind:
        query = query.where(BrandAsset.kind == kind)
    rows = session.scalars(query.order_by(BrandAsset.created_at))  # upload order, so a set of sponsors stays together
    return [asset_out(a) for a in rows]


@router.delete("/assets/{asset_id}", status_code=204, summary="Delete a logo")
def delete_asset(asset_id: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    asset = session.scalar(select(BrandAsset).where(BrandAsset.id == asset_id,
                                                    BrandAsset.organization_id == principal.organization.id))
    if asset is None:
        raise not_found("Asset")
    try:
        get_storage().delete(asset.storage_key)
    except Exception:  # noqa: BLE001, S110 - an orphaned file must not block removing the library entry
        pass
    audit(session, asset.organization_id, "asset.deleted", principal.actor, detail={"name": asset.name})
    session.delete(asset)
    session.commit()
    return Response(status_code=204)


@public_router.get("/assets/{asset_id}/file", summary="View a logo via a signed, expiring URL")
def asset_file(asset_id: uuid.UUID, exp: int, sig: str, session: Session = Depends(get_db)):
    if not verify_asset_signature(asset_id, exp, sig):
        raise AppError(403, "FORBIDDEN", "Link is invalid or has expired")
    asset = session.get(BrandAsset, asset_id)
    storage = get_storage()
    if asset is None or not storage.exists(asset.storage_key):
        raise not_found("Asset")
    return Response(storage.read_bytes(asset.storage_key), media_type=asset.content_type,
                    headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.post("/posters/preview", summary="Live preview of a poster layout (stand-in artwork, JPEG)",
             response_class=Response, responses={200: {"content": {"image/jpeg": {}}}})
def poster_preview(body: schemas.PosterSpec, principal: Principal = Depends(get_principal),
                   session: Session = Depends(get_db)):
    """Typesets the poster exactly as a job would, over placeholder art (or your own uploaded artwork).
    No AI call, no cost, nothing stored."""
    spec = body.model_dump(mode="json")
    storage = get_storage()
    logos, _ = load_logos(session, storage, principal.organization.id, spec)
    art = load_artwork(session, storage, principal.organization.id, spec.get("art_asset_id"))
    # Artwork kept from an AI poster is placed like fresh AI art, exactly as the job will place it.
    ai_art = artwork_source(session, principal.organization.id, spec.get("art_asset_id")) == "ai"
    jpeg = poster_layout.preview(spec, logos, art=art, lower=ai_art)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def check_poster_assets(session: Session, org_id: uuid.UUID, poster: schemas.PosterSpec) -> None:
    """Every logo a poster names must be in this organization's library, and its artwork (if any) too."""
    if poster.art_asset_id is not None:
        art = session.scalar(select(BrandAsset.id).where(BrandAsset.organization_id == org_id,
                                                         BrandAsset.id == poster.art_asset_id, BrandAsset.kind == "artwork"))
        if art is None:
            raise AppError(422, "ASSET_NOT_FOUND", "The chosen artwork is not in the artwork library")
    if poster.venue_photo_id is not None:
        photo = session.scalar(select(BrandAsset.id).where(BrandAsset.organization_id == org_id,
                                                           BrandAsset.id == poster.venue_photo_id,
                                                           BrandAsset.kind == "artwork"))
        if photo is None:
            raise AppError(422, "ASSET_NOT_FOUND", "The venue photo is not in the artwork library")
    wanted = set(poster.logo_ids())
    if not wanted:
        return
    found = set(session.scalars(select(BrandAsset.id).where(BrandAsset.organization_id == org_id,
                                                            BrandAsset.id.in_(wanted))))
    if missing := wanted - found:
        raise AppError(422, "ASSET_NOT_FOUND", f"{len(missing)} logo(s) are not in the logo library")
