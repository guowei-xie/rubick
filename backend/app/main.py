"""拉比克后端入口。"""
from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    admin,
    audit,
    auth,
    datasources,
    lookup,
    notifications,
    permissions,
    query,
    templates,
)
from app.core.config import settings
from app.core.exceptions import RubicError

app = FastAPI(title="Rubick / 拉比克", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RubicError)
async def rubic_error_handler(request: Request, exc: RubicError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health")
def health():
    return {"status": "ok"}


api = "/api"
app.include_router(auth.router, prefix=api)
app.include_router(datasources.router, prefix=api)
app.include_router(templates.router, prefix=api)
app.include_router(query.router, prefix=api)
app.include_router(permissions.router, prefix=api)
app.include_router(audit.router, prefix=api)
app.include_router(notifications.router, prefix=api)
app.include_router(admin.router, prefix=api)
app.include_router(lookup.router, prefix=api)
