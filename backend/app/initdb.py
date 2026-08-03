"""首次建表:直接 create_all。

用法:python -m app.initdb
schema 演进(增量列、存量数据升级)请用 `python -m app.migrate`(幂等,含轻量 ALTER 与加密迁移);
部署脚本已默认走 migrate。本模块保留为「仅建表」的最小入口。
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
