"""用户数据目录中的可选素材包，只读供图片与媒体请求访问。"""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.db import get_data_root


router = APIRouter(prefix="/api/asset-pack", tags=["asset-pack"])
MEDIA_TYPES = {
    ".png": "image/png",
    ".ogg": "audio/ogg",
    ".json": "application/json",
    ".md": "text/markdown",
}


@router.get("/{path:path}")
def get_asset_pack_file(path: str) -> FileResponse:
    relative = PurePosixPath(path.replace("\\", "/"))
    if relative.is_absolute() or PureWindowsPath(path).drive or ".." in relative.parts:
        raise HTTPException(status_code=404, detail="asset not found")
    try:
        override = os.environ.get("AMADEUS_ASSET_PACK_DIR")
        root = (Path(override).expanduser() if override else get_data_root() / "asset-pack" / "amadeus-original").resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(status_code=404, detail="asset not found")
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc
    return FileResponse(target, media_type=MEDIA_TYPES.get(target.suffix.lower()))
