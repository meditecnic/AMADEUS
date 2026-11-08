from fastapi import APIRouter, HTTPException

from app.services import data_relocation, diagnostics_bundle

router = APIRouter(prefix="/api/diagnostics")


@router.post("/bundle")
async def create_bundle():
    from app.main import in_memory_settings

    try:
        return await data_relocation.run_in_thread(diagnostics_bundle.create_bundle, dict(in_memory_settings))
    except OSError:
        raise HTTPException(500, detail={"code": "diagnostics_bundle_failed"}) from None
