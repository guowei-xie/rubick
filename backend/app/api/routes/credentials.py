"""团队取数账号:团队管理员维护 + 平台管理员总览。

权限边界(需求已定):
- **团队管理员**可增改删测本团队的账号 —— 账号归团队,维护责任也归团队管理员;
- **团队普通成员**只看得到状态(配没配 / 有没有测通 / 上次谁改的),**看不到库用户名**;
- **平台管理员**不受团队约束:可看全平台覆盖矩阵(含用户名)与「此刻跑不动的已上线任务」,
  也可代为配置与回收 —— 但**拿不到密码**。

密码只写不读:所有 Out 模型都不含 password(见 schemas/credential.py)。
库用户名是半机密(Hive auth=NONE 下它就是完整凭证),故按 reveal_username 分级返回。

**团队维度的守卫写在函数体内而不是 Depends**:① FastAPI 的依赖拿不到路径参数,做成依赖
工厂会把签名弄脏;② 本仓库的测试直接调用路由函数,守卫在体内才测得到 403。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin, require_task_author
from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.models.audit import (
    ACTION_CREDENTIAL_DELETE,
    ACTION_CREDENTIAL_UPSERT,
    ACTION_CREDENTIAL_VERIFY,
    RESOURCE_CREDENTIAL,
)
from app.models.datasource import DataSource
from app.models.team import Team
from app.models.user import User
from app.schemas.credential import (
    CredentialIn,
    CredentialOverviewOut,
    CredentialVerifyOut,
    TeamCredentialStatusOut,
)
from app.services import audit_service, credential_service, permission_service, team_service

router = APIRouter(prefix="/credentials", tags=["credentials"])


def _audit(
    db: Session, user: User, ip: str | None, action: str, team: Team, ds_id: int, detail: dict
) -> None:
    """凭证类审计的固定部分集中一处。

    资源记的是**数据源**(id + 名称快照):治理时要按库筛「哪个库上有哪些账号」,而
    resource_id 只有一个格子;「哪个团队」是第二个维度,放 detail。凭证行 id 对人毫无意义。
    detail 只放非机密的增量事实;真有人误传密码,audit_service.log 内部的脱敏兜底
    也会拦下(见 tests/test_audit_coverage.py)。
    """
    ds = db.get(DataSource, ds_id)
    audit_service.log(
        db, user=user, action=action, resource_type=RESOURCE_CREDENTIAL,
        resource_id=ds_id, resource_name=ds.name if ds else None,
        detail={
            "team_id": team.id,
            "team_name": team.name,
            # 这次是团队自己在维护,还是平台管理员干预?治理上要答得出来
            "by_platform_admin": not team_service.is_team_admin(db, user, team.id),
            **detail,
        },
        ip=ip,
    )


def _reveal(db: Session, user: User, team_id: int) -> bool:
    """该不该看到库用户名与失败原因(半机密,见 schemas/credential.py)。

    口径 = 「能治理这个团队的人」,直接问 team_service —— 平台管理员的豁免只在那里表述一次。
    """
    return team_service.can_admin_team(db, user, team_id)


# ---------------------------------------------------------------- 编辑器用


@router.get("/my-teams", response_model=list[TeamCredentialStatusOut])
def my_teams_credentials(
    db: Session = Depends(get_db), user: User = Depends(require_task_author)
):
    """我所在各团队 × 各数据源的就绪态。任务编辑器一次调用即可当场提示「选了这个团队 +
    这个数据源,账号就绪没」,不必等到点上线才发现。**永不含库用户名**。"""
    return credential_service.my_teams_status(db, user)


# ---------------------------------------------------------------- 团队维护


@router.get("/teams/{team_id}", response_model=list[TeamCredentialStatusOut])
def list_team_credentials(
    team_id: int, db: Session = Depends(get_db), user: User = Depends(require_task_author)
):
    """本团队在每个数据源上的账号状态(含尚未配置的,便于一眼看出还差哪个源)。

    团队管理员与平台管理员能看到库用户名,普通成员只看三态。
    """
    team_service.require_member(db, user, team_id)
    return credential_service.list_for_team(
        db, team_id, reveal_username=_reveal(db, user, team_id)
    )


@router.put("/teams/{team_id}/{ds_id}", response_model=TeamCredentialStatusOut)
def upsert_team_credential(
    team_id: int,
    ds_id: int,
    data: CredentialIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    """登记/修改本团队在该数据源上的取数账号。password 留空表示保留原密码,
    entry_database 留空表示用数据源配的 Database。

    改动后 verified 会被重置为 false —— 那条测通记录只对被换掉的那套凭证成立。
    **这不影响任务能不能跑**:测试连接是非必选项(见 services/credential_service),
    账号登记好即可上线与取数。
    """
    team = team_service.require_team_admin(db, user, team_id)
    cred, password_changed = credential_service.upsert(
        db, team_id=team.id, datasource_id=ds_id,
        username=data.username, password=data.password,
        entry_database=data.entry_database, updated_by=user.id,
    )
    _audit(
        db, user, ip, ACTION_CREDENTIAL_UPSERT, team, ds_id,
        {
            "db_username": cred.username, "password_changed": password_changed,
            # 入口库决定这套账号进哪个库,改它等于改取数行为 —— 治理上要能查
            "entry_database": cred.entry_database,
        },
    )
    return _one(db, team, ds_id, cred)


@router.post("/teams/{team_id}/{ds_id}/test", response_model=CredentialVerifyOut)
def verify_team_credential(  # 刻意不叫 test_*:那样会被 pytest 当成测试函数收集
    team_id: int,
    ds_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    """用本团队的账号真连一次目标库。**自愿的自检**:不测也能上线、也能取数,
    这一步只是让团队管理员当场知道账号填对没。

    审计:成功失败都留痕 —— 「一直测不通」本身就是要能查的治理事实。
    """
    team = team_service.require_team_admin(db, user, team_id)
    cred = credential_service.get(db, team.id, ds_id)
    if cred is None:
        raise NotFoundError("本团队尚未配置该数据源的取数账号")
    databases: list[str] = []
    try:
        databases = credential_service.verify(db, cred)
    finally:
        # verify 成功/失败都会先把状态落库,所以 ok 直接读 cred 即可,不必靠控制流分叉
        _audit(
            db, user, ip, ACTION_CREDENTIAL_VERIFY, team, ds_id,
            {
                "db_username": cred.username,
                "ok": cred.verified,
                **({} if cred.verified else {"error": (cred.last_verify_error or "")[:200]}),
                # 记「这个账号能进几个库」这个数,不记库名清单:清单可能上百条,
                # 会把审计 detail 撑成一堆噪音,而治理要答的是「测通时它有没有数据权限」。
                # 恒记(哪怕是 0):缺了这个键就分不清「一个库都进不去」和「压根没查出来」。
                "visible_databases": len(databases),
            },
        )
    # 库列表 = 这个账号能取到哪些库的数据(与数据源配的默认库无关),前端据此展示给管理员
    return {**_one(db, team, ds_id, cred), "databases": databases}


@router.delete("/teams/{team_id}/{ds_id}")
def delete_team_credential(
    team_id: int,
    ds_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_task_author),
    ip: str | None = Depends(client_ip),
):
    """删除本团队在该数据源上的账号(权限调整 / 回收)。平台管理员亦可代为回收。"""
    team = team_service.require_team_admin(db, user, team_id)
    cred = credential_service.delete(db, team.id, ds_id)
    if cred is None:
        # 什么都没发生就不留审计,否则日志里会有一条误导性的「删除」
        return {"ok": True, "deleted": False}
    _audit(
        db, user, ip, ACTION_CREDENTIAL_DELETE, team, ds_id, {"db_username": cred.username}
    )
    return {"ok": True, "deleted": True}


def _one(db: Session, team: Team, ds_id: int, cred) -> dict:
    """单行响应:与列表页同一个 status_row 口径,免得两处字段漂移。

    reveal_username 恒为 True:三个调用点都在 require_team_admin 之后,能走到这里的人
    按定义就看得见 —— 再问一次 _reveal 只是白查一次库。
    """
    ds = db.get(DataSource, ds_id)
    return {
        "team_id": team.id,
        "team_name": team.name,
        **credential_service.status_row(ds, cred, reveal_username=True),
    }


# ---------------------------------------------------------------- 平台管理员


@router.get("/overview", response_model=CredentialOverviewOut)
def credentials_overview(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """团队 × 数据源覆盖矩阵 + 此刻跑不动的已上线任务。

    **常态健康看板**(不再是「切开关前的体检表」——开关已取消):
    `not_ready_templates` 非空即意味着那些已上线任务此刻就跑不动,得让对应团队去配账号。
    """
    return credential_service.overview(db)
