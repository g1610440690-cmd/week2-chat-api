"""10 个接口测试（第 2 周实践流程第 7 步）。

覆盖：注册（成功/重复/参数非法）、登录（成功/密码错误）、
鉴权（未登录 401）、会话（创建/列表）、聊天（非流式/历史/SSE 流式）、文件上传。

运行：pytest -v
"""

# [MOD-注释增强-20260901] 引入 pytest 测试框架，用于编写和运行测试用例
import pytest


# ========================================================================
# [MOD-注释增强-20260901]
# 测试辅助函数区域
# 作用：把多个测试用例中重复出现的"注册、构造请求头、创建会话"等操作抽成公共函数，
#       避免代码重复，让每个测试用例更聚焦于验证"业务逻辑本身"。
# ========================================================================


def register(client, username: str, password: str = "secret123"):
    """[MOD-注释增强-20260901]
    【辅助函数 1】注册新用户并直接返回 access_token。

    设计意图：
    - 后面的大部分测试（聊天、会话、文件上传等）都需要"先有一个已登录的用户"才能跑，
      所以把"注册+拿token"这两步封装成一个函数，调用方一行代码就能拿到可用的 token。
    - 每个测试用例都传【不同的用户名】（如 carol / dave / erin...），
      目的是【隔离测试数据】，防止上一个测试注册的用户影响下一个测试的结果。

    参数说明：
    :param client:    pytest 提供的 FastAPI 测试客户端（可以理解为模拟的 HTTP 客户端）
    :param username:  要注册的用户名（每个测试用例传不同的，避免冲突）
    :param password:  密码，默认值 "secret123"，够复杂能通过密码强度校验

    :return: 注册成功后接口返回的 access_token（字符串，JWT 格式）
    """
    # [MOD-注释增强-20260901] 【第1步】调用 POST /auth/register 接口，提交用户名、邮箱、密码
    # 邮箱自动用用户名拼接，省去调用方每次都要传邮箱
    resp = client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": password},
    )
    # [MOD-注释增强-20260901] 【第2步】断言 HTTP 状态码必须是 201（创建成功）
    # 如果失败就把响应文本打印出来，方便排查是哪一步出了问题
    assert resp.status_code == 201, resp.text
    # [MOD-注释增强-20260901] 【第3步】从响应 JSON 中取出 access_token 并返回给调用方
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    """[MOD-注释增强-20260901]
    【辅助函数 2】构造"带 Bearer Token 的 Authorization 请求头"字典。

    作用：所有需要登录才能访问的接口（受保护接口），都必须在 HTTP 请求头里带上
         Authorization: Bearer <token>，FastAPI 的 OAuth2 机制才会认这个用户。
         每次手写这一行太麻烦，所以封装成函数。

    :param token: register() 或 login() 返回的 JWT access_token
    :return:      一个可以直接传给 client.get/post 的 headers= 参数的字典
                  例如：{"Authorization": "Bearer eyJhbGciOiJIUzI1NiIs..."}
    """
    return {"Authorization": f"Bearer {token}"}


def create_session(client, token: str, title: str | None = None) -> str:
    """[MOD-注释增强-20260901]
    【辅助函数 3】创建一个聊天会话，并返回新会话的 ID。

    背景：聊天消息是挂在"会话"下面的，要发消息必须先有一个 session_id。
         所以把"创建会话+拿session_id"封装成函数。

    :param client: FastAPI 测试客户端
    :param token:  已登录用户的 access_token（创建会话必须登录）
    :param title:  【可选参数】会话标题。
                   - 如果传了值，就用这个值作为标题；
                   - 如果不传（None），后端会用用户发的第一句话自动生成标题。
    :return:       新创建的会话 ID（字符串，UUID 格式）
    """
    # [MOD-注释增强-20260901] 调用 POST /sessions 创建会话
    # title 有值就传 {"title": xxx}，没值就传空 JSON {}，避免传 None 让后端困惑
    resp = client.post("/sessions", json={"title": title} if title else {}, headers=auth(token))
    # [MOD-注释增强-20260901] 断言创建成功（201 Created）
    assert resp.status_code == 201, resp.text
    # [MOD-注释增强-20260901] 返回会话 ID，后续发消息、查历史都要用到它
    return resp.json()["id"]


