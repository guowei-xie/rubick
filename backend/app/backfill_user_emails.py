"""一次性:用飞书通讯录给存量用户补齐邮箱(以及姓名/头像)。

背景:`users.email` 列一直存在,但飞书应用此前没有「获取用户邮箱信息」权限,登录也就
带不回邮箱 —— 于是这个字段全库为 NULL,而依赖它的功能(管理端用户列表的邮箱列、
授权选人下拉的邮箱、团队候选人按邮箱搜、BOOTSTRAP_ADMINS 按邮箱认管理员)全都
处于「写好了但永远显示空白 / 永远匹配不上」的状态。权限现已开通,本脚本补齐存量。

新用户不需要这个脚本:登录时会自动补(email 为空才补,见 auth_service._upsert_user),
permission_service.grant 落库时也会从通讯录取。本脚本只解决「已经在库里、邮箱是空的」
那批人 —— 尤其是「被授权时落库、但从不登录」的用户,他们永远走不到登录钩子。

**幂等**:默认只处理 email 为 NULL/空 的用户;补过的人重跑不会再打飞书。
反复执行安全。

注意应用的通讯录「授权范围」通常不是全员:范围外或已离职的 open_id 查不到资料,
脚本会如实报告「无数据」并跳过,不报错、不清空已有值。

安全要求(与 migrate.py / renumber_users.py 同):
  - 先在本地 SQLite 验证,再在部署窗口对线上 MySQL 执行;**勿直接改线上库**。
  - 需要能连飞书:config.ini 里的 FEISHU_APP_ID / FEISHU_APP_SECRET 必须有效。

用法(在 backend 目录):
    python -m app.backfill_user_emails                  # dry-run:只打印将补什么,不写库(默认)
    python -m app.backfill_user_emails --apply          # 实际写库
    python -m app.backfill_user_emails --all --apply    # 连已有邮箱的一起重新同步(同事换邮箱后用)
    python -m app.backfill_user_emails --limit 20       # 只看前 20 个,先小范围试
"""
from __future__ import annotations

import argparse

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  注册所有模型
from app.core.database import SessionLocal, engine
from app.models.user import User
from app.services import feishu_service, user_service


def select_targets(db: Session, *, all_: bool = False, limit: int | None = None) -> list[User]:
    """待补齐的用户。默认只取邮箱为空的;all_=True 取全部(重新同步)。

    按 id 排序而非 last_login_at:本脚本关心的恰恰是「从不登录」的那批人,
    用登录时间排序会把他们全甩到末尾,加 --limit 时就永远试不到。
    """
    stmt = select(User).order_by(User.id)
    if not all_:
        stmt = stmt.where(or_(User.email.is_(None), User.email == ""))
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def run(db: Session, users: list[User], *, apply: bool) -> tuple[int, int]:
    """逐个查通讯录并打印结果。返回 (补到资料的人数, 通讯录无数据的人数)。

    apply=False 时照样查飞书(那是只读的)、照样打印,只是最后不 commit —— 这样 dry-run
    看到的就是真实结果,而不是一份猜测。
    """
    if not users:
        print("[backfill] 没有需要补齐的用户。")
        return 0, 0

    # 一次批量查完(fetch_contact_profiles 内部按 50 分片),再逐人比对打印
    try:
        profiles = feishu_service.fetch_contact_profiles([u.feishu_open_id for u in users])
    except Exception as exc:  # noqa: BLE001 脚本要如实报错,不像登录钩子那样静默
        print(f"[backfill] 查飞书通讯录失败,未做任何改动:{exc}")
        raise SystemExit(1) from exc

    filled = missing = 0
    for user in users:
        profile = profiles.get(user.feishu_open_id) or {}
        email = profile.get("email")
        if not email:
            missing += 1
            print(f"  - id={user.id:<4} {user.name:<12} {user.feishu_open_id}  → 通讯录无数据(授权范围外/已离职)")
            continue
        filled += 1
        was = user.email or "(空)"
        arrow = f"{was} → {email}" if was != email else f"{email}(未变)"
        print(f"  ✓ id={user.id:<4} {user.name:<12} {user.feishu_open_id}  → {arrow}")
        user_service.apply_profile(user, profile)

    if apply:
        db.commit()
        print(f"[backfill] 已写库:处理 {len(users)} 人,补齐 {filled} 人,{missing} 人通讯录无数据。")
    else:
        db.rollback()
        print(f"[backfill] dry-run 结束,未写库:处理 {len(users)} 人,可补齐 {filled} 人,{missing} 人无数据。")
        print("[backfill] 确认无误后加 --apply 执行。")
    return filled, missing


def main() -> None:
    parser = argparse.ArgumentParser(description="用飞书通讯录给存量用户补齐邮箱")
    parser.add_argument("--apply", action="store_true", help="实际写库(默认仅 dry-run)")
    parser.add_argument("--all", action="store_true", help="连已有邮箱的用户一起重新同步")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 个用户")
    args = parser.parse_args()

    print(f"[backfill] 目标库:{engine.url}")
    with SessionLocal() as db:
        users = select_targets(db, all_=args.all, limit=args.limit)
        print(f"[backfill] 待处理 {len(users)} 人" + ("(--all:含已有邮箱者)" if args.all else "(邮箱为空者)"))
        run(db, users, apply=args.apply)


if __name__ == "__main__":
    main()
