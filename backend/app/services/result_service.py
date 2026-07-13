"""结果落地:CSV 序列化 + MinIO 上传 + 签名下载链接。"""
from __future__ import annotations

import csv
import io

from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule

from app.connectors.base import QueryResult
from app.core.config import settings

_client: Minio | None = None


def _ensure_lifecycle(client: Minio) -> None:
    """给结果桶设置生命周期规则:N 天后自动过期删除,存储量因此有上界。"""
    try:
        rule = Rule(
            ENABLED,
            rule_id="rubic-result-expiry",
            rule_filter=Filter(prefix=""),
            expiration=Expiration(days=settings.RESULT_RETENTION_DAYS),
        )
        client.set_bucket_lifecycle(settings.MINIO_BUCKET, LifecycleConfig([rule]))
    except Exception:
        # 个别 MinIO/S3 兼容实现可能不支持,失败不阻断主流程(可另配定时清理)
        pass


def _minio() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        if not _client.bucket_exists(settings.MINIO_BUCKET):
            _client.make_bucket(settings.MINIO_BUCKET)
        _ensure_lifecycle(_client)
    return _client


def to_csv_bytes(result: QueryResult) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow(["" if v is None else v for v in row])
    # UTF-8 BOM 便于 Excel 直接打开中文
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def upload_csv(object_key: str, data: bytes) -> None:
    client = _minio()
    client.put_object(
        settings.MINIO_BUCKET,
        object_key,
        io.BytesIO(data),
        length=len(data),
        content_type="text/csv; charset=utf-8",
    )


def presigned_url(object_key: str, filename: str | None = None) -> str:
    from datetime import timedelta

    extra = None
    if filename:
        extra = {"response-content-disposition": f'attachment; filename="{filename}"'}
    return _minio().presigned_get_object(
        settings.MINIO_BUCKET,
        object_key,
        expires=timedelta(seconds=settings.DOWNLOAD_URL_EXPIRE_SECONDS),
        response_headers=extra,
    )
