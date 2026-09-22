"""拉比克后端入口。"""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    admin,
    analytics,
    api_tokens,
    audit,
    auth,
    credentials,
    datasources,
    lookup,
    notifications,
    permissions,
    query,
    subscriptions,
    tasks,
    teams,
    templates,
    v1,
)
from app.core.config import settings
from app.core.exceptions import RubicError
from app.core.logging_setup import get_logger

log = get_logger("rubick.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时按 BOOTSTRAP_ADMINS 对库内已有用户提权(配置文件加超管,无需对方再登录一次)
    from app.core.database import SessionLocal
    from app.services import auth_service

    db = SessionLocal()
    try:
        n = auth_service.apply_bootstrap_admins(db)
        if n:
            log.info("启动时按 BOOTSTRAP_ADMINS 提权 %d 名管理员", n)
    finally:
        db.close()
    if settings.MOCK_AUTH:
        log.warning(
            "MOCK_AUTH 已开启——mock 登录信任前端传入的 open_id、无凭证校验,"
            "命中 BOOTSTRAP_ADMINS 即可无凭证成为管理员。切勿在生产环境开启!"
        )
    yield


app = FastAPI(title="Rubick / 拉比克", version="0.1.0", lifespan=lifespan)

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


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """未预期异常兜底:记完整堆栈,并返回带 detail 的 JSON。

    Starlette 默认回纯文本 "Internal Server Error"(响应体没有 detail),前端只能显示
    「操作失败」这类兜底文案,线上等于什么线索都没有。这里给出异常首行(如
    「(1364, "Field 'modes' doesn't have a default value")」),排查有据可依。
    """
    log.exception("未处理异常:%s %s", request.method, request.url.path)
    summary = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    detail = f"服务器内部错误({type(exc).__name__}):{summary}"[:500] if summary else \
        f"服务器内部错误({type(exc).__name__}),详见服务端日志"
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/health")
def health():
    return {"status": "ok"}


api = "/api"
app.include_router(auth.router, prefix=api)
app.include_router(datasources.router, prefix=api)
app.include_router(teams.router, prefix=api)
app.include_router(credentials.router, prefix=api)
app.include_router(templates.router, prefix=api)
app.include_router(tasks.router, prefix=api)
app.include_router(subscriptions.router, prefix=api)
app.include_router(query.router, prefix=api)
app.include_router(permissions.router, prefix=api)
app.include_router(audit.router, prefix=api)
app.include_router(notifications.router, prefix=api)
app.include_router(admin.router, prefix=api)
app.include_router(analytics.router, prefix=api)
app.include_router(lookup.router, prefix=api)
# API Token 管理(Web 端,JWT 鉴权)与开放 API v1(仅 API token)是两条完全分离的通道
app.include_router(api_tokens.router, prefix=api)
app.include_router(v1.router, prefix=api)


# 托管前端静态产物(frontend/dist),后端单端口即可提供完整应用,无需 nginx。
# 注意:此段必须放在所有 /api 路由与 /health 之后,catch-all 才不会截胡它们。
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """前端单页应用兜底:命中真实文件就返回它,否则一律回 index.html(交给前端路由)。"""
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
