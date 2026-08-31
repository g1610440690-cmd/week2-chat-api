"""文件上传路由。

要点：
- 文件类型白名单校验（扩展名）
- 大小限制（边读边统计，超限立即中止并清理已写文件）
- 用 uuid 重命名落盘，避免重名覆盖、路径穿越
- 上传目录通过 UPLOAD_DIR 配置，Docker 里挂载到数据卷
"""
import os
import uuid

from fastapi import APIRouter, Depends, File, UploadFile

from ..config import get_settings
from ..core.deps import get_current_user
from ..exceptions import FileTooLargeError, UnsupportedFileTypeError
from ..schemas import UploadOut

router = APIRouter(prefix="/upload", tags=["文件上传"])


@router.post(
    "",
    response_model=UploadOut,
    status_code=201,
    summary="上传文件（限制类型与大小）",
)
async def upload_file(
    file: UploadFile = File(...),
    user=Depends(get_current_user),  # 登录才能上传
):
    settings = get_settings()

    # 1. 类型校验：只看扩展名（学习版；生产建议同时校验 Content-Type 和文件魔数）
    filename = file.filename or "unnamed"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise UnsupportedFileTypeError(
            detail={"extension": ext, "allowed": sorted(settings.ALLOWED_UPLOAD_EXTENSIONS)}
        )

    # 2. 落盘：uuid 重命名，杜绝重名覆盖 / 路径穿越
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    dest_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(settings.UPLOAD_DIR, dest_name)

    size = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(256 * 1024)  # 每次读 256KB（流式写盘）
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_size_bytes:
                    raise FileTooLargeError(
                        detail={
                            "max_mb": settings.MAX_UPLOAD_SIZE_MB,
                            "received_bytes": size,
                        }
                    )
                out.write(chunk)
    except Exception:
        # 校验失败时清理半成品文件
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise

    return UploadOut(
        filename=filename,
        size=size,
        content_type=file.content_type or "application/octet-stream",
        url=f"/static/{dest_name}",
    )
