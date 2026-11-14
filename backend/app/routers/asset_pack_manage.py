"""Authenticated endpoints for inspecting, installing, and removing optional asset packs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from app.services import asset_pack_installer, data_relocation, data_startup
from app.services.asset_pack_installer import AssetPackError

router = APIRouter(prefix="/api/data/asset-pack", tags=["asset-pack"])

_BINARY_CONTENT_TYPES = frozenset({
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
})


async def _call(function: Callable[..., Any], *args: Any) -> Any:
    try:
        return await data_relocation.run_in_thread(function, *args)
    except AssetPackError as exc:
        raise HTTPException(
            exc.status,
            detail={"code": exc.code, "message": str(exc), **exc.extra},
        ) from exc
    except OSError as exc:
        raise HTTPException(
            500,
            detail={"code": "asset_pack_io_failed", "message": str(exc)},
        ) from exc


def _require_available() -> None:
    if not data_startup.available():
        raise HTTPException(
            409,
            detail={"code": "data_unavailable", "message": "Data directory is unavailable"},
        )


def _content_type(request: Request) -> str:
    raw = request.headers.get("content-type") or ""
    return raw.split(";", 1)[0].strip().lower()


def _parse_content_length(request: Request, limit: int) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except ValueError as exc:
        raise HTTPException(
            422,
            detail={"code": "invalid_request", "message": "Invalid Content-Length header"},
        ) from exc
    if length < 0:
        raise HTTPException(
            422,
            detail={"code": "invalid_request", "message": "Invalid Content-Length header"},
        )
    if length > limit:
        raise HTTPException(
            413,
            detail={"code": "payload_too_large", "message": "Request payload exceeds size limit"},
        )


async def _read_json_source_path(request: Request) -> str:
    _parse_content_length(request, asset_pack_installer.MAX_JSON_BODY_BYTES)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > asset_pack_installer.MAX_JSON_BODY_BYTES:
            raise HTTPException(
                413,
                detail={"code": "payload_too_large", "message": "JSON request exceeds size limit"},
            )
        chunks.append(chunk)
    raw_body = b"".join(chunks)
    if not raw_body:
        raise HTTPException(
            422,
            detail={"code": "invalid_request", "message": "Request body is required"},
        )
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            422,
            detail={"code": "invalid_request", "message": "Request body must be valid UTF-8 JSON"},
        ) from exc
    if not isinstance(payload, dict) or set(payload.keys()) != {"source_path"}:
        raise HTTPException(
            422,
            detail={"code": "invalid_request", "message": "Expected JSON object with only 'source_path'"},
        )
    source_path = payload["source_path"]
    if not isinstance(source_path, str) or not source_path.strip():
        raise HTTPException(
            422,
            detail={"code": "invalid_request", "message": "'source_path' must be a non-empty string"},
        )
    return source_path


async def _stream_upload_to_temp(request: Request) -> Path:
    _parse_content_length(request, asset_pack_installer.MAX_UPLOAD_BYTES)
    fd, tmp_name = tempfile.mkstemp(prefix="amadeus-pack-upload-", suffix=".zip")
    upload_path = Path(tmp_name)
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > asset_pack_installer.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        detail={"code": "payload_too_large", "message": "Uploaded archive exceeds size limit"},
                    )
                handle.write(chunk)
        if total == 0:
            raise HTTPException(
                422,
                detail={"code": "empty_upload", "message": "Uploaded archive is empty"},
            )
        return upload_path
    except BaseException:
        upload_path.unlink(missing_ok=True)
        raise


def _run_with_upload_cleanup(fn: Callable[[Path], Any], upload_path: Path) -> Any:
    try:
        return fn(upload_path)
    finally:
        upload_path.unlink(missing_ok=True)


@router.get("")
async def get_asset_pack_status():
    return await _call(asset_pack_installer.get_status)


@router.post("/inspect")
async def inspect_asset_pack(request: Request):
    _require_available()
    ctype = _content_type(request)
    if ctype == "application/json":
        source_path = await _read_json_source_path(request)
        return await _call(asset_pack_installer.inspect_path, source_path)
    if ctype in _BINARY_CONTENT_TYPES:
        upload_path = await _stream_upload_to_temp(request)
        return await _call(
            _run_with_upload_cleanup,
            asset_pack_installer.inspect_zip_file,
            upload_path,
        )
    raise HTTPException(
        422,
        detail={
            "code": "unsupported_content_type",
            "message": "Use application/json or raw application/zip request body",
        },
    )


@router.post("/install")
async def install_asset_pack(request: Request):
    _require_available()
    if asset_pack_installer._env_override() is not None:
        raise HTTPException(
            409,
            detail={
                "code": "asset_pack_not_movable",
                "message": "Cannot install asset pack while AMADEUS_ASSET_PACK_DIR is set",
            },
        )
    if asset_pack_installer._install_lock.locked():
        raise HTTPException(
            409,
            detail={
                "code": "asset_pack_busy",
                "message": "Another asset pack install or uninstall is in progress",
            },
        )
    ctype = _content_type(request)
    if ctype == "application/json":
        source_path = await _read_json_source_path(request)
        return await _call(asset_pack_installer.install_from_path, source_path)
    if ctype in _BINARY_CONTENT_TYPES:
        upload_path = await _stream_upload_to_temp(request)
        return await _call(
            _run_with_upload_cleanup,
            asset_pack_installer.install_from_zip_file,
            upload_path,
        )
    raise HTTPException(
        422,
        detail={
            "code": "unsupported_content_type",
            "message": "Use application/json or raw application/zip request body",
        },
    )


@router.delete("")
async def delete_asset_pack(request: Request):
    if request.query_params or await request.body():
        raise HTTPException(
            422,
            detail={
                "code": "invalid_delete_request",
                "message": "DELETE request must not include query parameters or a body",
            },
        )
    _require_available()
    return await _call(asset_pack_installer.uninstall_pack)
