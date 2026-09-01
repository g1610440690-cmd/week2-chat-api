"""[MOD-注释增强-20260901]
会话路由模块：会话的"增删改查" + 消息历史查询。

【权限控制设计】
    所有 5 个接口都声明了 `user: User = Depends(get_current_user)`，
    所以必须登录才能访问。
    同时，所有"针对某个具体会话"的操作（查历史/重命名/删除），
    都会先调 chat_service.get_owned_session(...) 做【归属校验】：
    查不到会话 OR 查到了但 user_id 不是当前用户 → 一律 404，
    保证"用户只能操作自己的会话"，绝不越权。

【会话列表里的消息数是怎么统计的？】
    sessions 表里没有 message_count 字段（那是冗余的，维护起来容易不一致）。
    我们用一条 SQL 一次性查出来：
    ① 用子查询 count_subq 按会话 id GROUP BY 统计每个会话有多少条消息；
    ② 主查询 LEFT JOIN 这个子查询（没消息的会话统计结果是 NULL，
       用 COALESCE(NULL, 0) 转成 0，Python 里看到的就是 0）。
    这样统计出来的数字永远和 messages 表真实数据一致，不会不一致。
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.deps import get_current_user
from ..database import get_db
from ..models import ChatSession, Message, User
from ..schemas import MessageOut, SessionCreate, SessionOut, SessionUpdate
from ..services.chat_service import chat_service

# [MOD-注释增强-20260901] 路由前缀 /sessions，Swagger 里"会话"分组
router = APIRouter(prefix="/sessions", tags=["会话"])


# ========================================================================
# [MOD-注释增强-20260901] 接口 1：创建会话 POST /sessions
# ========================================================================

@router.post("", response_model=SessionOut, status_code=201, summary="创建会话")
async def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[MOD-注释增强-20260901]
    创建一个新的聊天会话。

    - user_id 直接从当前登录用户拿（不是前端传！），防止伪造归属；
    - title 从 body.title 拿（默认"新会话"）；
    - 201 Created + 返回新创建的会话对象（含 id）。
    """
    session = ChatSession(user_id=user.id, title=body.title)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


# ========================================================================
# [MOD-注释增强-20260901] 接口 2：我的会话列表 GET /sessions
# ========================================================================

@router.get("", response_model=list[SessionOut], summary="我的会话列表（含消息数）")
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[MOD-注释增强-20260901]
    查询当前登录用户的所有会话（按"最近更新时间"倒序，最近聊过的排最前）。

    每条返回里带 message_count（当前会话有多少条消息），
    SQL 实现：
    ① 子查询：按会话 id 分组，COUNT(Message.id) 算出消息数；
    ② 主查询 chat_sessions LEFT JOIN 子查询（LEFT JOIN 是为了"没消息的会话也能列出来"）；
    ③ COALESCE(cnt, 0)：NULL（没消息）→ 0。
    """

    # [MOD-注释增强-20260901] 子查询：每个会话的消息数
    count_subq = (
        select(Message.session_id, func.count(Message.id).label("cnt"))
        .group_by(Message.session_id)
        .subquery()
    )
    # [MOD-注释增强-20260901] 主查询：会话 LEFT JOIN 消息数统计
    stmt = (
        select(ChatSession, func.coalesce(count_subq.c.cnt, 0))
        .outerjoin(count_subq, ChatSession.id == count_subq.c.session_id)
        .where(ChatSession.user_id == user.id)  # 只看"我"的会话
        .order_by(ChatSession.updated_at.desc())  # 最近更新的排最前
    )
    rows = (await db.execute(stmt)).all()
    # [MOD-注释增强-20260901] 手动组装成 SessionOut（因为 message_count 不是 ORM 字段）
    return [
        SessionOut(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            message_count=cnt,
        )
        for session, cnt in rows
    ]


# ========================================================================
# [MOD-注释增强-20260901] 接口 3：某个会话的消息历史 GET /sessions/{id}/messages
# ========================================================================

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
    """[MOD-注释增强-20260901]
    分页查询某个会话的历史消息（按发送时间正序，最早的排最前）。

    校验步骤（get_owned_session 里做）：
    ① 这个 session_id 对应的会话存在吗？
    ② 会话的 user_id == 当前登录用户的 id 吗？
    不满足任一条 → 直接 404（越权防护）。

    参数：
    - limit：一次最多拉多少条，默认 50（防止一次拉几万条把内存打爆）
    - offset：从第几条开始，配合 limit 做分页
    """
    # [MOD-注释增强-20260901] 先校验归属（越权防护）
    await chat_service.get_owned_session(db, session_id, user)
    # [MOD-注释增强-20260901] 按时间正序取 limit 条（早的在前）
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
        .limit(limit)
        .offset(offset)
    )
    return (await db.execute(stmt)).scalars().all()


# ========================================================================
# [MOD-注释增强-20260901] 接口 4：重命名会话 PATCH /sessions/{id}
# ========================================================================

@router.patch("/{session_id}", response_model=SessionOut, summary="重命名会话")
async def rename_session(
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[MOD-注释增强-20260901]
    重命名会话：PATCH 只改需要改的字段（这里只有 title 能改）。

    同样经过 get_owned_session 归属校验，只能改自己的。
    """
    session = await chat_service.get_owned_session(db, session_id, user)
    session.title = body.title
    await db.commit()
    await db.refresh(session)
    return session


# ========================================================================
# [MOD-注释增强-20260901] 接口 5：删除会话 DELETE /sessions/{id}
# ========================================================================

@router.delete("/{session_id}", status_code=204, summary="删除会话")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """[MOD-注释增强-20260901]
    删除一个会话（级联删除该会话下所有消息）。

    - 204 No Content：删除成功但不返回任何响应体（REST 惯例）；
    - 删除的级联：
      ① ORM 层 cascade="all, delete-orphan"：Python 里 delete(session) 会删掉 messages；
      ② 数据库层 ForeignKey ondelete="CASCADE"：就算绕过 ORM 直接跑 SQL 也会级联删。
      双保险保证不留下孤儿消息。
    """
    session = await chat_service.get_owned_session(db, session_id, user)
    await db.delete(session)
    await db.commit()
    # [MOD-注释增强-20260901] 204：返回空响应（显式构造 Response 写 status_code）
    return Response(status_code=204)
