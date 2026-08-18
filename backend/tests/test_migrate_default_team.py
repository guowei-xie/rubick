"""存量任务并入「默认团队」的前向迁移:回填、成员纳入、幂等、以及「任务必属团队」体检。

仿 test_department_removal.py:直接对测试 SQLite 下手,用裸 SQL 造出「存量库」的形状
(任务的 team_id 为空)—— 这正是模型把 team_id 声明为**可空**的原因:声明 NOT NULL
就造不出这个状态,迁移逻辑也就永远无法回归。
"""
import pytest
from sqlalchemy import func, select, text

from app.core.database import engine, tbl
from app.migrate import _assert_every_template_has_team, _migrate_default_team
from app.models.team import DEFAULT_TEAM_NAME, Team, TeamMember
from app.models.template import SqlTemplate

_TS = "2026-06-01 00:00:00"

# ID 段 8670–8673(与其它测试文件不重叠)
M_ADMIN, M_DEV, M_PLAIN = 8670, 8671, 8672


@pytest.fixture
def legacy(db):
    """造一个「团队功能上线前」的库状态,并在用例结束后清干净 ——
    本文件会真的动 teams / team_members / sql_templates,不能污染别的测试。
    """
    with engine.begin() as c:
        c.execute(
            text(
                f"INSERT INTO {tbl('users')} "
                "(id, feishu_open_id, name, role, is_active, created_at, updated_at, last_login_at) "
                "VALUES (:a,'ou_mdt_admin','迁移管理员','admin',1,:t,:t,:t),"
                "       (:d,'ou_mdt_dev','迁移开发者','developer',1,:t,:t,:t),"
                "       (:p,'ou_mdt_plain','迁移普通用户','user',1,:t,:t,:t)"
            ),
            {"a": M_ADMIN, "d": M_DEV, "p": M_PLAIN, "t": _TS},
        )
        c.execute(
            text(
                f"INSERT INTO {tbl('data_sources')} "
                "(id, name, engine, host, port, username, extra, is_active, created_at, updated_at) "
                "VALUES (9701,'mdt-ds','mysql','h',3306,'u','{}',1,:t,:t)"
            ),
            {"t": _TS},
        )
        # 两个存量任务:一个作者是开发者(会被纳入默认团队),一个作者是普通用户(不会)
        c.execute(
            text(
                f"INSERT INTO {tbl('sql_templates')} "
                "(id, name, datasource_id, dialect, status, author_id, tags, team_id, "
                " created_at, updated_at) "
                "VALUES (9711,'存量-开发者的任务',9701,'mysql','published',:d,'[]',NULL,:t,:t),"
                "       (9712,'存量-普通用户的老任务',9701,'mysql','draft',:p,'[]',NULL,:t,:t)"
            ),
            {"d": M_DEV, "p": M_PLAIN, "t": _TS},
        )
    yield
    with engine.begin() as c:
        c.execute(text(f"DELETE FROM {tbl('sql_templates')} WHERE id IN (9711, 9712)"))
        c.execute(
            text(
                f"DELETE FROM {tbl('team_members')} WHERE team_id IN "
                f"(SELECT id FROM {tbl('teams')} WHERE name = :n)"
            ),
            {"n": DEFAULT_TEAM_NAME},
        )
        c.execute(text(f"DELETE FROM {tbl('teams')} WHERE name = :n"), {"n": DEFAULT_TEAM_NAME})
        c.execute(text(f"DELETE FROM {tbl('data_sources')} WHERE id = 9701"))
        c.execute(
            text(f"DELETE FROM {tbl('users')} WHERE id IN (:a,:d,:p)"),
            {"a": M_ADMIN, "d": M_DEV, "p": M_PLAIN},
        )


def _default_team(db) -> Team | None:
    db.expire_all()
    return db.scalar(select(Team).where(Team.name == DEFAULT_TEAM_NAME))


