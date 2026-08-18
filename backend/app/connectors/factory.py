"""按数据源引擎构造连接器。"""
from __future__ import annotations

from app.connectors.base import ConnectionConfig, Credential, DataSourceConnector
from app.connectors.hive import HiveConnector
from app.connectors.mysql import MySQLConnector
from app.models.datasource import DataSource

_REGISTRY: dict[str, type[DataSourceConnector]] = {
    "mysql": MySQLConnector,
    "hive": HiveConnector,
}


def get_connector(ds: DataSource, credential: Credential) -> DataSourceConnector:
    """构造连接器。地址与引擎参数恒取自数据源,身份取自 credential。

    credential **必填且无默认值**:这是本平台的数据权限边界所在。给它一个
    「默认用公共账号」的缺省值,等于让任何新增的取数入口只要忘了传参就静默拿到
    能看全库的账号 —— 而那种疏漏不会有任何测试失败。要用公共账号请显式写
    `ds.public_credential`(见 models/datasource.py),让它成为一个签过字的选择。

    团队身份从 services/credential_service.for_template / for_team 取。
    """
    cls = _REGISTRY.get(ds.engine)
    if cls is None:
        raise ValueError(f"不支持的数据源引擎:{ds.engine}")
    config = ConnectionConfig(
        host=ds.host,
        port=ds.port,
        database=ds.database,
        username=credential.username,
        password=credential.password,
        extra=ds.extra or {},
    )
    return cls(config)
