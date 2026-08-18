"""邮箱打通:通讯录是 email 的唯一可信来源。

飞书应用开通「获取用户邮箱信息」后,email 才真正有值。这里锁住三条不能回归的保证:
  1. 补齐永不抛异常 —— 登录/授权不能因为补邮箱失败而失败;
  2. 登录时「先补邮箱、再按邮箱提权」的顺序(重排后 BOOTSTRAP_ADMINS 写邮箱会静默失效);
  3. 客户端传来的 email 不被采信(否则是一条提权路径)。

飞书全部走 monkeypatch 打桩(照 conftest 的 spy_connector 约定:patch 模块上的名字),
不发真实网络请求。
"""
from sqlalchemy import func, select

from app.api.routes.admin import list_users
from app.api.routes.permissions import grant as grant_route
from app.core.config import settings
from app.models.user import ROLE_ADMIN, User
from app.schemas.permission import GrantIn
from app.services import auth_service, feishu_service, user_service
from app import backfill_user_emails as backfill

# 本文件占用 id 段 6100-6199(库不按用例清理,见 conftest)
OU = "ou_email_"


def _user(db, uid: int, suffix: str, name: str = "某人", **kw) -> User:
    """本文件的用户工厂。conftest.user_factory 不收 email、也不让指定 open_id,
    而这里的用例恰恰要按 open_id 打通讯录的桩。"""
    u = User(id=uid, feishu_open_id=f"{OU}{suffix}", name=name, role=kw.pop("role", "user"), **kw)
    db.add(u)
    db.flush()
    return u


def _profile(open_id: str, email: str | None = "someone@hetao101.com", name: str = "某人") -> dict:
    return {"open_id": open_id, "name": name, "email": email, "avatar": None, "union_id": None}


def _stub_contacts(monkeypatch, mapping: dict[str, dict] | None = None, *, raises: bool = False):
    """替掉 user_service 看到的 fetch_contact_profiles。mapping 里没有的 open_id = 授权范围外。"""
    calls: list[list[str]] = []

    def fake(open_ids):
        calls.append(list(open_ids))
        if raises:
            raise RuntimeError("飞书挂了")
        return {k: v for k, v in (mapping or {}).items() if k in open_ids}

    monkeypatch.setattr(feishu_service, "fetch_contact_profiles", fake)
    return calls


# ---- sync_profiles_from_feishu ----


def test_sync_fills_email(db, monkeypatch):
    u = _user(db, 6101, "101", "旧名")
    _stub_contacts(monkeypatch, {f"{OU}101": _profile(f"{OU}101", "a@hetao101.com", "新名")})

    assert user_service.sync_profiles_from_feishu([u]) == 1
    assert u.email == "a@hetao101.com"
    assert u.name == "新名"  # 顺带补齐姓名


def test_sync_skips_open_id_out_of_scope(db, monkeypatch):
    """人在应用「授权范围」外 → 通讯录缺项。必须静默跳过,不抛、不动现有资料。"""
    u = _user(db, 6102, "102", "范围外")
    _stub_contacts(monkeypatch, {})  # 什么都没返回

    assert user_service.sync_profiles_from_feishu([u]) == 0
    assert u.email is None
    assert u.name == "范围外"


def test_sync_never_raises_when_feishu_down(db, monkeypatch):
    """通讯录不可用时返回 0,调用方无感 —— 登录不能因为补邮箱失败而 500。"""
    u = _user(db, 6103, "103", "c")
    _stub_contacts(monkeypatch, raises=True)

    assert user_service.sync_profiles_from_feishu([u]) == 0
    assert u.email is None


def test_sync_does_not_clobber_existing_email(db, monkeypatch):
    """空值不覆盖(复用 upsert_user 的规则);有新值则更新(--all 重新同步场景)。"""
    u = _user(db, 6104, "104", "d", email="old@hetao101.com")

    _stub_contacts(monkeypatch, {f"{OU}104": _profile(f"{OU}104", None)})
    user_service.sync_profiles_from_feishu([u])
    assert u.email == "old@hetao101.com"  # 通讯录没返回 → 保留原值

    _stub_contacts(monkeypatch, {f"{OU}104": _profile(f"{OU}104", "new@hetao101.com")})
    user_service.sync_profiles_from_feishu([u])
    assert u.email == "new@hetao101.com"


