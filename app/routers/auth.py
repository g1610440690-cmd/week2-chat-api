"""[MOD-注释增强-20260901]
认证路由模块：注册 / 登录 / 查看当前用户 三个接口。

【安全要点（学习重点，面试会问）】

1. 【密码绝对不明文存储】—— PBKDF2-SHA256 加盐哈希
   - 同一个密码每次哈希结果都不同（因为盐是随机的），彩虹表没用；
   - 10 万次迭代（PBKDF2_ITERATIONS=100_000），暴力破解成本飙升；
   - Python 标准库 hashlib 实现，零第三方依赖。

2. 【发 JWT 而不是存 Session】
   - 传统 session：服务端存用户状态，有状态；
   - JWT：服务端不存任何东西，用户 id、过期时间都签在 token 里自带，
          天然支持分布式部署（多台机器不用共享 session）。

3. 【校验密码用 hmac.compare_digest（常数时间比较）】
   - 普通 Python == 比较字符串时：前几位不同就立刻返回 False，
     攻击者可以根据"比较耗时的微小差异"猜出正确密码的前几位（时序攻击）；
   - compare_digest 无论前几位对不对，都会把所有字节比较完再返回，
     耗时恒定，防时序攻击。

4. 【用户不存在 vs 密码错误 → 返回同一个错误】
   - 不要区分"用户名不存在"和"密码错误"，统一返回"用户名或密码错误"；
   - 否则攻击者可以用"提示不同"来【枚举哪些用户名已经注册】。
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

# [MOD-注释增强-20260901] PyJWT：JSON Web Token 的签发和解析
import jwt
from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..core.deps import get_current_user
from ..database import get_db
from ..exceptions import ConflictError, InvalidCredentialsError
from ..models import User
from ..schemas import LoginRequest, TokenResponse, UserCreate, UserOut

# [MOD-注释增强-20260901] 路由前缀 /auth，在 /docs 里归类到"认证"标签下
router = APIRouter(prefix="/auth", tags=["认证"])

# [MOD-注释增强-20260901]
# PBKDF2 迭代次数：迭代越多越安全，但也越慢。
# 100_000 次是 2024 年的最低推荐值（OWASP 推荐），
# 现代 CPU 跑一次大概几毫秒，用户登录基本无感，但暴力破解要哭。
PBKDF2_ITERATIONS = 100_000


# ========================================================================
# [MOD-注释增强-20260901] 密码哈希 + 校验（公共工具函数）
# ========================================================================

def hash_password(password: str) -> str:
    """[MOD-注释增强-20260901]
    生成【带随机盐】的 PBKDF2-SHA256 密码哈希。

    存储格式（四段用 $ 分隔）：
        pbkdf2_sha256${迭代次数}${盐}${哈希值的十六进制}
    示例：
        pbkdf2_sha256$100000$a1b2c3d4...$e5f6a7b8...

    为什么"同一个密码每次结果都不同"？
    → 因为盐是 secrets.token_hex(16) 每次随机生成的 32 字符随机串，
      哪怕两次都 hash_password("secret123")，结果完全不同，防彩虹表。
    """
    # [MOD-注释增强-20260901] 16 字节 = 32 个十六进制字符的随机盐
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    # [MOD-注释增强-20260901] 四合一存进一个字符串字段 password_hash 里，
    #                            校验时 split 拆出来按同样的算法算一遍再比较
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """[MOD-注释增强-20260901]
    校验密码：把用户输入的密码按同样的算法重算一遍，和数据库里的比较。

    ⚠️ 关键安全点：用 hmac.compare_digest 做比较，而不是普通的 ==
    → 常数时间比较，防止时序攻击。
    """
    try:
        # [MOD-注释增强-20260901] 从存储的字符串里拆出"算法/迭代次数/盐/期望哈希"四段
        _algo, iterations, salt, expected = stored.split("$")
        # [MOD-注释增强-20260901] 按完全相同的参数再算一遍哈希
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        # [MOD-注释增强-20260901] 常数时间比较（不管对不对，耗时都一样）
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        # [MOD-注释增强-20260901] stored 格式不对（比如被改过）→ 直接返回 False，
        #                            不要抛异常，不要泄露信息
        return False


def create_token(user: User) -> str:
    """[MOD-注释增强-20260901]
    签发 JWT（JSON Web Token）。

    JWT Payload 字段含义（标准字段，不是瞎编的）：
    - sub：subject → 用户唯一标识（= user.id）
    - username：自定义字段 → 方便调试日志里直接看出是谁（不用每次查数据库）
    - iat：issued at → 签发时间
    - exp：expiration → 过期时间（iat + JWT_EXPIRE_MINUTES）

    用 settings.JWT_SECRET 做 HMAC-SHA256 签名—— 服务端自己知道这个秘钥，
    别人改了 payload 里的任何一个字节，验签都会失败。
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


