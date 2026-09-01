"""[MOD-注释增强-20260901]
FastAPI 依赖注入模块 —— 认证相关的通用依赖。

【什么是 FastAPI 的 Depends（依赖注入）？】
    简单说：你在路由函数参数里写 `user: User = Depends(get_current_user)`，
    FastAPI 在真正调用路由函数之前，会【先执行 get_current_user 这个函数】，
    把它的返回值（= 当前登录的 User 对象）自动赋给 user 参数。
    路由函数里就能直接用 user，不用每次都写"从 Authorization 头里取 token → 解密 → 查数据库"这一大坨。

【为什么这层要单独放 core/deps.py？】
    因为"取当前登录用户"这件事，4 个路由（auth/sessions/chat/upload）都要用。
    抽成一个公共依赖，大家统一用 Depends(get_current_user)，
    既避免重复代码，又保证"认证逻辑永远只有一份，改起来只改一个地方"。
"""

import logging

# [MOD-注释增强-20260901] PyJWT：JWT 解析（签发在 auth.py 的 create_token 里）
import jwt
from fastapi import Depends
# [MOD-注释增强-20260901] HTTPBearer：FastAPI 自带的 OAuth2 Bearer Token 解析器，
#                            会自动从 Authorization: Bearer xxx 头里把 xxx 抠出来。
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..exceptions import AuthRequiredError
from ..models import User

logger = logging.getLogger("app.auth")

# [MOD-注释增强-20260901]
# auto_error=False 很关键！
#   - True（默认）：拿不到 Authorization 头时，FastAPI 直接抛 HTTPException 401，
#                   用的是 FastAPI 默认的 {"detail": "Not authenticated"} 结构，
#                   跟我们的统一错误结构 {code, message, detail} 不一致。
#   - False：拿不到头时返回 None，让我们下面的代码自己判断抛 AuthRequiredError，
#            保证错误结构统一。
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """[MOD-注释增强-20260901]
    解析 Bearer Token 并返回【当前登录的用户对象】。

    这是整个项目最核心的认证依赖——所有"需要登录才能用"的接口，
    在路由参数里都要写 user: User = Depends(get_current_user)。

    完整验证流程（共 6 道关，任何一道不过就直接 401 踢走）：
    ① 有没有带 Authorization 头？头格式是不是 Bearer xxx？—— 没有就 401
    ② JWT 能不能正常解开？（签名对不对、格式对不对）—— 解开失败就 401
    ③ JWT 过期没有？（exp 字段是不是已经过了）—— 过期返回"登录已过期"的明确提示
    ④ payload 里有没有 sub 字段（= 用户 id）？—— 没有就 401
    ⑤ 根据用户 id 查数据库，用户真的存在吗？（有可能 token 是有效的，但用户已经被管理员删了）
    ⑥ 全部通过 → 返回 User 对象

    :return: 当前登录的 User ORM 对象
    :raises AuthRequiredError: 任何一道关卡没通过都会抛这个异常（code=40101）
    """

    # [MOD-注释增强-20260901] 关卡①：Authorization 头有没有？格式是不是 Bearer？
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthRequiredError()

    settings = get_settings()
    try:
        # [MOD-注释增强-20260901]
        # 关卡②+③：jwt.decode 一次性做签名校验 + 过期校验。
        #   - credentials.credentials：就是 Bearer 后面那串 JWT（xxx.yyy.zzz）
        #   - settings.JWT_SECRET：签名密钥（签发时用的同一个秘钥）
        #   - algorithms=[HS256]：【必须显式指定】，不写有安全风险（alg:none 攻击）。
        #  两种异常要区分：ExpiredSignatureError 要给用户明确的"过期"提示，
        #  其他所有 PyJWTError（签名错/格式错/被篡改）统一按"请先登录"处理，不给攻击者太多信息。
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        # [MOD-注释增强-20260901] JWT 的 exp 时间戳 < 当前时间 → 过期了
        raise AuthRequiredError("登录已过期，请重新登录")
    except jwt.PyJWTError:
        # [MOD-注释增强-20260901] 签名错误 / 格式错误 / token 被篡改 / 随便写了一个字符串
        raise AuthRequiredError()

    # [MOD-注释增强-20260901] 关卡④：从 JWT payload 里取 subject（我们签发时写的是用户 id）
    user_id = payload.get("sub")
    if not user_id:
        raise AuthRequiredError()

    # [MOD-注释增强-20260901] 关卡⑤：数据库里这个用户 id 真实存在吗？
    #                       （有可能管理员刚才把这个用户删了，但 JWT 还没过期）
    user = await db.get(User, user_id)
    if user is None:
        raise AuthRequiredError("账号不存在或已被删除")

    # [MOD-注释增强-20260901] 全部关卡通过 → 返回 User 对象，路由函数直接用
    return user
