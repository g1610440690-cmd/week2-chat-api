"""Pydantic 校验模型（Schemas）。

作用：
1. 请求校验：客户端传进来的 JSON 先经过这里，不合格直接返回 422
2. 响应序列化：response_model 保证返回给客户端的字段白名单
3. 自动生成 OpenAPI 文档（/docs 里的请求/响应示例）

Field 里的 min_length / max_length 就是"校验规则"，
EmailStr 会真正校验邮箱格式。
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- 认证 ----------
class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, examples=["alice"])
    email: EmailStr
    password: str = Field(min_length=6, max_length=128, examples=["secret123"])


class UserOut(BaseModel):
    """返回给客户端的用户信息（不含密码哈希！）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    created_at: datetime


class LoginRequest(BaseModel):
    username_or_email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- 会话 ----------
class SessionCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=100)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    message_count: int = 0


# ---------- 消息 ----------
class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000, examples=["你好，介绍一下 FastAPI"])


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str  # user / assistant
    content: str
    created_at: datetime


# ---------- 文件上传 ----------
class UploadOut(BaseModel):
    filename: str
    size: int
    content_type: str
    url: str