# ========================================================================
# [MOD-注释增强-20260901] 接口 1：注册 POST /auth/register
# ========================================================================

@router.post("/register",response_model=TokenResponse,status_code=201,summary="注册（注册即登录，直接返回 token）",)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    """[MOD-注释增强-20260901]
    注册接口：

    完整流程：
    ① Pydantic 已经帮我们校验了参数（用户名 3-50、密码 6-128、邮箱合法）
    ② 查重：用户名或邮箱任一个已存在 → 409 Conflict
    ③ 新建 User 对象：密码只存哈希（hash_password(body.password)），【绝对不要存明文！】
    ④ commit + refresh → 拿到数据库自动生成的 id / created_at
    ⑤ 直接签发 JWT 返回 → "注册即登录"，前端不用再调一次 /login
    """

    # [MOD-注释增强-20260901] 查重：用户名 or 邮箱，命中任意一个就算重复
    exists = await db.scalar(
        select(User).where(
            or_(User.username == body.username, User.email == body.email)
        )
    )
    if exists:
        raise ConflictError("用户名或邮箱已被注册")

    # [MOD-注释增强-20260901] 新建用户（只存密码哈希！）
    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # [MOD-注释增强-20260901] 签发 JWT + 返回用户信息（response_model=TokenResponse，
    #                            password_hash 字段被白名单过滤掉，绝对不会返回给前端）
    return TokenResponse(access_token=create_token(user), user=user)


# ========================================================================
# [MOD-注释增强-20260901] 接口 2：登录 POST /auth/login
# ========================================================================

@router.post("/login", response_model=TokenResponse, summary="登录")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """[MOD-注释增强-20260901]
    登录接口：支持用户名 OR 邮箱登录。

    流程：
    ① 按"用户名=xxx 或 邮箱=xxx"查用户；
    ② 用户不存在 或 密码校验失败 → 统一抛 InvalidCredentialsError（两者同一个提示，不暴露信息）；
    ③ 成功 → 签发 JWT 返回。
    """
    # [MOD-注释增强-20260901] 支持"用用户名登录"或"用邮箱登录"（同一个字段两种可能）
    user = await db.scalar(
        select(User).where(
            or_(
                User.username == body.username_or_email,
                User.email == body.username_or_email,
            )
        )
    )
    # [MOD-注释增强-20260901] 安全设计：用户不存在 或 密码错误 → 同一个错误提示，
    #                            不告诉攻击者"这个用户名到底存在不存在"
    if user is None or not verify_password(body.password, user.password_hash):
        raise InvalidCredentialsError()
    return TokenResponse(access_token=create_token(user), user=user)


# ========================================================================
# [MOD-注释增强-20260901] 接口 3：查看当前用户 GET /auth/me
# ========================================================================

@router.get("/me", response_model=UserOut, summary="查看当前登录用户")
async def me(user: User = Depends(get_current_user)):
    """[MOD-注释增强-20260901]
    查看当前登录的用户是谁。

    Depends(get_current_user) 已经帮我们做了：
    - 有没有 token？
    - token 过期没？
    - token 签名对不对？
    - 这个 user_id 对应的用户真的存在吗？
    全部通过才会把 User 对象赋值给 user 参数，我们直接 return 就行。
    response_model=UserOut 保证不返回 password_hash。
    """
    return user
