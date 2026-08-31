"""统一异常体系（第 2 周实践流程第 7 步）。

设计目标：无论哪里出错，返回给客户端的 JSON 都是同一个结构：
    {
        "code":    业务错误码（数字，例如 40400 表示"资源不存在"），
        "message": 人类可读的简短描述，
        "detail":  附加细节（可选，如字段错误列表、文件大小限制）
    }

用法：业务代码里直接 raise 对应的异常子类即可，
register_exception_handlers(app) 统一捕获并转成上面的 JSON。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """业务异常基类。code 是业务错误码，status_code 是 HTTP 状态码。"""

    def __init__(self, code: int, message: str, status_code: int = 400, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


class NotFoundError(AppError):
    """资源不存在（对外不区分"没有"还是"不属于你"，避免信息泄露）。"""

    def __init__(self, message: str = "资源不存在", detail=None):
        super().__init__(code=40400, message=message, status_code=404, detail=detail)


class InvalidCredentialsError(AppError):
    def __init__(self, message: str = "用户名或密码错误"):
        super().__init__(code=40100, message=message, status_code=401)


class AuthRequiredError(AppError):
    def __init__(self, message: str = "请先登录"):
        super().__init__(code=40101, message=message, status_code=401)


class ConflictError(AppError):
    def __init__(self, message: str = "资源已存在", detail=None):
        super().__init__(code=40900, message=message, status_code=409, detail=detail)


class RateLimitError(AppError):
    def __init__(self, message: str = "请求过于频繁，请稍后再试", detail=None):
        super().__init__(code=42900, message=message, status_code=429, detail=detail)


class FileTooLargeError(AppError):
    def __init__(self, message: str = "文件超过大小限制", detail=None):
        super().__init__(code=40001, message=message, status_code=400, detail=detail)


class UnsupportedFileTypeError(AppError):
    def __init__(self, message: str = "不支持的文件类型", detail=None):
        super().__init__(code=40002, message=message, status_code=400, detail=detail)


def register_exception_handlers(app: FastAPI) -> None:
    """把上面的异常类统一注册成 FastAPI 全局处理器。"""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # 覆盖默认 {"detail": ...} 结构，保持全站错误格式统一
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail), "detail": None},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        # Pydantic 校验失败统一成 422 + 字段错误列表
        return JSONResponse(
            status_code=422,
            content={
                "code": 42200,
                "message": "请求参数校验失败",
                "detail": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # 兜底：任何未预料的异常都记日志并返回 500，不把堆栈泄露给客户端
        logging.getLogger("app").exception(
            "未捕获异常: %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={"code": 50000, "message": "服务器内部错误", "detail": None},
        )