def test_backfills_orphan_tasks_and_enrolls_authors_roles(db, legacy, capsys):
    _migrate_default_team()
    team = _default_team(db)
    assert team is not None, "有存量无主任务时必须建出默认团队"

    # 两个存量任务都并入了
    for tid in (9711, 9712):
        assert db.get(SqlTemplate, tid).team_id == team.id

    members = {
        uid: is_admin
        for uid, is_admin in db.execute(
            select(TeamMember.user_id, TeamMember.is_team_admin).where(
                TeamMember.team_id == team.id
            )
        )
    }
    # 管理员当团队管理员、开发者当普通成员 —— 否则切换后每个开发者都「没有团队」,
    # 连自己写的任务都看不见
    assert members.get(M_ADMIN) is True
    assert members.get(M_DEV) is False
    # 普通用户**不**入队:加入团队等于拿到该团队取数账号的全部数据权限,不能静默扩权
    assert M_PLAIN not in members

    # 但要把这件事**打印出来**交人工决定(改角色 / 加成员 / 转移任务)
    out = capsys.readouterr().out
    assert "作者不在其所属团队内" in out
    assert "存量-普通用户的老任务" in out


def test_is_idempotent(db, legacy, capsys):
    _migrate_default_team()
    team_id = _default_team(db).id
    n_members = db.scalar(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)
    )

    _migrate_default_team()  # 第二遍应当是 no-op
    out = capsys.readouterr().out
    assert "无需建立" in out
    assert _default_team(db).id == team_id, "不该建出第二个默认团队"
    assert (
        db.scalar(
            select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)
        )
        == n_members
    ), "不该重复插入成员行"


def test_health_check_passes_after_migration(db, legacy):
    _migrate_default_team()
    _assert_every_template_has_team()  # 不抛即通过


def test_health_check_fails_on_a_remaining_orphan(db, legacy):
    """「无主任务」除平台管理员外谁都看不到、也解析不出取数身份。

    宁可在部署窗口失败(有人看着、信息明确),也不要到运行期变成一句「无权查看该任务」。
    """
    _migrate_default_team()
    with engine.begin() as c:
        c.execute(
            text(f"UPDATE {tbl('sql_templates')} SET team_id = NULL WHERE id = 9711")
        )
    with pytest.raises(RuntimeError, match="没有所属团队"):
        _assert_every_template_has_team()


def test_creates_a_team_for_authors_even_without_orphan_tasks(monkeypatch, capsys):
    """库里已有开发者但一个团队都没有时也要建 —— 否则他们连建任务时能选的团队都没有,
    而那个报错会发生在业务侧、离迁移很远,没人联想得到。

    这条判定是**全库级**的(「有没有团队」),没法在共享的测试库里构造而不破坏别的用例。
    故给 app.migrate 换一个**独立的临时库** —— 这也顺带验证了迁移不依赖任何全局状态。
    """
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SASession

    import app.migrate as mig
    from app.core.database import Base

    tmp = Path(tempfile.mkdtemp(prefix="rubick-mdt-")) / "iso.db"
    iso = create_engine(f"sqlite:///{tmp}")
    Base.metadata.create_all(iso)
    with iso.begin() as c:
        c.execute(
            text(
                f"INSERT INTO {tbl('users')} "
                "(id, feishu_open_id, name, role, is_active, created_at, updated_at) "
                "VALUES (1,'ou_lone_dev','孤身开发者','developer',1,:t,:t)"
            ),
            {"t": _TS},
        )
    monkeypatch.setattr(mig, "engine", iso)

    _migrate_default_team()

    assert "一个团队都没有" in capsys.readouterr().out
    with SASession(iso) as s:
        team = s.scalar(select(Team).where(Team.name == DEFAULT_TEAM_NAME))
        assert team is not None
        # 那名开发者被纳入,否则他登录后一个能选的团队都没有
        assert s.scalar(
            select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)
        ) == 1


def test_fresh_empty_database_creates_no_team(monkeypatch, capsys):
    """全新空库两个触发条件都不成立 ⇒ 不造一个没人要的团队。"""
    import tempfile
    from pathlib import Path

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SASession

    import app.migrate as mig
    from app.core.database import Base

    tmp = Path(tempfile.mkdtemp(prefix="rubick-mdt-empty-")) / "empty.db"
    iso = create_engine(f"sqlite:///{tmp}")
    Base.metadata.create_all(iso)
    monkeypatch.setattr(mig, "engine", iso)

    _migrate_default_team()

    assert "无需建立" in capsys.readouterr().out
    with SASession(iso) as s:
        assert s.scalar(select(func.count()).select_from(Team)) == 0
