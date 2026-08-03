"""pytest 全局夹具:用本地临时 SQLite 承载平台元数据库,绝不触碰线上库。

必须在任何 app.* 导入之前设置 CONFIG_FILE(settings 在导入时即缓存)。
"""
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="rubick-test-")
_CFG = Path(_TMP) / "config.ini"
_CFG.write_text(
    "[rubick]\n"
    f"DATABASE_URL = sqlite:///{_TMP}/test.db\n"
    "JWT_SECRET = test-secret\n"
    "MOCK_AUTH = false\n",
    encoding="utf-8",
)
os.environ["CONFIG_FILE"] = str(_CFG)

import pytest  # noqa: E402

import app.models  # noqa: F401,E402  注册所有模型
from app.core.database import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
