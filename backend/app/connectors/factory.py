"""按数据源引擎构造连接器。"""
from __future__ import annotations

from app.connectors.base import ConnectionConfig, DataSourceConnector
from app.connectors.hive import HiveConnector
from app.connectors.mysql import MySQLConnector
from app.models.datasource import DataSource

_REGISTRY: dict[str, type[DataSourceConnector]] = {
    "mysql": MySQLConnector,
    "hive": HiveConnector,
}


def get_connector(ds: DataSource) -> DataSourceConnector:
    cls = _REGISTRY.get(ds.engine)
    if cls is None:
        raise ValueError(f"不支持的数据源引擎:{ds.engine}")
    config = ConnectionConfig(
        host=ds.host,
        port=ds.port,
        database=ds.database,
        username=ds.username,
        password=ds.password,
        extra=ds.extra or {},
    )
    return cls(config)