# ---- 登录路径:补邮箱必须早于按邮箱提权 ----


def test_login_promotes_by_email_via_backfill(db, monkeypatch):
    """BOOTSTRAP_ADMINS 写邮箱时,首次登录就该提权 —— 即使 OAuth 没带回 email。

    这条锁住 auth_service._upsert_user 里「先 sync_profiles_from_feishu、再 _maybe_promote」
    的顺序。顺序反了本用例会红,而线上表现是「配置写了邮箱但永远不生效」且完全静默。
    """
    open_id = f"{OU}110"
    boss = "boss@hetao101.com"
    _user(db, 6110, "110", "老板")

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMINS", boss)
    # OAuth 不带 email(模拟 OAUTH_SCOPES 里没加 contact:user.email:readonly)
    monkeypatch.setattr(
        auth_service.feishu_service, "exchange_code",
        lambda code: {"open_id": open_id, "name": "老板", "email": None},
    )
    _stub_contacts(monkeypatch, {open_id: _profile(open_id, boss, "老板")})

    _, user = auth_service.login_with_code(db, "fake-code")
    assert user.email == boss
    assert user.role == ROLE_ADMIN, "补齐邮箱后应立刻命中 BOOTSTRAP_ADMINS"


def test_login_does_not_recall_contacts_once_email_known(db, monkeypatch):
    """已有邮箱的人登录不再打通讯录(补齐只在 email 为空时触发)。"""
    open_id = f"{OU}111"
    _user(db, 6111, "111", "老用户", email="has@hetao101.com")

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMINS", "")
    monkeypatch.setattr(
        auth_service.feishu_service, "exchange_code",
        lambda code: {"open_id": open_id, "name": "老用户", "email": None},
    )
    calls = _stub_contacts(monkeypatch, {open_id: _profile(open_id)})

    auth_service.login_with_code(db, "fake-code")
    assert calls == [], "email 已有值时不该再查通讯录"


# ---- 授权路径:不采信客户端传来的 email ----


def test_grant_ignores_client_supplied_email(db, monkeypatch):
    """GrantIn 已无 subject_email 字段;即便硬塞也不得落库(否则是提权路径)。"""
    admin = _user(db, 6120, "120", "admin", role=ROLE_ADMIN)
    _user(db, 6121, "121", "被授权人")

    # pydantic 默认忽略未声明字段:传了也进不了模型
    payload = GrantIn(
        subject_open_id=f"{OU}121", subject_name="被授权人",
        subject_email="boss@hetao101.com", resource_id="6199",
    )
    assert not hasattr(payload, "subject_email")

    # 通讯录返回的才是可信值
    monkeypatch.setattr(
        feishu_service, "fetch_contact_profiles",
        lambda ids: {f"{OU}121": _profile(f"{OU}121", "real@hetao101.com", "被授权人")},
    )
    grant_route(payload, db, admin, ip=None)
    assert db.get(User, 6121).email == "real@hetao101.com"


def test_grant_leaves_email_empty_when_contacts_unavailable(db, monkeypatch):
    """通讯录查不到时宁可空着,也不落一个不可信的值。"""
    admin = _user(db, 6122, "122", "admin2", role=ROLE_ADMIN)
    _user(db, 6123, "123", "范围外的人")

    def boom(ids):
        raise RuntimeError("飞书挂了")

    monkeypatch.setattr(feishu_service, "fetch_contact_profiles", boom)
    out = grant_route(
        GrantIn(subject_open_id=f"{OU}123", subject_name="范围外的人", resource_id="6198"),
        db, admin, ip=None,
    )
    assert out, "通讯录不可用不该挡住授权"
    assert db.get(User, 6123).email is None


# ---- 分片 ----


def _stub_batch(monkeypatch, *, bad: tuple[str, ...] = ()):
    """替掉 _batch_get_users,记录每次的入参分片;bad 里的 id 会让整批被打回。"""
    calls: list[list[str]] = []

    def fake(open_ids):
        calls.append(list(open_ids))
        if any(i in bad for i in open_ids):
            raise feishu_service._InvalidOpenIdInBatch("含飞书不认的 open_id")
        return {i: {"open_id": i, "email": f"{i}@hetao101.com", "name": i} for i in open_ids}

    monkeypatch.setattr(feishu_service.settings, "FEISHU_APP_ID", "cli_fake")
    monkeypatch.setattr(feishu_service, "_batch_get_users", fake)
    return calls


