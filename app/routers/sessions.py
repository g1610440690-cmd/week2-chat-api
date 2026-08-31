"""会话路由：创建 / 列表 / 重命名 / 删除 / 消息历史。

所有接口都要求登录（Depends(get_current_user)），
并且只允许操作属于自己的会话。
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.deps import get_current_user
from ..database import get_db
from ..models import ChatSession, Message, User
from ..schemas import MessageOut, SessionCreate, SessionOut, SessionUpdate
from ..services.chat_service import chat_service

router = APIRouter(prefix="/sessions", tags=["会话"])


@router.post("", response_model=SessionOut, status_code=201, summary="创建会话")
async def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = ChatSession(user_id=user.id, title=body.title)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get("", response_model=list[SessionOut], summary="我的会话列表（含消息数）")
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # 子查询：按会话分组统计消息数
    count_subq = (
        select(Message.session_id, func.count(Message.id).label("cnt"))
        .group_by(Message.session_id)
        .subquery()
    )
    stmt = (
        select(ChatSession, func.coalesce(count_subq.c.cnt, 0))
        .outerjoin(count_subq, ChatSession.id == count_subq.c.session_id)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        SessionOut(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            message_count=cnt,
        )
        for session, cnt in rows
    ]


@router.get(
    "/{session_id}/messages",
    response_model=list[MessageOut],
    summary="会话消息历史（分页）",
)
async def list_messages(
    session_id: str,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.get_owned_session(db, session_id, user)  # 校验归属
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).scalars().all()


@router.patch("/{session_id}", response_model=SessionOut, summary="重命名会话")
async def rename_session(
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await chat_service.get_owned_session(db, session_id, user)
    session.title = body.title
    await db.commit()
    await db.refresh(session)
    return session


@router.delete("/{session_id}", status_code=204, summary="删除会话")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await chat_service.get_owned_session(db, session_id, user)
    await db.delete(session)
    await db.commit()
    return Response(status_code=204)
