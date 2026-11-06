"""Data-directory APIs; transport authentication is shared with all local APIs."""
from fastapi import APIRouter

from app.routers.data import _call
from app.services import data_location

router = APIRouter(prefix="/api/data")


@router.get("/location")
async def get_location():
    return await _call(data_location.location)


@router.get("/disk")
async def get_disk():
    return await _call(data_location.disk_usage)