def test_fetch_contact_profiles_chunks_at_50(monkeypatch):
    """/contact/v3/users/batch 文档上限 50。分片在 fetch_contact_profiles,不在 _batch_get_users。"""
    seen = _stub_batch(monkeypatch)

    ids = [f"ou_{n}" for n in range(120)]
    out = feishu_service.fetch_contact_profiles(ids)

    assert [len(c) for c in seen] == [50, 50, 20]
    assert len(out) == 120


def test_fetch_contact_profiles_dedupes_and_skips_unconfigured(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        feishu_service, "_batch_get_users",
        lambda ids: (calls.append(list(ids)), {})[1],
    )

    # 未配凭证 → 直接空,不打网络
    monkeypatch.setattr(feishu_service.settings, "FEISHU_APP_ID", "")
    assert feishu_service.fetch_contact_profiles(["ou_a"]) == {}
    assert calls == []

    # 去重且保序
    monkeypatch.setattr(feishu_service.settings, "FEISHU_APP_ID", "cli_fake")
    feishu_service.fetch_contact_profiles(["ou_a", "ou_b", "ou_a", None, ""])
    assert calls == [["ou_a", "ou_b"]]


# ---- 管理端按邮箱搜 ----


def test_admin_list_users_matches_email(db, user_factory):
    """列表一直显示邮箱列,搜索也必须能按邮箱命中。"""
    admin = user_factory(6130, ROLE_ADMIN, "管理员", prefix="email")
    target = user_factory(6131, "user", "张三", prefix="email")
    target.email = "zhangsan@hetao101.com"
    db.commit()

    by_email = list_users(q="zhangsan", db=db, _=admin)
    assert 6131 in [u.id for u in by_email]

    by_name = list_users(q="张三", db=db, _=admin)
    assert 6131 in [u.id for u in by_name]

    assert 6131 not in [u.id for u in list_users(q="不存在的关键词", db=db, _=admin)]


# ---- 存量回填脚本 ----


def test_backfill_selects_only_empty_emails_by_default(db):
    _user(db, 6140, "140", "有邮箱", email="has@hetao101.com")
    _user(db, 6141, "141", "空邮箱")
    _user(db, 6142, "142", "空串", email="")
    db.commit()

    ids = {u.id for u in backfill.select_targets(db)}
    assert {6141, 6142} <= ids, "邮箱为 NULL 或空串的都该入选"
    assert 6140 not in ids, "已有邮箱的默认不重复处理(幂等)"

    assert 6140 in {u.id for u in backfill.select_targets(db, all_=True)}


def test_backfill_dry_run_writes_nothing(db, monkeypatch):
    u = _user(db, 6143, "143", "待补")
    db.commit()
    _stub_contacts(monkeypatch, {f"{OU}143": _profile(f"{OU}143", "dry@hetao101.com")})

    filled, missing = backfill.run(db, [u], apply=False)
    assert (filled, missing) == (1, 0)  # 如实报告「可补 1 人」
    db.expire_all()
    assert db.get(User, 6143).email is None, "dry-run 不得写库"


def test_backfill_apply_fills(db, monkeypatch):
    u = _user(db, 6144, "144", "待补2")
    out_of_scope = _user(db, 6145, "145", "范围外")
    db.commit()
    _stub_contacts(monkeypatch, {f"{OU}144": _profile(f"{OU}144", "ok@hetao101.com")})

    filled, missing = backfill.run(db, [u, out_of_scope], apply=True)
    assert (filled, missing) == (1, 1)
    db.expire_all()
    assert db.get(User, 6144).email == "ok@hetao101.com"
    assert db.get(User, 6145).email is None

    # 幂等:补过的人不再出现在待处理清单里
    assert 6144 not in {x.id for x in backfill.select_targets(db)}


def test_fetch_contact_profiles_survives_one_invalid_open_id(monkeypatch):
    """一个失效 open_id 会让飞书把**整批**打回(code 99992351,好的一起没了)。

    必须降级为逐个查,否则库里只要有一个离职销号/seed 造的假 id,整批补齐就全军覆没 ——
    而且表现是「所有人都查不到」,极易被误读成「权限没开」。
    """
    good, bad = "ou_good", "ou_bad"
    calls = _stub_batch(monkeypatch, bad=(bad,))

    out = feishu_service.fetch_contact_profiles([good, bad])
    assert set(out) == {good}, "好的必须捞回来,坏的缺项"
    # 先整批(失败)→ 再逐个
    assert calls == [[good, bad], [good], [bad]]