# ========================================================================
# [MOD-注释增强-20260901]
# 第一组测试（1~5）：【认证模块】—— 注册 + 登录
# 目标：验证用户能注册、重复注册会报错、密码太弱不让过、
#       登录成功能拿 token、密码错误返回 401。
# ========================================================================


def test_01_register_success(client):
    """[MOD-注释增强-20260901]
    【测试用例 1】注册新用户 - 成功场景。

    验证点（共 4 个）：
    ① HTTP 状态码必须是 201（资源创建成功）
    ② 响应体里必须包含 access_token（说明"注册即登录"，注册完自动登录了）
    ③ 响应里 user.username 必须是我们传进去的 "alice"
    ④ 【安全断言】响应里绝对不能出现 password 字段（哪怕是哈希也不能泄露）
    """
    # [MOD-注释增强-20260901] 模拟浏览器提交注册表单：用户名、邮箱、密码
    resp = client.post(
        "/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "secret123"},
    )
    # [MOD-注释增强-20260901] 验证点①：状态码 201
    assert resp.status_code == 201
    # [MOD-注释增强-20260901] 把响应体转成 Python 字典，方便后续取字段
    data = resp.json()
    # [MOD-注释增强-20260901] 验证点②：access_token 非空（非空字符串在 Python 里是 True）
    assert data["access_token"]
    # [MOD-注释增强-20260901] 验证点③：用户名正确
    assert data["user"]["username"] == "alice"
    # [MOD-注释增强-20260901] 验证点④：【安全检查】把整个响应转成字符串，搜不到 "password" 字样
    #                            哪怕后端想不小心把密码哈希带出来，这里也能拦住
    assert "password" not in str(data)


def test_02_register_duplicate_returns_409(client):
    """[MOD-注释增强-20260901]
    【测试用例 2】重复注册 - 返回 409 Conflict。

    背景：测试用例 1 已经用 "alice" 注册过了（pytest 按函数名字典序跑，01 在 02 前面），
          这里再注册一次 "alice"，后端应该拒绝并报错。

    验证点（共 3 个）：
    ① HTTP 状态码必须是 409（资源冲突 = 用户名已存在）
    ② 响应里的业务错误码 code 必须是 40900（项目约定的"用户名重复"错误码）
    ③ 响应里必须有人类可读的 message 字段（不能是空的，否则前端不知道咋提示用户）
    """
    # [MOD-注释增强-20260901] 用相同用户名 "alice" 再次注册（邮箱故意换了个，
    #                            目的是验证"只要用户名重复就报错"，跟邮箱无关）
    resp = client.post(
        "/auth/register",
        json={"username": "alice", "email": "another@example.com", "password": "secret123"},
    )
    # [MOD-注释增强-20260901] 验证点①：409 Conflict
    assert resp.status_code == 409
    body = resp.json()
    # [MOD-注释增强-20260901] 验证点②：业务错误码 40900
    assert body["code"] == 40900
    # [MOD-注释增强-20260901] 验证点③：错误信息非空（非空字符串为 True）
    assert body["message"]


def test_03_register_invalid_password_returns_422(client):
    """[MOD-注释增强-20260901]
    【测试用例 3】注册时密码太短 - 返回 422 Unprocessable Entity。

    背景：后端用 Pydantic 对请求参数做校验，密码应该有"最少几位"的限制（比如 ≥ 6 位）。
          这里故意传 "123"（3位），看看校验会不会拦住。

    验证点（共 2 个）：
    ① HTTP 状态码必须是 422（Pydantic 参数校验失败的标准返回码）
    ② 业务错误码 code 必须是 42200（项目约定的"参数非法"错误码）
    """
    # [MOD-注释增强-20260901] 用新用户名 bob + 超短密码 123 注册
    resp = client.post(
        "/auth/register",
        json={"username": "bob", "email": "bob@example.com", "password": "123"},  # 太短
    )
    # [MOD-注释增强-20260901] 验证点①：422 参数校验失败
    assert resp.status_code == 422
    # [MOD-注释增强-20260901] 验证点②：业务错误码 42200
    assert resp.json()["code"] == 42200


