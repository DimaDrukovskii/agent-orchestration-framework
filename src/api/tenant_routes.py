"""GET /api/tenant -> подписи виджета клиента (имя, приветствие), чтобы виджет не был привязан к компании."""

from fastapi import APIRouter, Depends

from src.api.auth import verify_api_key
from src.tenant import load_branding

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.get("/tenant")
async def tenant_branding():
    return load_branding()
