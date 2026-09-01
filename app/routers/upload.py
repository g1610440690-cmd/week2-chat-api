"""[MOD-注释增强-20260901]
文件上传路由：POST /upload 单接口。

【文件上传的几个必须考虑的安全点（面试会问）】

1. 【扩展名白名单】：
   - 只允许 .txt / .md / .csv / .json / .log / .png / .jpg / .jpeg / .pdf
   - 绝不允许 .exe / .bat / .php / .jsp 等可执行脚本（防止上传 webshell）
   - 生产建议同时校验：Content-Type + 文件魔数（文件头真实字节），
     光看扩展名可以被"把 .exe 重命名成 .txt"绕过。

2. 【文件大小限制（边读边统计）】：
   - 不能等文件全读进内存再判断大小—— 攻击者上传 10GB 文件直接把服务内存打爆；
   - 我们每次只读 256KB，读一块加一块大小，超限立刻抛异常+删半成品。

3. 【UUID 重命名落盘 + 绝对不能用原始文件名当存储名】：
   - 防止重名覆盖（两个人都叫"学习笔记.txt"，谁的被覆盖？）；
   - 防止路径穿越攻击：如果有人文件名传 "../../../etc/passwd"，
     用原始名会写到系统目录；改成 UUID 就完全没这个风险。
   - 原始文件名只在响应里返回（给用户看的），磁盘上的文件名是 UUID。

4. 【异常时清理半成品文件】：
   - try/except 包裹写盘过程，任何异常（包括大小超限/磁盘满）都立刻删掉已经写了一半的文件，
     防止半成品留在磁盘上占空间。
"""

import os
import uuid

from fastapi import APIRouter, Depends, File, UploadFile

from ..config import get_settings
from ..core.deps import get_current_user
from ..exceptions import FileTooLargeError, UnsupportedFileTypeError
from ..schemas import UploadOut

router = APIRouter(prefix="/upload", tags=["文件上传"])


# ========================================================================
# [MOD-注释增强-20260901] 接口 1：POST /upload
# ========================================================================

@router.post(
    "",
    response_model=UploadOut,
    status_code=201,
    summary="上传文件（限制类型与大小）",
)
async def upload_file(
    file: UploadFile = File(...),
    user=Depends(get_current_user),  # [MOD-注释增强-20260901] 必须登录才能上传（防止匿名刷磁盘）
):
    """[MOD-注释增强-20260901]
    上传单个文件。

    完整流程：
    ① 取 settings；
    ② 扩展名白名单校验（不通过 → 40002 UnsupportedFileType）；
    ③ 确保 UPLOAD_DIR 存在；
    ④ 生成 UUID + 扩展名作为真正的落盘文件名；
    ⑤ 【流式】分块（256KB）读取上传文件，边读边统计大小，边写磁盘；
       任何一块读完后 size 超了 → 立刻抛 40001 FileTooLarge；
    ⑥ 异常时（包括 40001）：删掉已经写了一半的文件；
    ⑦ 返回 UploadOut：原始文件名、大小、MIME、下载 URL（/static/uuid.ext）。
    """
    settings = get_settings()

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 步骤 1：类型校验（只看扩展名，学习版简化）
    # ------------------------------------------------------------------
    filename = file.filename or "unnamed"
    # [MOD-注释增强-20260901] 取最后一个 . 后面的部分作为扩展名（全部小写，避免 .TXT 绕过）
    ext = os.path.splitext(filename)[1].lower()
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise UnsupportedFileTypeError(
            detail={"extension": ext, "allowed": sorted(settings.ALLOWED_UPLOAD_EXTENSIONS)}
        )

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 步骤 2：准备落盘路径（UUID 重命名）
    # ------------------------------------------------------------------
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    # [MOD-注释增强-20260901] 32 位十六进制 UUID + 原始扩展名 → 既保证唯一又保留扩展名
    dest_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(settings.UPLOAD_DIR, dest_name)

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 步骤 3：流式分块读 + 写盘 + 实时统计大小
    # ------------------------------------------------------------------
    size = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                # [MOD-注释增强-20260901] 每次读 256KB（256*1024 字节）
                #                            不大不小：太小会循环太多次，太大会占内存
                chunk = await file.read(256 * 1024)
                if not chunk:
                    break  # [MOD-注释增强-20260901] 读不到内容了 → 上传结束
                size += len(chunk)
                # [MOD-注释增强-20260901] 超限 → 立刻抛（被下面 except 接住会清理半成品）
                if size > settings.max_upload_size_bytes:
                    raise FileTooLargeError(
                        detail={
                            "max_mb": settings.MAX_UPLOAD_SIZE_MB,
                            "received_bytes": size,
                        }
                    )
                out.write(chunk)
    except Exception:
        # [MOD-注释增强-20260901] 任何异常 → 清理落盘到一半的文件（防止垃圾占磁盘）
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise  # [MOD-注释增强-20260901] 再原封不动 re-raise，交给全局异常处理器返回 JSON

    # ------------------------------------------------------------------
    # [MOD-注释增强-20260901] 步骤 4：返回上传结果
    #   - url = /static/{uuid}.ext，配合 main.py 里 app.mount("/static", ...)
    #     可以直接在浏览器里下载/渲染；
    #   - content_type 兜底成 application/octet-stream（当 UploadFile 没识别到时）。
    # ------------------------------------------------------------------
    return UploadOut(
        filename=filename,
        size=size,
        content_type=file.content_type or "application/octet-stream",
        url=f"/static/{dest_name}",
    )