def test_04_login_success(client):
    """[MOD-注释增强-20260901]
    【测试用例 4】登录 - 成功场景。

    背景：测试用例 1 已经注册好了 alice / secret123，这里直接用这套账号密码登录。

    验证点（共 2 个）：
    ① HTTP 状态码必须是 200（OK）
    ② 响应体里必须包含 access_token（登录成功的标志）
    """
    # [MOD-注释增强-20260901] 调用 POST /auth/login，用 alice 的账号密码登录
    resp = client.post(
        "/auth/login", json={"username_or_email": "alice", "password": "secret123"}
    )
    # [MOD-注释增强-20260901] 验证点①：200 OK
    assert resp.status_code == 200
    # [MOD-注释增强-20260901] 验证点②：拿到了 token（非空即成功）
    assert resp.json()["access_token"]


def test_05_login_wrong_password_returns_401(client):
    """[MOD-注释增强-20260901]
    【测试用例 5】登录 - 密码错误返回 401 Unauthorized。

    验证点（共 2 个）：
    ① HTTP 状态码必须是 401（未授权 = 认证失败）
    ② 业务错误码 code 必须是 40100（项目约定的"认证失败"错误码）
    """
    # [MOD-注释增强-20260901] 用户名是对的（alice），但密码故意写错
    resp = client.post(
        "/auth/login", json={"username_or_email": "alice", "password": "wrong-pass"}
    )
    # [MOD-注释增强-20260901] 验证点①：401 未授权
    assert resp.status_code == 401
    # [MOD-注释增强-20260901] 验证点②：业务错误码 40100
    assert resp.json()["code"] == 40100


# ========================================================================
# [MOD-注释增强-20260901]
# 第二组测试（6~7）：【会话模块】
# 目标：验证创建会话必须登录（鉴权）、能创建会话、能在列表里看到刚创建的会话。
# ========================================================================


def test_06_create_session_requires_auth(client):
    """[MOD-注释增强-20260901]
    【测试用例 6】鉴权验证 - 创建会话必须先登录。

    作用：这是一个【安全测试】，专门用来防止"忘加 @app.get(..., dependencies=[Depends(get_current_user)])"
          这种低级错误——如果后端某接口忘了做鉴权，匿名用户也能调，那就是大漏洞。

    验证点（共 1 个）：
    ① 不带 Authorization 请求头直接调 POST /sessions，必须返回 401
    """
    # [MOD-注释增强-20260901] 故意不带 headers=auth(token)，模拟"未登录的匿名用户"
    resp = client.post("/sessions", json={})
    # [MOD-注释增强-20260901] 验证点①：必须被拦住，返回 401
    assert resp.status_code == 401


def test_07_create_and_list_session(client):
    """[MOD-注释增强-20260901]
    【测试用例 7】创建会话 + 查询会话列表 - 正常流程。

    完整流程：注册用户 → 创建会话 → 查询会话列表 → 校验列表内容。

    验证点（共 3 个）：
    ① 会话列表里【能找到】刚才创建的那个会话（用 sid 比对）
    ② 列表按时间倒序排列，最新的会话在最前面（所以 sessions[0] 就是刚创建的）
    ③ 新建的会话【还没发过消息】，message_count 必须是 0
    """
    # [MOD-注释增强-20260901] 【步骤1】注册新用户 carol（隔离数据，不跟前面的 alice 混）
    token = register(client, "carol")
    # [MOD-注释增强-20260901] 【步骤2】创建一个带自定义标题的会话
    sid = create_session(client, token, title="我的第一个会话")

    # [MOD-注释增强-20260901] 【步骤3】调用 GET /sessions 拉取当前用户的所有会话列表
    resp = client.get("/sessions", headers=auth(token))
    assert resp.status_code == 200
    sessions = resp.json()
    # [MOD-注释增强-20260901] 验证点①：any() 遍历列表，看有没有一个会话的 id 等于我们刚创建的 sid
    assert any(s["id"] == sid for s in sessions)
    # [MOD-注释增强-20260901] 验证点②+③：最新会话在列表第 0 位，且消息数为 0
    assert sessions[0]["message_count"] == 0


