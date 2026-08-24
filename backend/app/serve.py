"""按 config.ini 启动 uvicorn。

**存在的理由是「监听在哪」只该有一份真相。** 从前 systemd unit 里写死
`--host 127.0.0.1 --port 18091`,而 deploy.sh 的 nohup 路径读的是 config.ini 的
BACKEND_HOST / BACKEND_PORT —— 改了配置在线上不生效、而且**不报错**:服务照样起得来,
只是反代打不通,连 APP_BASE_URL 派生出的飞书回调地址也跟着算错。
现在两条启动路径都走这里,unit 与配置不可能再对不上。

用法:python -m app.serve
"""
from __future__ import annotations

import uvicorn

from app.core.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        # reload / workers 刻意不给:单端口单进程是本项目的部署模型(后端同时托管 SPA),
        # 真要多进程得先把 worker 与结果目录的假设一起想清楚。
    )


if __name__ == "__main__":
    main()
