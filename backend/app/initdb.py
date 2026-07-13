"""首次建表:直接 create_all(Phase 1 便捷起步)。

用法:python -m app.initdb
后续 schema 演进请改用 Alembic。
"""
from __future__ import annotations
from app.core.database import Base, engine
import app.models  # noqa: F401  注册所有模型


def main() -> None:
    print("Creating all tables on", engine.url)
    Base.metadata.create_all(bind=engine)
    print("Done.")


if __name__ == "__main__":
    main()
