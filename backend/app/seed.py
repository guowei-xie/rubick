"""种子数据:建表 + 造一个可端到端跑通的演示环境。

用法:python -m app.seed

产出:
  - 4 个用户:admin/analyst(管理员)、dev(开发者)、viewer(普通用户)
  - 一个演示团队「演示团队」:analyst 任团队管理员、dev 任成员
  - 演示业务库 rubic_demo.orders(与平台元数据库同一 MySQL 实例,不同 database)
  - 一个 MySQL 数据源指向 rubic_demo,并为演示团队登记同一套账号作团队取数账号
  - 一条已上线任务"按日期查订单"(归属演示团队),授权给 viewer 个人
之后即可用 viewer 登录 → 跑任务 → 下载 → 在审计里看到记录。
(mock 登录需要 config.ini 里 MOCK_AUTH = true,默认关闭)
"""
from __future__ import annotations

import os

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
import app.models  # noqa: F401
from app.models.datasource import DataSource
from app.models.team import Team
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_DEVELOPER, ROLE_USER, User
from app.schemas.common import ParamDef
from app.schemas.template import TemplateCreateIn
from app.services import credential_service, permission_service, team_service, template_service

DEMO_DB = "rubic_demo"
DEMO_HOST = os.getenv("DEMO_DS_HOST", "localhost")
DEMO_PORT = int(os.getenv("DEMO_DS_PORT", "3306"))
DEMO_USER = os.getenv("DEMO_DS_USER", "rubic")
DEMO_PASS = os.getenv("DEMO_DS_PASS", "rubic")


def create_demo_business_data() -> None:
    """在同一 MySQL 实例建一个演示业务库与订单表,填充样例数据。"""
    with engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {DEMO_DB} CHARACTER SET utf8mb4"))
        conn.execute(
            text(
                f"""CREATE TABLE IF NOT EXISTS {DEMO_DB}.orders (
                    order_id INT PRIMARY KEY,
                    customer VARCHAR(64),
                    amount DECIMAL(10,2),
                    created_date DATE
                )"""
            )
        )
        count = conn.execute(text(f"SELECT COUNT(*) FROM {DEMO_DB}.orders")).scalar()
        if not count:
            conn.execute(
                text(
                    f"""INSERT INTO {DEMO_DB}.orders (order_id, customer, amount, created_date) VALUES
                    (1,'张三',120.50,'2024-01-05'),
                    (2,'李四',88.00,'2024-02-11'),
                    (3,'王五',256.75,'2024-03-20'),
                    (4,'赵六',49.90,'2024-05-01'),
                    (5,'钱七',999.00,'2024-06-15')"""
                )
            )
        conn.commit()
    print(f"  demo business data ready: {DEMO_DB}.orders")


def upsert_user(db, open_id: str, name: str, role: str) -> User:
    user = db.scalar(select(User).where(User.feishu_open_id == open_id))
    if user is None:
        user = User(feishu_open_id=open_id, name=name, role=role)
        db.add(user)
    else:
        user.role = role
    db.commit()
    db.refresh(user)
    return user


def main() -> None:
    # 护栏:seed 造的是假数据 —— 4 个假账号、演示团队、演示数据源,还会在目标实例上
    # CREATE DATABASE rubic_demo。连着远端库跑一次就污染正式库(bitest 的假账号即由此
    # 而来,已由 app/cleanup_mock_users.py 清除),故只允许对本地库执行。
    if not settings.DATABASE_IS_LOCAL:
        raise SystemExit(
            f"拒绝执行:seed 只能对本地库跑,当前 DATABASE_URL 指向 {settings.database_display}。"
            "要造演示数据请先把 DATABASE_URL 换成本地库(sqlite 或 localhost 的 MySQL)。"
        )

    print("1) create metadata tables")
    Base.metadata.create_all(bind=engine)

    print("2) create demo business data")
    create_demo_business_data()

    db = SessionLocal()
    try:
        print("3) users")
        admin = upsert_user(db, "ou_admin", "管理员小A", ROLE_ADMIN)
        analyst = upsert_user(db, "ou_analyst", "管理员小B", ROLE_ADMIN)  # 原商分并入管理员
        dev = upsert_user(db, "ou_dev", "开发小D", ROLE_DEVELOPER)  # 开发者:只在团队内取值
        viewer = upsert_user(db, "ou_viewer", "普通小C", ROLE_USER)

        print("3.5) demo team")
        # 开发者必须先有团队才能建任务(见 permission_service.require_can_create_in_team)。
        # 团队成员只能是开发者,所以团队管理员也由开发者担任;平台管理员不受团队约束、
        # 无需入队(require_member / require_team_admin 里对他恒放行)。
        team = db.scalar(select(Team).where(Team.name == "演示团队"))
        if team is None:
            team = team_service.create_team(
                db, name="演示团队", description="seed 造的演示团队", created_by=admin.id
            )
        if not team_service.is_member(db, dev, team.id):
            team_service.add_member(db, team, dev.id, is_team_admin_flag=True, added_by=admin.id)

        print("4) demo datasource")
        ds = db.scalar(select(DataSource).where(DataSource.name == "demo-mysql"))
        if ds is None:
            ds = DataSource(
                name="demo-mysql",
                engine="mysql",
                host=DEMO_HOST,
                port=DEMO_PORT,
                database=DEMO_DB,
                username=DEMO_USER,
                password=DEMO_PASS,
                extra={},
            )
            db.add(ds)
            db.commit()
            db.refresh(ds)

        print("4.5) team credential (演示环境直接复用数据源账号,并标记为已测通)")
        # 真实环境里由团队管理员在团队页登记账号,「测试连接」是自愿的自检。
        # seed 顺手把测通时间也写上,免得演示环境的团队页上常挂着一条「未测通」的弱提醒。
        cred, _ = credential_service.upsert(
            db, team_id=team.id, datasource_id=ds.id,
            username=DEMO_USER, password=DEMO_PASS, updated_by=admin.id,
        )
        if not cred.verified:
            from datetime import datetime

            cred.last_verified_at = datetime.now()
            cred.last_verify_error = None
            db.commit()

        print("5) demo template (published)")
        existing = db.scalar(select(SqlTemplate).where(SqlTemplate.name == "按日期查订单"))
        if existing is None:
            tmpl = template_service.create_template(
                db,
                analyst,
                TemplateCreateIn(
                    name="按日期查订单",
                    description="查询指定起始日期之后的订单明细",
                    tags=["订单", "演示"],
                    team_id=team.id,
                    datasource_id=ds.id,
                    sql_text=(
                        "SELECT order_id, customer, amount, created_date "
                        "FROM orders WHERE created_date >= :start_date "
                        "ORDER BY created_date"
                    ),
                    params=[
                        ParamDef(
                            name="start_date",
                            kind="single",
                            label="起始日期",
                            description="格式 YYYY-MM-DD,例 2024-01-01",
                        )
                    ],
                ),
            )
            template_service.publish(db, tmpl, admin, note="演示发布")
            print(f"  published template id={tmpl.id}")

            print("6) grant viewer view/run/download")
            permission_service.grant(
                db,
                subject_type="user",
                subject_id=str(viewer.id),
                resource_type="template",
                resource_id=str(tmpl.id),
                actions=["view", "run", "download"],
                granted_by=admin.id,
            )

        print("\nSeed done. 登录 open_id:")
        print("  管理员 = ou_admin / ou_analyst   开发者 = ou_dev   普通用户 = ou_viewer")
    finally:
        db.close()


if __name__ == "__main__":
    main()
