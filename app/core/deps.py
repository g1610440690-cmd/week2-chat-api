"""依赖注入：认证相关的通用依赖。

FastAPI 的 Depends 会在调用路由函数前自动执行依赖函数，
把结果作为参数传进来 —— 这就是"依赖注入"（DI）。
"""
import logging

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..exceptions import AuthRequiredError
from ..models import User

logger = logging.getLogger("app.auth")

# auto_error=False：拿不到 Authorization 头时返回 None 而不是直接 401，
# 方便我们在下面统一抛 AuthRequiredError（错误格式统一）
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析 Bearer Token 并返回当前用户。

    用法：在路由里写 user: User = Depends(get_current_user)，
    未登录的请求会自动 401，登录用户直接拿到 User 对象。
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthRequiredError()

    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise AuthRequiredError("登录已过期，请重新登录")
    except jwt.PyJWTError:
        # 签名错误 / 格式错误 / 被篡改 —— 统一按未登录处理
        raise AuthRequiredError()

    user_id = payload.get("sub")
    if not user_id:
        raise AuthRequiredError()

    user = await db.get(User, user_id)
    if user is None:
        # Token 有效但用户已删除（例如被管理员清理）
        raise AuthRequiredError("账号不存在或已被删除")
    return user
