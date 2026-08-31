"""10 个接口测试（第 2 周实践流程第 7 步）。

覆盖：注册（成功/重复/参数非法）、登录（成功/密码错误）、
鉴权（未登录 401）、会话（创建/列表）、聊天（非流式/历史/SSE 流式）、文件上传。

运行：pytest -v
"""
import pytest

# ---------- 测试辅助函数 ----------


def register(client, username: str, password: str = "secret123"):
    """注册并返回 token（每个测试用独立用户名，避免相互影响）。"""
    resp = client.post(
        "/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_session(client, token: str, title: str | None = None) -> str:
    resp = client.post("/sessions", json={"title": title} if title else {}, headers=auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------- 1~5：认证 ----------


def test_01_register_success(client):
    resp = client.post(
        "/auth/register",
        json={"username": "alice", "email": "alice@example.com", "password": "secret123"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["access_token"]  # 注册即登录，返回 token
    assert data["user"]["username"] == "alice"
    # 响应里绝不能有密码哈希
    assert "password" not in str(data)


def test_02_register_duplicate_returns_409(client):
    resp = client.post(
        "/auth/register",
        json={"username": "alice", "email": "another@example.com", "password": "secret123"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == 40900  # 统一错误码
    assert body["message"]


def test_03_register_invalid_password_returns_422(client):
    resp = client.post(
        "/auth/register",
        json={"username": "bob", "email": "bob@example.com", "password": "123"},  # 太短
    )
    assert resp.status_code == 422  # Pydantic 校验失败
    assert resp.json()["code"] == 42200


def test_04_login_success(client):
    resp = client.post(
        "/auth/login", json={"username_or_email": "alice", "password": "secret123"}
    )
    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_05_login_wrong_password_returns_401(client):
    resp = client.post(
        "/auth/login", json={"username_or_email": "alice", "password": "wrong-pass"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 40100


# ---------- 6~7：会话 ----------


def test_06_create_session_requires_auth(client):
    resp = client.post("/sessions", json={})  # 不带 token
    assert resp.status_code == 401


def test_07_create_and_list_session(client):
    token = register(client, "carol")
    sid = create_session(client, token, title="我的第一个会话")

    resp = client.get("/sessions", headers=auth(token))
    assert resp.status_code == 200
    sessions = resp.json()
    assert any(s["id"] == sid for s in sessions)
    assert sessions[0]["message_count"] == 0  # 新会话消息数为 0


# ---------- 8~9：聊天 ----------


def test_08_send_message_and_get_history(client):
    token = register(client, "dave")
    sid = create_session(client, token)

    resp = client.post(
        f"/chat/sessions/{sid}/messages",
        json={"content": "你好，FastAPI"},
        headers=auth(token),
    )
    assert resp.status_code == 200
    reply = resp.json()
    assert reply["role"] == "assistant"
    assert "你好，FastAPI" in reply["content"]  # 回复里包含用户的问题

    # 历史消息：user -> assistant
    history = client.get(f"/sessions/{sid}/messages", headers=auth(token))
    assert history.status_code == 200
    roles = [m["role"] for m in history.json()]
    assert roles == ["user", "assistant"]

    # 第一句话自动生成标题
    sessions = client.get("/sessions", headers=auth(token)).json()
    assert sessions[0]["title"] == "你好，FastAPI"[:20]


def test_09_stream_chat_returns_sse(client):
    token = register(client, "erin")
    sid = create_session(client, token)

    with client.stream(
        "POST",
        f"/chat/sessions/{sid}/stream",
        json={"content": "请用流式输出回答"},
        headers=auth(token),
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    # SSE 帧：start -> token... -> done
    assert body.startswith("data: ")
    assert '"type": "start"' in body
    assert '"type": "token"' in body
    assert '"type": "done"' in body

    # 流式回复也已持久化
    history = client.get(f"/sessions/{sid}/messages", headers=auth(token)).json()
    assert [m["role"] for m in history] == ["user", "assistant"]


# ---------- 10：文件上传 ----------


def test_10_upload_and_download_file(client):
    token = register(client, "frank")

    resp = client.post(
        "/upload",
        files={"file": ("学习笔记.txt", "第 2 周：FastAPI 与 Docker".encode("utf-8"), "text/plain")},
        headers=auth(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["size"] > 0
    assert data["url"].startswith("/static/")

    # 通过 /static 下载回来验证内容一致
    download = client.get(data["url"])
    assert download.status_code == 200
    assert "FastAPI 与 Docker" in download.text
