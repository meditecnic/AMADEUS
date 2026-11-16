import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from app.security import passcode

router = APIRouter(prefix="/api/auth")


class Login(BaseModel):
    passcode: str | None = None


class ChangePasscode(BaseModel):
    current: str | None = None
    new: str | None = None


@router.get("/state")
async def auth_state():
    return await asyncio.to_thread(passcode.state)


@router.post("/login")
async def auth_login(body: Login):
    return await passcode.login(body.passcode)


@router.put("/passcode")
async def auth_passcode(body: ChangePasscode):
    return await passcode.change(body.current, body.new)
