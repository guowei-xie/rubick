"""订阅结果的保留期:result_expired 的 subscribe 分支、清理侧的受保护键集合。

口径:订阅结果保留到**下一期成功结果产生**(superseded_at 被盖章)为止,且不低于常规
保留天数;两侧(模型 property / 文件清理)必须互为镜像 —— 本文件同时钉住两边。
"""
import os
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.models.query_job import JOB_SUCCESS, SOURCE_RUN, SOURCE_SUBSCRIBE, QueryJob
from app.models.user import ROLE_USER
from app.services import result_service, subscription_service

# ID 段 9240
OWNER = 9240


@pytest.fixture
def owner(user_factory):
    return user_factory(OWNER, ROLE_USER, "保留期用户", prefix="ret")


@pytest.fixture
def ds(datasource_factory):
    return datasource_factory("ret-mysql")


def _job(db, owner, ds, *, source, age_days=0, superseded=False, key="jobs/r/x.csv"):
    job = QueryJob(
        user_id=owner.id, template_id=1, datasource_id=ds.id, params={},
        status=JOB_SUCCESS, source=source, result_object_key=key,
    )
    db.add(job)
    db.commit()
    values = {}
    if age_days:
        values["created_at"] = datetime.now() - timedelta(days=age_days)
    if superseded:
        values["superseded_at"] = datetime.now()
    if values:
        db.execute(update(QueryJob).where(QueryJob.id == job.id).values(**values))
        db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------- result_expired


def test_subscribe_result_not_expired_until_superseded(db, owner, ds):
    """未被下一期取代:哪怕远超常规保留天数也不过期(月频订阅的第 8 天还得能下载)。"""
    job = _job(db, owner, ds, source=SOURCE_SUBSCRIBE, age_days=settings.RESULT_RETENTION_DAYS + 3)
    assert job.result_expired is False


def test_subscribe_result_expires_after_superseded_and_retention(db, owner, ds):
    old = _job(db, owner, ds, source=SOURCE_SUBSCRIBE,
               age_days=settings.RESULT_RETENTION_DAYS + 3, superseded=True)
    assert old.result_expired is True
    fresh = _job(db, owner, ds, source=SOURCE_SUBSCRIBE, age_days=1, superseded=True)
    assert fresh.result_expired is False  # 被取代了,但还没满常规保留天数


def test_normal_run_retention_is_unchanged(db, owner, ds):
    """回归:非订阅结果照旧按天数过期,不受 superseded_at 影响。"""
    job = _job(db, owner, ds, source=SOURCE_RUN, age_days=settings.RESULT_RETENTION_DAYS + 1)
    assert job.result_expired is True


# ---------------------------------------------------------------- 清理侧


def _old_csv(key: str) -> str:
    """在结果目录里造一个 mtime 早已过期的 CSV,返回 object_key。"""
    result_service.upload_csv(key, b"c\n1\n")
    path = result_service.local_path(key)
    stale = time.time() - (settings.RESULT_RETENTION_DAYS + 5) * 86400
    os.utime(path, (stale, stale))
    return key


def test_cleanup_skips_protected_keys(db):
    protected = _old_csv("jobs/ret-p/p.csv")
    doomed = _old_csv("jobs/ret-d/d.csv")

    removed = result_service.cleanup_expired(frozenset({protected}))

    assert removed >= 1
    assert result_service.exists(protected) is True
    assert result_service.exists(doomed) is False


def test_cleanup_default_behavior_unchanged(db):
    doomed = _old_csv("jobs/ret-plain/x.csv")
    result_service.cleanup_expired()
    assert result_service.exists(doomed) is False


def test_protected_result_keys_mirror_result_expired(db, owner, ds):
    """保护名单 = 「result_expired 为 False 的订阅结果」:未取代的老结果在列,
    已取代且过保留期的不在,普通取数结果从来不在。"""
    keep_unsuperseded = _job(db, owner, ds, source=SOURCE_SUBSCRIBE,
                             age_days=settings.RESULT_RETENTION_DAYS + 3,
                             key="jobs/ret-k1/a.csv")
    keep_recent = _job(db, owner, ds, source=SOURCE_SUBSCRIBE, age_days=1,
                       superseded=True, key="jobs/ret-k2/b.csv")
    drop_old = _job(db, owner, ds, source=SOURCE_SUBSCRIBE,
                    age_days=settings.RESULT_RETENTION_DAYS + 3,
                    superseded=True, key="jobs/ret-k3/c.csv")
    normal = _job(db, owner, ds, source=SOURCE_RUN, key="jobs/ret-k4/d.csv")

    keys = subscription_service.protected_result_keys()

    assert keep_unsuperseded.result_object_key in keys
    assert keep_recent.result_object_key in keys
    assert drop_old.result_object_key not in keys
    assert normal.result_object_key not in keys