# ========================================================================
# [MOD-注释增强-20260901]
# 第三组测试（8~9）：【聊天模块】—— 普通消息 + SSE 流式消息
# 目标：验证①能发消息、收到回复、历史记录正确、自动生成标题；
#       ②SSE 流式接口返回的格式正确，且消息也会持久化。
# ========================================================================


def test_08_send_message_and_get_history(client):
    """[MOD-注释增强-20260901]
    【测试用例 8】发送普通消息（非流式）+ 查看聊天历史 + 自动生成标题。

    完整流程：
    注册(dave) → 创建会话(无标题) → 发消息"你好，FastAPI" → 拿到回复
    → 查历史消息 → 查会话列表看自动生成的标题

    验证点（共 5 个）：
    ① 发送消息接口返回 200，且回复的角色是 assistant（不是 user）
    ② 回复内容里"包含用户的原话"（这是 Echo 模式的特征，后端把用户的话复读回来）
    ③ 历史消息按发送顺序排列：先是 user，再是 assistant
    ④ 新建会话没传标题，后端会用"用户第一句话"自动生成标题
    ⑤ 自动生成的标题只截取【前 20 个字符】（防止一句话太长把标题撑爆）
    """
    # [MOD-注释增强-20260901] 【步骤1】注册新用户 dave
    token = register(client, "dave")
    # [MOD-注释增强-20260901] 【步骤2】创建会话（不传标题，待会儿看后端会不会自动生成）
    sid = create_session(client, token)

    # [MOD-注释增强-20260901] 【步骤3】调用发送消息接口 POST /chat/sessions/{sid}/messages
    resp = client.post(
        f"/chat/sessions/{sid}/messages",
        json={"content": "你好，FastAPI"},
        headers=auth(token),
    )
    assert resp.status_code == 200
    reply = resp.json()
    # [MOD-注释增强-20260901] 验证点①：回复的角色是 AI 助手
    assert reply["role"] == "assistant"
    # [MOD-注释增强-20260901] 验证点②：回复里包含用户的问题（Echo 模式）
    assert "你好，FastAPI" in reply["content"]

    # [MOD-注释增强-20260901] 【步骤4】调用 GET /sessions/{sid}/messages 查这个会话的全部历史消息
    history = client.get(f"/sessions/{sid}/messages", headers=auth(token))
    assert history.status_code == 200
    # [MOD-注释增强-20260901] 把所有消息的 role 字段摘出来做成一个列表
    roles = [m["role"] for m in history.json()]
    # [MOD-注释增强-20260901] 验证点③：顺序必须是 用户问 → AI 答
    assert roles == ["user", "assistant"]

    # [MOD-注释增强-20260901] 【步骤5】查会话列表，看后端有没有自动给会话生成标题
    sessions = client.get("/sessions", headers=auth(token)).json()
    # [MOD-注释增强-20260901] 验证点④+⑤：标题 = 第一句话的前 20 个字符
    assert sessions[0]["title"] == "你好，FastAPI"[:20]


