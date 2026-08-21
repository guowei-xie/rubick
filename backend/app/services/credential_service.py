"""团队取数账号:哪个团队的库身份用在哪次取数上。

**为什么存在**:公共账号能看到目标库的全部数据,于是「谁能建任务」等价于「谁能看全部数据」,
平台侧无从做取数权限控制。让每个团队登记自己的库账号、任务用**所属团队**的账号取数后,
越权与否交回数据库裁决 —— 平台不再需要复刻一套数据权限模型。

**唯一的身份解析入口**:所有取数链路(正式取数 / 枚举候选值 / 编辑器试跑)都只经由这里
拿 Credential,不各自去查凭证表。因此「用谁的账号」这条规则只在本模块表述一次:

- 正式取数、更新枚举候选值 → 任务所属团队(SqlTemplate.team_id)。业务用户跑的是别人写的
  任务,数据边界理应是那个团队的,而不是随发起人漂移。
- 编辑器试跑 / 测枚举 SQL   → **同一个团队账号**,且操作者必须是该团队成员。

第二条是对「个人账号」时代的实质修正:那时试跑用操作者本人、正式取数用作者,于是
「试跑通过」并不代表「上线后能跑」(两套账号的库权限可能不同),问题最终落到业务用户面前。
团队化后两者是同一套身份,**试跑通过第一次真正等价于上线后能跑**。

**没有开关**。团队账号是唯一路径,`ds.public_credential` 只剩管理员测数据源连通性一个用处。
早先那个 REQUIRE_OWNER_CREDENTIAL 过渡开关已删除:它的每一条 false 分支都是一次静默回退到
「能看全库的公共账号」,而那种回退不会让任何测试失败。

⚠️ **团队即数据边界**:团队账号是共享的,任何成员都能在编辑器里用它试跑任意 SQL。
所以团队账号能读到的数据,全体团队成员都能读到 —— 把人加进团队等于一次数据授权。
「密码不可见」防的是账号被带出平台,防不了(也不打算防)成员在平台内用它取数。

**密码只写不读**:所有 Out 模型都不含 password(见 schemas/credential.py)。库**用户名**属
半机密(Hive 在 auth=NONE 下用户名本身就是完整凭证,拿到就能用本地客户端绕过平台直连),
故只对该团队的团队管理员、平台管理员与审计日志可见 —— 见 reveal_username 参数与 redact()。

**「就绪」= 已登记账号,测试连接不是必选项**:配好用户名/密码即可上线与运行,
`verified`(最近一次连接测试是否通过)只是给团队管理员的自检信息与页面上的弱提醒,
不参与任何拦截。理由:测通与否只代表**那一刻**连得上,库侧权限随时会变,拿它当卡点既拦不住
真正的失败,又会因为「改完密码忘了点测试」让该数据源上全团队的任务集体停摆 ——
把偶发的连不上留给运行时报错(错误文案已指名团队与补救路径),比预先卡住所有人划算。
真正必须拦的只有「压根没有账号」:那时连都没得连,失败是必然的。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import Credential, get_connector
from app.core.exceptions import (
    CredentialRequiredError,
    NotFoundError,
    PermissionDeniedError,
    RubicError,
)
from app.models.credential import TeamDataSourceCredential
from app.models.datasource import DataSource
from app.models.team import Team
from app.models.template import STATUS_PUBLISHED, SqlTemplate
from app.models.user import User, is_platform_admin
from app.services import team_service


def get(db: Session, team_id: int, datasource_id: int) -> TeamDataSourceCredential | None:
    return db.scalar(
        select(TeamDataSourceCredential).where(
            TeamDataSourceCredential.team_id == team_id,
            TeamDataSourceCredential.datasource_id == datasource_id,
        )
    )


def _load_datasource(db: Session, datasource_id: int) -> DataSource:
    ds = db.get(DataSource, datasource_id)
    if ds is None:
        raise NotFoundError("数据源不存在")
    return ds


# ---------------------------------------------------------------- 身份解析


def _resolve(db: Session, ds: DataSource, team: Team | None, remedy: str) -> Credential:
    """team 在 ds 上的取数身份。**任何情况下都不回退公共账号** —— 那会成为绕过身份隔离的后门。

    私有:补救话术由调用方给出,所以只经由下面两个公开入口调用。
    """
    if team is None:
        # 拿不到团队(任务还没归属、或团队行被删)时明确失败
        raise CredentialRequiredError("无法确定该任务所属团队的取数账号,请联系平台管理员")

    cred = get(db, team.id, ds.id)
    if cred is None:
        raise CredentialRequiredError(
            f"团队《{team.name}》尚未配置数据源《{ds.name}》的取数账号,{remedy}"
        )
    # 刻意不看 cred.verified:测试连接是自愿的自检,不是取数的前置条件(见模块 docstring)。
    # 账号真的连不上时,由引擎在本次取数里报错 —— 那条报错同样指名团队与补救路径。
    return Credential(
        username=cred.username, password=cred.password, owner_team_id=cred.team_id,
        entry_database=cred.entry_database,
    )


_REMEDY = "请联系该团队的团队管理员在「我的团队 → 团队取数账号」中登记账号"


def for_template(db: Session, tmpl: SqlTemplate) -> Credential:
    """任务运行(含更新枚举候选值)使用的身份 = 任务所属团队的账号。

    不收操作者:运行授权已由 permission_service 判过,身份跟着**任务**走,不随发起人漂移。
    """
    team = tmpl.team or (db.get(Team, tmpl.team_id) if tmpl.team_id else None)
    return _resolve(
        db, tmpl.datasource or _load_datasource(db, tmpl.datasource_id), team, _REMEDY
    )


def for_team(
    db: Session, *, team_id: int | None, datasource_id: int, actor: User | None
) -> Credential:
    """编辑器试跑 / 测枚举 SQL 使用的身份 = 该任务所属团队的账号。

    **成员校验长在这里,不在路由里**:team_id 是客户端传来的,少一处校验就等于
    「借别的团队的账号跑任意 SQL」。平台管理员放行(不受团队约束)。
    """
    if team_id is None:
        raise PermissionDeniedError("请选择任务所属团队")
    if actor is None:
        # 拿不到操作人时明确失败,绝不放行
        raise PermissionDeniedError("无法确定操作人,无法使用团队取数账号")
    team = team_service.get_team(db, int(team_id))
    if not is_platform_admin(actor) and not team_service.is_member(
        db, actor, team.id
    ):
        # 403 而非 409:这是权限问题,不是「补齐配置后重试就成」
        raise PermissionDeniedError(f"你不是团队《{team.name}》的成员,无法使用该团队的取数账号")
    return _resolve(
        db, _load_datasource(db, datasource_id), team,
        "请联系该团队的团队管理员登记账号后再试",
    )


def require_ready(db: Session, tmpl: SqlTemplate) -> None:
    """上线卡点:**只拦「压根没有账号」**,免得业务用户第一次点运行才发现连都没得连。

    不拦「未测通」:测试连接是非必选项(见模块 docstring),否则团队管理员改完密码忘了点一下,
    该数据源上全团队的任务就会集体下线。

    编辑一个已上线的任务会让新版本自动接替上线(见 template_service.add_version),
    那同样是一次上线,故文案要把这条路径也说清楚,否则作者会困惑「我只是点了保存」。
    """
    try:
        for_template(db, tmpl)
    except CredentialRequiredError as e:
        raise CredentialRequiredError(
            f"任务上线被拦下:{e}"
            "(编辑一个已上线的任务会自动重新上线,故保存时同样会做这项检查)"
        ) from e


# ---------------------------------------------------------------- 错误脱敏


def redact(text: str | None, credential: Credential | None) -> str:
    """把库账号名从**面向用户**的错误文案里抹掉。

    为什么必须做:引擎的鉴权错误长这样 `Access denied for user 'team_acct'@'10.0.0.5'`,
    而 job.error 会经 JobOut 展示给运行记录的查看者、还会被 notify_service 推进飞书通知
    —— 业务用户根本不属于这个团队,却能就此拿到团队库账号名。Hive 在 auth=NONE 下
    用户名就是完整凭证,泄一个等于泄掉整个团队的数据权限。

    审计 detail 里**保留原文**:那是 admin-only 的取证面,抹掉就查不出「哪个账号被拒了」。
    用户名短于 3 字符时不替换,免得在正文里到处打洞。
    """
    s = text or ""
    if credential and credential.username and len(credential.username) >= 3:
        s = s.replace(credential.username, "***")
    return s


# ---------------------------------------------------------------- 维护


def upsert(
    db: Session,
    *,
    team_id: int,
    datasource_id: int,
    username: str,
    password: str | None,
    entry_database: str | None = None,
    updated_by: int | None,
) -> tuple[TeamDataSourceCredential, bool]:
    """按 (team_id, datasource_id) 覆盖写,返回 (凭证行, 密码是否被改动)。

    password 传空表示保留原密码(与数据源编辑同一约定,见 routes/datasources.py)。
    entry_database 传空 = 用数据源配的 Database(见 models/credential.py::entry_database);
    它与用户名同属「这套凭证怎么连」,故变动时同样要清掉测通痕迹。
    用户名、密码或入口库有变动即清空 last_verified_at:那条记录是「**这套**凭证连通过」的凭据,
    换了凭证它就失效了,继续挂着只会误导团队管理员。清空**不影响任务能不能跑** ——
    测试连接是非必选项(见模块 docstring),所以这里也不需要前端做什么二次确认。
    """
    _load_datasource(db, datasource_id)
    username = (username or "").strip()
    if not username:
        raise RubicError("请填写取数账号的用户名")

    cred = get(db, team_id, datasource_id)
    new_password = password or None  # 空串等同于「没填」
    entry = (entry_database or "").strip() or None  # 空串等同于「用数据源的默认库」

    if cred is None:
        cred = TeamDataSourceCredential(
            team_id=team_id,
            datasource_id=datasource_id,
            username=username,
            password=new_password,
            entry_database=entry,
            updated_by=updated_by,
        )
        db.add(cred)
        db.commit()
        db.refresh(cred)
        return cred, new_password is not None

    password_changed = new_password is not None and new_password != cred.password
    if username != cred.username or password_changed or entry != cred.entry_database:
        cred.last_verified_at = None
        cred.last_verify_error = None
    cred.username = username
    if new_password is not None:
        cred.password = new_password
    cred.entry_database = entry
    cred.updated_by = updated_by
    db.commit()
    db.refresh(cred)
    return cred, password_changed


def probe(ds: DataSource, credential: Credential) -> list[str]:
    """用某个身份连一次目标库,失败转成可读错误;**返回这个账号能访问的库名列表**。

    团队凭证测通(verify)与管理员测数据源连通性(routes/datasources)共用这一处,
    否则两边的错误文案会各自漂移 —— 而开发者手册的报错速查表是按这个前缀写的。

    返回值的用处:团队管理员配完账号最想知道的就是「它到底能取什么数」。而这个问题
    与数据源上配的默认库无关(那只是不写库名时的解析起点),故连接器刻意不依赖它 ——
    见 DataSourceConnector.test_connection 的契约。
    """
    try:
        return get_connector(ds, credential).test_connection()
    except Exception as e:  # noqa: BLE001 -- 引擎/驱动异常一律转可读 400
        raise RubicError(f"连接失败:{str(e)[:400]}") from e


def verify(db: Session, cred: TeamDataSourceCredential) -> list[str]:
    """用这套凭证真连一次目标库(**自愿的自检,不是上线前置条件**)。
    成功记 last_verified_at,失败记原因并清空测通状态;返回 probe 给的「可访问库列表」。

    这条链路的价值是「让团队管理员当场知道账号填对没」,而不是给平台一个卡点 ——
    测通只代表那一刻连得上,库侧权限随时会变(见模块 docstring)。
    失败即清空:那条测通记录只对当次连接成立,留着会让人误以为账号还好着。
    注:两条路径都会在返回/抛出**之前**把状态落库并 commit —— 路由层的审计据此记 ok。
    """
    ds = _load_datasource(db, cred.datasource_id)
    try:
        databases = probe(
            ds,
            Credential(
                cred.username, cred.password, owner_team_id=cred.team_id,
                entry_database=cred.entry_database,
            ),
        )
    except RubicError as e:
        cred.last_verified_at = None
        # 落库保留原文(团队管理员要靠它自查);对普通成员的遮挡在读出口做 —— 见 _cell
        cred.last_verify_error = str(e)[:500]
        db.commit()
        raise
    cred.last_verified_at = datetime.now()  # 与 enum_cache_service 同一应用时钟
    cred.last_verify_error = None
    db.commit()
    # 库列表刻意**不落库**:它和 last_verified_at 一样只对这一刻成立,而库侧权限随时会变
    # (见模块 docstring)。留在库里会变成一条越来越旧的断言,不如每次点按钮时现算。
    return databases


def delete(db: Session, team_id: int, datasource_id: int) -> TeamDataSourceCredential | None:
    """删除某团队在某数据源上的凭证。返回被删的行(供审计取名),不存在则返回 None。"""
    cred = get(db, team_id, datasource_id)
    if cred is None:
        return None
    db.delete(cred)
    db.commit()
    return cred


def delete_for_datasource(db: Session, datasource_id: int) -> int:
    """数据源被删除时清掉其下所有团队凭证(不提交,跟随调用方事务)。返回删除行数。"""
    result = db.execute(
        sa_delete(TeamDataSourceCredential).where(
            TeamDataSourceCredential.datasource_id == datasource_id
        )
    )
    return result.rowcount or 0


def delete_for_team(db: Session, team_id: int) -> int:
    """团队被删除时清掉它的所有凭证(不提交,跟随调用方事务)。返回删除行数。"""
    result = db.execute(
        sa_delete(TeamDataSourceCredential).where(TeamDataSourceCredential.team_id == team_id)
    )
    return result.rowcount or 0


# ---------------------------------------------------------------- 查询视图


def _configured_pairs(db: Session, pairs: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """给定 (team_id, datasource_id) 组合中**已登记账号**的那些(不看测通与否)。

    只取两列(不取实体):实体加载会把 password 这个 EncryptedText 列一并解密,
    而这里一个密码都不需要 —— 任务列表是落地页,每次渲染白解密几百行很不值。
    用两个 IN 而不是 tuple_ IN:后者在 SQLite 上支持不稳,多取的行在内存里过滤掉即可。
    """
    if not pairs:
        return set()
    team_ids = {t for t, _ in pairs}
    ds_ids = {d for _, d in pairs}
    rows = db.execute(
        select(TeamDataSourceCredential.team_id, TeamDataSourceCredential.datasource_id).where(
            TeamDataSourceCredential.team_id.in_(team_ids),
            TeamDataSourceCredential.datasource_id.in_(ds_ids),
        )
    )
    return {pair for pair in rows if pair in pairs}


def ready_template_ids(db: Session, templates) -> set[int]:
    """给定任务里「所属团队已登记该数据源的取数账号」的那些 id。

    收已加载的任务对象而不是 id:调用方(任务列表)手上本来就有整行,
    只收 id 会让这里为了拿回 team_id / datasource_id 再查一遍库。
    """
    configured = _configured_pairs(
        db, {(t.team_id, t.datasource_id) for t in templates if t.team_id is not None}
    )
    return {
        t.id
        for t in templates
        if t.team_id is not None and (t.team_id, t.datasource_id) in configured
    }


def _cell(datasource_id: int, cred, *, reveal_username: bool) -> dict:
    """一格状态:配没配 / 测通没 / 什么时候测通的。**永不含 password**。

    cred 可以是 ORM 行,也可以是 overview 的列级 Row —— 故只读两者都有的列。
    reveal_username=False 时把用户名抹掉:库用户名是半机密(见模块 docstring),
    普通团队成员只该看到三态。判定由路由层做一次,本层不自己猜。
    """
    return {
        "datasource_id": datasource_id,
        "configured": cred is not None,
        "username": getattr(cred, "username", None) if reveal_username else None,
        "verified": _is_verified(cred),
        "last_verified_at": getattr(cred, "last_verified_at", None),
        # 失败原因同样受 reveal_username 管:引擎的鉴权报错长这样
        # `Access denied for user 'team_acct'@...`,里面就带着库账号名。只挡 username 而放它
        # 过去,等于从后门把半机密漏给普通成员(见模块 docstring)。
        "last_verify_error": getattr(cred, "last_verify_error", None) if reveal_username else None,
        # 这套账号进哪个库(空 = 用数据源的 Database)。不属半机密:它是库名,不是凭证
        "entry_database": getattr(cred, "entry_database", None),
        "updated_by": getattr(cred, "updated_by", None),
        "updated_at": getattr(cred, "updated_at", None),
    }


def status_row(ds: DataSource, cred, *, reveal_username: bool) -> dict:
    """一个「数据源 × 某团队」的状态行,给团队取数账号页用。**永不含 password**。"""
    return {
        "datasource_name": ds.name,
        "engine": ds.engine,
        "host": ds.host,
        "port": ds.port,
        "database": ds.database,
        **_cell(ds.id, cred, reveal_username=reveal_username),
    }


def _with_updater_names(db: Session, rows: list[dict]) -> list[dict]:
    """给状态行补「上次是谁改的」姓名。一次批量查,不做 N+1。

    团队管理员读不到审计日志,所以这条治理事实必须由业务接口给出(见模型 updated_by)。
    """
    uids = {r["updated_by"] for r in rows if r.get("updated_by")}
    names = (
        dict(db.execute(select(User.id, User.name).where(User.id.in_(uids))).all())
        if uids
        else {}
    )
    for r in rows:
        # 恒赋值:否则「没人改过」的行会少一个键,同一个函数就有了两种输出形状
        r["updated_by_name"] = names.get(r.get("updated_by"))
    return rows


def list_for_team(db: Session, team_id: int, *, reveal_username: bool) -> list[dict]:
    """某团队在每个数据源上的取数账号状态。**列出全部数据源**(含未配置的),
    这样「还差哪个源没配」一眼可见 —— 这正是配置页要回答的问题。"""
    # 只取状态需要的几列(不取实体):实体加载会把 password 这个 EncryptedText 列一并解密,
    # 而这里一个密码都不用 —— 与本模块其它查询视图同一口径。_cell 对列级 Row 同样成立。
    creds = {
        c.datasource_id: c
        for c in db.execute(
            select(
                TeamDataSourceCredential.datasource_id,
                TeamDataSourceCredential.username,
                TeamDataSourceCredential.last_verified_at,
                TeamDataSourceCredential.last_verify_error,
                TeamDataSourceCredential.updated_by,
                TeamDataSourceCredential.updated_at,
            ).where(TeamDataSourceCredential.team_id == team_id)
        )
    }
    rows = [
        {
            "team_id": team_id,
            **status_row(ds, creds.get(ds.id), reveal_username=reveal_username),
        }
        for ds in db.scalars(select(DataSource).order_by(DataSource.id))
    ]
    return _with_updater_names(db, rows)


def my_teams_status(db: Session, user: User) -> list[dict]:
    """我所在各团队 × 各数据源的就绪态。给任务编辑器用:选完团队与数据源就能当场提示
    「已登记 / 未登记」,不必等到点上线才发现;未测通只作为一句建议性提示。

    **永不含 username** —— 编辑器只需要知道「就绪没」,而它面向全体团队成员。
    """
    teams = team_service.all_teams(db) if is_platform_admin(user) else team_service.teams_of(db, user)
    if not teams:
        return []
    datasources = list(db.scalars(select(DataSource).order_by(DataSource.id)))
    creds = {
        (c.team_id, c.datasource_id): c
        for c in db.execute(
            select(
                TeamDataSourceCredential.team_id,
                TeamDataSourceCredential.datasource_id,
                TeamDataSourceCredential.last_verified_at,
                TeamDataSourceCredential.last_verify_error,
            ).where(TeamDataSourceCredential.team_id.in_([t.id for t in teams]))
        )
    }
    return [
        {
            "team_id": t.id,
            "team_name": t.name,
            **_cell(ds.id, creds.get((t.id, ds.id)), reveal_username=False),
            "datasource_name": ds.name,
            "engine": ds.engine,
        }
        for t in teams
        for ds in datasources
    ]


def overview(db: Session) -> dict:
    """平台管理员视角:团队 × 数据源 的覆盖矩阵 + 此刻缺账号的已上线任务。

    **常态健康看板**。`not_ready_templates` 只收「压根没有账号」的两种情形:
      ① 任务没有所属团队;② 所属团队在该数据源上的账号被删/从未登记。
    **不再收「未测通」** —— 测试连接是非必选项(见模块 docstring),未测通的账号照样能跑,
    把它列进「跑不动」只会制造一份永远清不完、也不该清的清单。测通与否仍在覆盖矩阵里
    以弱提醒呈现,供管理员催团队自查。

    全部走列级批量查询后在内存里配对,不做 N+1,也不加载任何密码。
    """
    datasources = list(db.scalars(select(DataSource).order_by(DataSource.id)))
    teams = team_service.all_teams(db)
    # 只取状态需要的几列:实体加载会连带解密每个团队的密码,而这里一个都不用
    creds = {
        (c.team_id, c.datasource_id): c
        for c in db.execute(
            select(
                TeamDataSourceCredential.team_id,
                TeamDataSourceCredential.datasource_id,
                TeamDataSourceCredential.username,
                TeamDataSourceCredential.last_verified_at,
                TeamDataSourceCredential.last_verify_error,
            )
        )
    }

    # 成员一次查完:逐队查 member_count + members_of 会是 2N 次查询,而这一页是平台级看板
    members = team_service.members_by_team(db, [t.id for t in teams])
    team_rows = [
        {
            "team_id": t.id,
            "team_name": t.name,
            "member_count": len(members[t.id]),
            "admins": [m for m in members[t.id] if m["is_team_admin"]],
            # 平台管理员可见用户名(治理矩阵要答「哪个库用的哪个账号」)
            "credentials": [
                _cell(ds.id, creds.get((t.id, ds.id)), reveal_username=True)
                for ds in datasources
            ],
        }
        for t in teams
    ]

    # 此刻跑不动的已上线任务:所属团队在该任务数据源上压根没有凭证行。
    # 列级 select + join 取团队/作者名:SqlTemplate 实体会把 description/tags 与三个 joined
    # 关系一起拉来,而这里只要几个字段。
    ds_names = {ds.id: ds.name for ds in datasources}
    team_names = {t.id: t.name for t in teams}
    not_ready = [
        {
            "template_id": tid,
            "template_name": name,
            "team_id": team_id,
            "team_name": team_names.get(team_id),
            "author_id": author_id,
            "author_name": author_name,
            "datasource_id": ds_id,
            "datasource_name": ds_names.get(ds_id),
            "reason": "无所属团队" if team_id is None else "未配置",
        }
        for tid, name, team_id, author_id, author_name, ds_id in db.execute(
            select(
                SqlTemplate.id,
                SqlTemplate.name,
                SqlTemplate.team_id,
                SqlTemplate.author_id,
                User.name,
                SqlTemplate.datasource_id,
            )
            .join(User, User.id == SqlTemplate.author_id)
            .where(SqlTemplate.status == STATUS_PUBLISHED)
            .order_by(SqlTemplate.id)
        )
        if team_id is None or (team_id, ds_id) not in creds
    ]

    return {
        "datasources": [
            {"id": ds.id, "name": ds.name, "engine": ds.engine} for ds in datasources
        ],
        "teams": team_rows,
        "not_ready_templates": not_ready,
    }


def _is_verified(cred) -> bool:
    """「最近一次测试连接通过过吗」,对 ORM 行与列级 Row 都成立(两者都有 last_verified_at)。

    这是纯展示口径(三态标签、覆盖矩阵的弱提醒),**不参与任何拦截** —— 拦截只看有没有账号。
    直接借模型上那条 property 的实现(fget),不在这里另抄一遍:本模块的查询视图大多拿的是
    列级 Row(为了不解密 password),Row 上没有属性描述符,但 fget 只读 last_verified_at,
    对 Row 同样成立。
    """
    return cred is not None and TeamDataSourceCredential.verified.fget(cred)
