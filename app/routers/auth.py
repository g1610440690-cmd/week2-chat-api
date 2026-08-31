"""认证路由：注册 / 登录。

安全要点（学习重点）：
1. 密码绝不明文存储 —— 存 PBKDF2 加盐哈希（Python 标准库实现，零依赖）
2. 登录成功发 JWT（JSON Web Token）：服务端不存会话，token 自带过期时间
3. 校验用 hmac.compare_digest 常数时间比较，防时序攻击
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

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

router = APIRouter(prefix="/auth", tags=["认证"])

# PBKDF2 迭代次数：越大越难被暴力破解（同时越慢）
PBKDF2_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """生成带随机盐的 PBKDF2-SHA256 哈希。

    存储格式：pbkdf2_sha256$迭代次数$盐$哈希
    盐是随机的，所以同一个密码每次哈希结果都不同。
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。用 compare_digest 做常数时间比较，防止时序攻击。"""
    try:
        _algo, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, AttributeError):
        return False


def create_token(user: User) -> str:
    """签发 JWT：payload 里带用户 id 和过期时间，用 JWT_SECRET 签名。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,  # subject：用户标识
        "username": user.username,
        "iat": now,  # 签发时间
        "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),  # 过期时间
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    summary="注册（注册即登录，直接返回 token）",
)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    # 用户名或邮箱已存在 -> 409
    exists = await db.scalar(
        select(User).where(
            or_(User.username == body.username, User.email == body.email)
        )
    )
    if exists:
        raise ConflictError("用户名或邮箱已被注册")

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),  # 只存哈希！
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return TokenResponse(access_token=create_token(user), user=user)


@router.post("/login", response_model=TokenResponse, summary="登录")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    # 支持用户名或邮箱登录
    user = await db.scalar(
        select(User).where(
            or_(
                User.username == body.username_or_email,
                User.email == body.username_or_email,
            )
        )
    )
    if user is None or not verify_password(body.password, user.password_hash):
        # 无论"用户不存在"还是"密码错误"都返回同样的错误，避免暴露哪个用户名存在
        raise InvalidCredentialsError()
    return TokenResponse(access_token=create_token(user), user=user)


@router.get("/me", response_model=UserOut, summary="查看当前登录用户")
async def me(user: User = Depends(get_current_user)):
    return user
