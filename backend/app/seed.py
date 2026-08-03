"""种子数据:建表 + 造一个可端到端跑通的演示环境。

用法:python -m app.seed

产出:
  - 3 个用户:admin(管理员)/ analyst(商分)/ viewer(业务用户)
  - 演示业务库 rubic_demo.orders(与平台元数据库同一 MySQL 实例,不同 database)
  - 一个 MySQL 数据源指向 rubic_demo
  - 一条已发布模板"按日期查订单",授权给 viewer 个人
之后即可用 viewer 登录 → 跑模板 → 下载 → 在审计里看到记录。
"""
from __future__ import annotations

import os

from sqlalchemy import select, text

from app.core.database import Base, SessionLocal, engine
import app.models  # noqa: F401
from app.models.datasource import DataSource
from app.models.template import SqlTemplate
from app.models.user import ROLE_ADMIN, ROLE_USER, User
from app.schemas.common import ParamDef
from app.schemas.template import TemplateCreateIn
from app.services import permission_service, template_service

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
    print("1) create metadata tables")
    Base.metadata.create_all(bind=engine)

    print("2) create demo business data")
    create_demo_business_data()

    db = SessionLocal()
    try:
        print("3) users")
        admin = upsert_user(db, "ou_admin", "管理员小A", ROLE_ADMIN)
        analyst = upsert_user(db, "ou_analyst", "管理员小B", ROLE_ADMIN)  # 原商分并入管理员
        viewer = upsert_user(db, "ou_viewer", "业务小C", ROLE_USER)

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
        print("  管理员 = ou_admin / ou_analyst(均为管理员)   业务用户 = ou_viewer")
    finally:
        db.close()


if __name__ == "__main__":
    main()
