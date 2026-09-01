"""[MOD-注释增强-20260901]
Pydantic 校验模型（Schemas / DTOs —— Data Transfer Objects）。

【为什么要单独抽 schemas 这一层？】
把"接口的入参/出参格式"和"数据库 ORM 模型"严格分开，好处有三：

1. **【请求校验】（守门员）**：
   前端传进来的 JSON 先经 Pydantic 校验——字段不够、类型不对、长度超了、
   邮箱格式错……统统在【进入业务代码之前】就被拦下来返回 422，
   业务代码里完全不用写"if username is None: ..."这种恶心的判断。

2. **【响应序列化】（白名单控制）**：
   比如用户表 User 有 password_hash 字段，这玩意儿绝对不能返回给前端！
   通过 response_model=UserOut 声明返回格式，Pydantic 会只挑白名单里的字段输出，
   哪怕你不小心把 User 对象直接 return 了，密码哈希也不会泄露。

3. **【自动生成 OpenAPI 文档】（/docs 页面好看又准）**：
   Pydantic 的 Field(example=...) / EmailStr / min_length 这些元信息，
   FastAPI 会自动收集并生成 Swagger UI 的请求/响应示例、字段说明，
   前端同学看文档就能对接，不用你拉群扯皮。

【分类】本文件里的所有 Model 按业务分成 4 组：
- 认证类：注册 / 登录 / 返回用户信息 / Token
- 会话类：创建 / 重命名 / 返回会话信息
- 消息类：发消息 / 返回消息
- 文件类：上传成功返回
"""

from datetime import datetime

# [MOD-注释增强-20260901] Pydantic 三件套：
#   - BaseModel：所有校验模型的基类（继承它就获得"校验 + JSON 序列化"的能力）
#   - ConfigDict：配置 Pydantic 行为（例如 from_attributes=True 让它能从 ORM 对象转成 Pydantic）
#   - EmailStr：专门的邮箱字段类型，会真正校验邮箱格式，不是简单正则哦
#   - Field：给单个字段加"校验规则 + 示例值 + 描述"，比单独写 min_length= 清晰
from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ========================================================================
# [MOD-注释增强-20260901] 【第 1 组：认证相关 Schemas】
# ========================================================================

class UserCreate(BaseModel):
    """[MOD-注释增强-20260901]
    【请求体】注册新用户时前端要传的 JSON 结构。

    校验规则（Pydantic 自动检查）：
    - username：3~50 个字符，示例 "alice"
    - email：必须是合法邮箱格式（EmailStr 自动校验）
    - password：6~128 个字符，示例 "secret123"
    """
    username: str = Field(min_length=3, max_length=50, examples=["alice"])
    email: EmailStr
    password: str = Field(min_length=6, max_length=128, examples=["secret123"])


class UserOut(BaseModel):
    """[MOD-注释增强-20260901]
    【响应体】返回给前端的用户信息【白名单】。

    关键！注意这里没有 password_hash 字段——哪怕后端 User ORM 对象里有这个字段，
    Pydantic 在转的时候也会直接丢掉，不会泄露给前端。

    model_config = ConfigDict(from_attributes=True)
      → 意思是：【允许从 ORM 对象直接构造】。
        路由里只要 return user（User ORM 对象），FastAPI 会自动调 UserOut.model_validate(user)，
        把 ORM 的属性按名字抄到 Pydantic 对象里，再转 JSON。
        （没有这一行的话，Pydantic 默认只认 dict，传 ORM 对象会直接报错。）
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    created_at: datetime


class LoginRequest(BaseModel):
    """[MOD-注释增强-20260901]
    【请求体】登录时前端要传的 JSON。

    注意是 username_or_email：支持用用户名登录，也支持用邮箱登录，
    业务代码里用 or_(User.username == x, User.email == x) 查就行。
    """
    username_or_email: str
    password: str


class TokenResponse(BaseModel):
    """[MOD-注释增强-20260901]
    【响应体】注册/登录成功后返回的结构。

    标准 OAuth2 Bearer Token 格式：
    - access_token：JWT 字符串（前端要存下来，后续请求放 Authorization 头里）
    - token_type：固定 "bearer"（OAuth2 规范要求，有些前端库会检查这个字段）
    - user：用户信息（前端拿到后可以直接显示头像/用户名，不用再调一次 /me）
    """
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ========================================================================
# [MOD-注释增强-20260901] 【第 2 组：会话相关 Schemas】
# ========================================================================

class SessionCreate(BaseModel):
    """[MOD-注释增强-20260901]
    【请求体】创建会话时前端要传的 JSON。
    - title 是可选的（默认"新会话"）：不传的话，用户发第一条消息时后端会自动生成标题
    - 最短 1 字符（防止空字符串），最长 100（防止标题太长撑爆 UI）
    """
    title: str = Field(default="新会话", min_length=1, max_length=100)


class SessionUpdate(BaseModel):
    """[MOD-注释增强-20260901]
    【请求体】重命名会话（PATCH）时前端要传的 JSON。
    只有 title 一个字段（没有默认值，必须传）。
    """
    title: str = Field(min_length=1, max_length=100)


class SessionOut(BaseModel):
    """[MOD-注释增强-20260901]
    【响应体】会话列表/详情返回的结构。

    相比 ORM 多了一个 message_count 字段（不是数据库里存的，
    是 sessions 路由里用子查询实时统计出来的）。
    因为它不是 ORM 的属性，所以给了默认值 0（from_attributes=True 时找不到也不会报错）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    message_count: int = 0


# ========================================================================
# [MOD-注释增强-20260901] 【第 3 组：消息相关 Schemas】
# ========================================================================

class MessageCreate(BaseModel):
    """[MOD-注释增强-20260901]
    【请求体】发送消息时前端要传的 JSON。
    - content：用户说的话，最短 1（防止发空消息），最长 4000（防止一句话一篇论文把 LLM 打爆）
    - examples 里的内容会显示在 /docs 的示例框里，前端同学可以直接点"Try it out"
    """
    content: str = Field(min_length=1, max_length=4000, examples=["你好，介绍一下 FastAPI"])


class MessageOut(BaseModel):
    """[MOD-注释增强-20260901]
    【响应体】返回单条消息（非流式回复直接返回这个，历史消息列表也返回 list[MessageOut]）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str  # user / assistant
    content: str
    created_at: datetime


# ========================================================================
# [MOD-注释增强-20260901] 【第 4 组：文件上传相关 Schemas】
# ========================================================================

class UploadOut(BaseModel):
    """[MOD-注释增强-20260901]
    【响应体】文件上传成功后返回给前端的信息。

    前端拿到后应该：
    - url 存起来（下次渲染图片/下载就用这个 URL）
    - size / content_type 显示给用户看
    """
    filename: str      # 用户上传时的原始文件名（展示用）
    size: int          # 文件大小（字节）
    content_type: str  # MIME 类型，例如 text/plain / image/png
    url: str           # 访问 URL，例如 /static/xxxxxx.txt（前端直接用这个地址渲染/下载）