def test_09_stream_chat_returns_sse(client):
    """[MOD-注释增强-20260901]
    【测试用例 9】SSE 流式聊天接口 - 格式校验 + 消息持久化。

    背景：大模型聊天常见两种模式：
    - 普通模式：等 AI 全部想好了，一次性返回（测试用例 8）
    - 流式模式：AI 想一个字吐一个字（SSE = Server-Sent Events，服务端推送事件）
               前端看起来就像"打字机效果"

    SSE 帧格式约定（后端必须按这个格式推）：
    - 开头先发一帧 {"type": "start"}  （告诉前端：开始啦）
    - 中间发 N 帧 {"type": "token", "content": "字"} （一个字一帧）
    - 最后发一帧 {"type": "done"}     （告诉前端：结束啦，没有了）

    验证点（共 6 个）：
    ① 流式接口返回 200，且 Content-Type 是 text/event-stream（SSE 标准 MIME）
    ② 响应体以 "data: " 开头（SSE 协议规定，每一帧的前缀都是 data: ）
    ③ 响应里能找到 start 帧
    ④ 响应里能找到 token 帧（说明真的有内容出来）
    ⑤ 响应里能找到 done 帧（说明正常结束了，不是中途断了）
    ⑥ 流式说完的话，后端也必须【持久化到数据库】，查历史能查到这两条消息
    """
    # [MOD-注释增强-20260901] 【步骤1】注册新用户 erin + 创建会话
    token = register(client, "erin")
    sid = create_session(client, token)

    # [MOD-注释增强-20260901] 【步骤2】用 client.stream 调流式接口
    #                            with 语法保证响应流在退出时会被正确关闭
    with client.stream(
        "POST",
        f"/chat/sessions/{sid}/stream",
        json={"content": "请用流式输出回答"},
        headers=auth(token),
    ) as resp:
        # [MOD-注释增强-20260901] 验证点①：状态码 + Content-Type 必须是 SSE 格式
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # [MOD-注释增强-20260901] iter_text() 一帧一帧地把响应体读出来，拼成一个大字符串
        body = "".join(resp.iter_text())

    # [MOD-注释增强-20260901] 验证点②：SSE 帧格式开头必须是 "data: "
    assert body.startswith("data: ")
    # [MOD-注释增强-20260901] 验证点③：有 start 帧
    assert '"type": "start"' in body
    # [MOD-注释增强-20260901] 验证点④：有 token 帧
    assert '"type": "token"' in body
    # [MOD-注释增强-20260901] 验证点⑤：有 done 帧
    assert '"type": "done"' in body

    # [MOD-注释增强-20260901] 【步骤3】流式回复结束后，查历史消息——
    #                            验证流式接口也会把消息写进数据库，而不是"说完就忘"
    history = client.get(f"/sessions/{sid}/messages", headers=auth(token)).json()
    # [MOD-注释增强-20260901] 验证点⑥：历史记录必须是 用户问 → AI 答
    assert [m["role"] for m in history] == ["user", "assistant"]


# ========================================================================
# [MOD-注释增强-20260901]
# 第四组测试（10）：【文件上传模块】
# 目标：验证①能上传文件、拿到文件 URL 和大小；
#       ②通过返回的 URL 能把文件下载回来，且内容跟上传的完全一致。
# ========================================================================


def test_10_upload_and_download_file(client):
    """[MOD-注释增强-20260901]
    【测试用例 10】上传文件 + 下载验证 - 端到端闭环。

    完整流程：
    注册(frank) → 上传 txt 文件 → 校验上传响应（size > 0、url 以 /static/ 开头）
    → 用返回的 URL 下载文件 → 校验下载内容 == 上传内容

    验证点（共 5 个）：
    ① 上传返回 201 Created
    ② size > 0（不可能上传了个 0 字节的空文件吧？）
    ③ url 以 /static/ 开头（FastAPI 的 StaticFiles 挂载路径约定）
    ④ 用 url 能正常下载，返回 200
    ⑤ 下载回来的文本里，能找到上传时写进去的关键内容 "FastAPI 与 Docker"
    """
    # [MOD-注释增强-20260901] 【步骤1】注册新用户 frank
    token = register(client, "frank")

    # [MOD-注释增强-20260901] 【步骤2】调用 POST /upload 上传文件
    # files 参数的格式是：{"字段名": (文件名, 文件二进制内容, MIME类型)}
    # 这里模拟上传一个叫"学习笔记.txt"的文本文件，内容是"第 2 周：FastAPI 与 Docker"
    resp = client.post(
        "/upload",
        files={"file": ("学习笔记.txt", "第 2 周：FastAPI 与 Docker".encode("utf-8"), "text/plain")},
        headers=auth(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    # [MOD-注释增强-20260901] 验证点②：文件大小大于 0
    assert data["size"] > 0
    # [MOD-注释增强-20260901] 验证点③：URL 必须是 /static/xxx（静态文件服务路径）
    assert data["url"].startswith("/static/")

    # [MOD-注释增强-20260901] 【步骤3】直接用后端返回的 URL 发起 GET 请求，把文件下载回来
    download = client.get(data["url"])
    # [MOD-注释增强-20260901] 验证点④：下载成功
    assert download.status_code == 200
    # [MOD-注释增强-20260901] 验证点⑤：文件内容没丢、没损坏（找到关键字即视为一致）
    assert "FastAPI 与 Docker" in download.text
