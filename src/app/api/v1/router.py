from fastapi import APIRouter

from app.api.v1.routes.account import router as account_router
from app.api.v1.routes.appointments import router as appointments_router
from app.api.v1.routes.attachments import router as attachments_router
from app.api.v1.routes.audit import router as audit_router
from app.api.v1.routes.auth.router import router as auth_router
from app.api.v1.routes.chat import router as chat_router
from app.api.v1.routes.data_rights import router as data_rights_router
from app.api.v1.routes.families import router as families_router
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.insights import router as insights_router
from app.api.v1.routes.profiles import router as profiles_router
from app.api.v1.routes.records import router as records_router
from app.api.v1.routes.sos import router as sos_router
from app.api.v1.routes.wellness import router as wellness_router

api_router = APIRouter()
api_router.include_router(attachments_router)
api_router.include_router(appointments_router)
api_router.include_router(audit_router)
api_router.include_router(auth_router)
api_router.include_router(account_router)
api_router.include_router(data_rights_router)
api_router.include_router(health_router)
api_router.include_router(insights_router)
api_router.include_router(families_router)
api_router.include_router(profiles_router)
api_router.include_router(records_router)
api_router.include_router(sos_router)
api_router.include_router(wellness_router)
api_router.include_router(chat_router)