def test_fetch_contact_profiles_still_raises_on_real_failure(monkeypatch):
    """真实故障(凭证错/飞书挂了)必须向上抛,不能伪装成「大家都没邮箱」。"""
    monkeypatch.setattr(feishu_service.settings, "FEISHU_APP_ID", "cli_fake")

    def boom(open_ids):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(feishu_service, "_batch_get_users", boom)
    try:
        feishu_service.fetch_contact_profiles(["ou_x"])
    except RuntimeError:
        pass
    else:
        raise AssertionError("真实故障应向上抛")


# ---- 补齐不得建出第二行(SessionLocal 是 autoflush=False)----


def test_sync_merges_onto_unflushed_new_user(db, monkeypatch):
    """全新用户尚未 flush 时补邮箱,必须合并到同一个对象上,不能建出第二行。

    补齐一旦走「按 open_id 重新 select」,autoflush=False 下就查不到这个待插入的新用户,
    于是同一个 open_id 会被建成两行 —— commit 时撞 unique 约束,**首次登录直接 500**。
    这条用例是那个坑的护栏(修法:sync 走 apply_profile,不重新查库)。
    """
    oid = f"{OU}150"
    _stub_contacts(monkeypatch, {oid: _profile(oid, "brandnew@hetao101.com", "新人")})

    u = user_service.upsert_user(db, {"open_id": oid, "name": "新人"})
    assert len([o for o in db.new if isinstance(o, User)]) == 1
    user_service.sync_profiles_from_feishu([u])
    assert len([o for o in db.new if isinstance(o, User)]) == 1, "建出了第二行同 open_id 的用户"
    assert u.email == "brandnew@hetao101.com"


def test_login_of_brand_new_user_commits(db, monkeypatch):
    """端到端:从未见过的人首次登录能提交成功(上一条护栏的真实场景)。"""
    oid = f"{OU}151"
    monkeypatch.setattr(settings, "BOOTSTRAP_ADMINS", "")
    monkeypatch.setattr(
        auth_service.feishu_service, "exchange_code",
        lambda code: {"open_id": oid, "name": "首登新人", "email": None},
    )
    _stub_contacts(monkeypatch, {oid: _profile(oid, "first@hetao101.com", "首登新人")})

    _, user = auth_service.login_with_code(db, "fake-code")
    assert user.email == "first@hetao101.com"
    assert db.scalar(
        select(func.count()).select_from(User).where(User.feishu_open_id == oid)
    ) == 1


# ---- 启动提权:名单写邮箱时也要生效 ----


def test_apply_bootstrap_admins_backfills_email_first(db, monkeypatch):
    """BOOTSTRAP_ADMINS 写邮箱 + 重启,应该对「从未登录过」的人也生效。

    README 承诺「改配置重启即提权,无需其再登录」。启动那趟不补邮箱的话,这条承诺对
    邮箱形式的名单永远是空话(且完全静默)。
    """
    oid = f"{OU}160"
    boss = "startup-boss@hetao101.com"
    u = _user(db, 6160, "160", "没登录过的老板")  # email 为空,从未登录
    db.commit()

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMINS", boss)
    _stub_contacts(monkeypatch, {oid: _profile(oid, boss, "没登录过的老板")})

    assert auth_service.apply_bootstrap_admins(db) >= 1
    db.refresh(u)
    assert u.email == boss
    assert u.role == ROLE_ADMIN


def test_apply_bootstrap_admins_skips_contacts_for_open_id_list(db, monkeypatch):
    """名单里只有 open_id 时不该白打飞书 —— 提权根本不需要邮箱。"""
    u = _user(db, 6161, "161", "按 open_id 提权的人")
    db.commit()

    monkeypatch.setattr(settings, "BOOTSTRAP_ADMINS", u.feishu_open_id)
    calls = _stub_contacts(monkeypatch, {})

    assert auth_service.apply_bootstrap_admins(db) >= 1
    db.refresh(u)
    assert u.role == ROLE_ADMIN
    assert calls == [], "名单里没有邮箱,不该去查通讯录"
