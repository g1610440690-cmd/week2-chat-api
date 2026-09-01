"""[MOD-注释增强-20260901]
统一异常体系。

【为什么要自己定义一套异常，而不是直接 raise HTTPException？】

  传统做法的痛点：
  - 每个路由都写 raise HTTPException(status_code=404, detail="xxx")，
    错误结构完全靠人自觉，容易不一致（有人写 {detail}，有人写 {msg}，有人写 {error}）；
  - 前端对接苦不堪言：每个接口的错误格式都不同，写 10 套判断分支还容易漏。

  本项目统一方案：
  ① 定义一个 AppError 基类 + 一堆子类（NotFoundError / ConflictError 等）；
  ② 业务代码里直接 raise 子类，例如 raise NotFoundError("会话不存在")；
  ③ 在 main.py 里注册 4 个全局异常处理器，无论哪种异常，
     最终返回给前端的 JSON 都是同一个【固定结构】：

        {
            "code":    数字业务错误码（前端可以用它做 i18n 翻译 / 跳转逻辑，例如 40101 就跳登录页），
            "message": 人类可读的简短描述（直接给用户看），
            "detail":  附加细节（可选，例如 Pydantic 字段错误列表、具体限制是多少 MB）
        }

  好处：
  - 前端对接爽：所有错误一种格式解析；
  - 业务代码爽：一行 raise 就完事，不用自己包 JSON；
  - 错误码统一：40400 永远是"资源不存在"，40100 永远是"认证失败"，不会搞混。

【业务错误码规则（可参考，面试时也会问"你们怎么设计错误码"）】
  - 前 3 位 = HTTP 状态码，第 4/5 位 = 细分：
    例如 40400 = 404 大类（资源不存在）下的第 0 种细分，
    40001 / 40002 = 400 大类（参数错误）下的文件过大 / 文件类型不支持。
  - 好处：一眼看 code 前 3 位就知道是哪类错误，方便排查。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


# ========================================================================
# [MOD-注释增强-20260901] 第一部分：异常类定义（业务代码里直接 raise 这些）
# ========================================================================

class AppError(Exception):
    """[MOD-注释增强-20260901]
    【业务异常基类】—— 所有自定义异常都继承它。

    4 个字段正好对应上面的统一 JSON 结构：
    - code：        数字业务错误码（如 40400）
    - message：     面向用户的简短描述
    - status_code： HTTP 响应状态码（200/400/401/404/409...）
    - detail：      附加调试细节（可 None）

    使用示例（业务代码里）：
        if user is None:
            raise NotFoundError("用户不存在")
    """

    def __init__(self, code: int, message: str, status_code: int = 400, detail=None):
        super().__init__(message)           # 调 Exception 父类初始化，保证异常本身的 message 也对
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


class NotFoundError(AppError):
    """[MOD-注释增强-20260901]
    资源不存在 —— 404。

    ⚠️ 安全小技巧：
    查不到 OR 查到了但不属于当前用户，【都返回同一个 404】，
    不要区分"没有这个会话"和"这个会话不是你的"。
    否则恶意攻击者可以用"返回 403 还是 404"来枚举哪些会话 ID 是真实存在的（信息泄露）。
    """

    def __init__(self, message: str = "资源不存在", detail=None):
        super().__init__(code=40400, message=message, status_code=404, detail=detail)


class InvalidCredentialsError(AppError):
    """[MOD-注释增强-20260901]
    登录凭证无效（用户名或密码错误）—— 401。

    同样是【统一错误信息】的安全考量：
    用户名不存在 → 返回这个错误；
    密码错误     → 也返回同一个错误。
    攻击者无法用"用户名错误/密码错误"的不同提示来"枚举哪些用户名存在"。
    """

    def __init__(self, message: str = "用户名或密码错误"):
        super().__init__(code=40100, message=message, status_code=401)


class AuthRequiredError(AppError):
    """[MOD-注释增强-20260901]
    未登录 / Token 无效 / Token 过期 —— 401。
    和上面的区别：40100 是"账号密码错了"，40101 是"没带 token / token 坏了"。
    前端可以根据 code=40101 跳转到登录页。
    """

    def __init__(self, message: str = "请先登录"):
        super().__init__(code=40101, message=message, status_code=401)


class ConflictError(AppError):
    """[MOD-注释增强-20260901]
    资源冲突（例如用户名已被注册）—— 409。
    """

    def __init__(self, message: str = "资源已存在", detail=None):
        super().__init__(code=40900, message=message, status_code=409, detail=detail)


class RateLimitError(AppError):
    """[MOD-注释增强-20260901]
    限流 —— 429 Too Many Requests。
    detail 里可以带"还剩多少秒才能再请求"等信息给前端展示。
    """

    def __init__(self, message: str = "请求过于频繁，请稍后再试", detail=None):
        super().__init__(code=42900, message=message, status_code=429, detail=detail)


class FileTooLargeError(AppError):
    """[MOD-注释增强-20260901]
    文件过大 —— 400。
    """

    def __init__(self, message: str = "文件超过大小限制", detail=None):
        super().__init__(code=40001, message=message, status_code=400, detail=detail)


class UnsupportedFileTypeError(AppError):
    """[MOD-注释增强-20260901]
    文件类型不支持（扩展名不在白名单里）—— 400。
    """

    def __init__(self, message: str = "不支持的文件类型", detail=None):
        super().__init__(code=40002, message=message, status_code=400, detail=detail)


# ========================================================================
# [MOD-注释增强-20260901] 第二部分：全局异常处理器注册
#   register_exception_handlers(app) 在 main.py 里被调用一次即可。
#   之后整个应用任何地方抛出的异常，都会走到这里被"包装成统一 JSON"返回。
# ========================================================================

def register_exception_handlers(app: FastAPI) -> None:
    """[MOD-注释增强-20260901]
    注册 4 个全局异常处理器，把各种异常都转成【统一 JSON 结构】返回。

    按"捕获范围从小到大"排列：
    ① AppError 及其子类 —— 我们业务代码主动 raise 的，最优先匹配
    ② Starlette HTTPException —— FastAPI 内部抛出的（如 Depends 里抛的 HTTPException）
    ③ RequestValidationError —— Pydantic 参数校验失败
    ④ Exception 兜底 —— 任何没预料到的异常都走这里，保证不把堆栈泄露给用户
    """

    # -----------------------------------------------------------------------
    # [MOD-注释增强-20260901] 处理器 1：AppError（我们自定义的业务异常）
    #   最简单直接——按 4 个字段拼 JSON 返回就完事了
    # -----------------------------------------------------------------------
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    # -----------------------------------------------------------------------
    # [MOD-注释增强-20260901] 处理器 2：Starlette 的 HTTPException
    #   FastAPI 内部一些机制（比如 OAuth2 默认的 auto_error=True）会抛出这个异常，
    #   默认返回结构是 {"detail": "..."}，跟我们的统一结构不一致。
    #   这里覆盖掉，强制转成 {code, message, detail} 三件套，保证前端对接一致。
    #   另外 headers 要原样传回去（比如 401 的 WWW-Authenticate 头）。
    # -----------------------------------------------------------------------
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "detail": None},
            headers=exc.headers,
        )

    # -----------------------------------------------------------------------
    # [MOD-注释增强-20260901] 处理器 3：Pydantic 请求参数校验失败
    #   默认 Pydantic 返回的结构也是 {"detail": [...字段错误列表...]}，
    #   这里包一层，加上我们的 code=42200 和 message。
    #   jsonable_encoder(exc.errors()) 把 Pydantic 的错误对象转成可序列化的 JSON 结构，
    #   里面会包含"哪个字段错了 / 错在哪 / 期望什么类型"等详细信息，方便前端定位。
    # -----------------------------------------------------------------------
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": 42200,
                "message": "请求参数校验失败",
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    # -----------------------------------------------------------------------
    # [MOD-注释增强-20260901] 处理器 4：兜底 —— 所有未预料的异常（Exception）
    #   这是最后一道防线：任何没被上面 3 个捕获到的异常（比如空指针、索引越界、第三方库报错）
    #   都会走到这里。⚠️ 绝对不能把 traceback 堆栈返回给前端（信息泄露 + 难看），
    #   只返回"服务器内部错误"几个字 + code=50000；
    #   同时在服务端【用 logger.exception 记录完整堆栈】，方便开发排查。
    # -----------------------------------------------------------------------
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # [MOD-注释增强-20260901] logger.exception 会自动把当前异常的完整堆栈打出来
        logging.getLogger("app").exception(
            "未捕获异常: %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"code": 50000, "message": "服务器内部错误", "detail": None},
        )
