"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import governance, resource

# 固定路径的贡献视图路由必须排在通用 /api/{module} 资源路由之前。
ROUTERS = [governance, resource]
