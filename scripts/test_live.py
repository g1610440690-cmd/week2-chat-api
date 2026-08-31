"""在线接口测试脚本：对"正在运行"的 week2-chat-api（Docker 启动）做端到端冒烟测试。

覆盖 19 项检查：
  健康检查 / 注册 / 重复注册 409 / 参数校验 422 / 登录 / 密码错误 401 /
  未登录 401 / 创建会话 / 会话列表 / 重命名 / 非流式消息 / 消息历史 /
  SSE 流式 / 404 / 越权 404 / 文件上传 / 文件类型 400 / 超大文件 400 / 并发限流 429

用法（任选其一）：
    python scripts/test_live.py                  # 默认 http://localhost:8000
    python scripts/test_live.py http://IP:8000   # 指定地址
"""
import asyncio
import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

passed = 0
failed = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global passed, failed
    # 用 ASCII 标记，避免 Windows 控制台(GBK)编码崩溃
    mark = "[OK]  " if ok else "[FAIL]"
    print(f"  {mark} {name}" + (f"   {extra}" if extra else ""))
    if ok:
        passed += 1
    else:
        failed += 1


async def main() -> int:
    print(f"目标服务: {BASE_URL}\n")
    try:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=60) as c:
            # ---- 1. 健康检查 ----
            r = await c.get("/healthz")
            check("GET /healthz", r.status_code == 200 and r.json()["status"] == "ok", r.text)

            # ---- 2. 注册（用户名带时间戳保证唯一）----
            uname = f"tester{int(time.time())}"
            r = await c.post(
                "/auth/register",
                json={"username": uname, "email": f"{uname}@example.com", "password": "secret123"},
            )
            check("POST /auth/register", r.status_code == 201 and r.json().get("access_token"))
            token = r.json()["access_token"]
            h = {"Authorization": f"Bearer {token}"}

            # ---- 3. 重复注册 -> 409 ----
            r = await c.post(
                "/auth/register",
                json={"username": uname, "email": f"x{uname}@example.com", "password": "secret123"},
            )
            check("重复注册 -> 409", r.status_code == 409 and r.json()["code"] == 40900)

            # ---- 4. 非法参数 -> 422 ----
            r = await c.post(
                "/auth/register",
                json={"username": "x", "email": "not-an-email", "password": "1"},
            )
            check("非法参数 -> 422", r.status_code == 422 and r.json()["code"] == 42200)

            # ---- 5. 登录 ----
            r = await c.post("/auth/login", json={"username_or_email": uname, "password": "secret123"})
            check("POST /auth/login", r.status_code == 200 and r.json().get("access_token"))

            # ---- 6. 密码错误 -> 401 ----
            r = await c.post("/auth/login", json={"username_or_email": uname, "password": "wrong"})
            check("密码错误 -> 401", r.status_code == 401 and r.json()["code"] == 40100)

            # ---- 7. 未登录 -> 401 ----
            r = await c.post("/sessions", json={})
            check("未登录建会话 -> 401", r.status_code == 401)

            # ---- 8. 创建会话 ----
            r = await c.post("/sessions", json={"title": "接口测试会话"}, headers=h)
            check("POST /sessions", r.status_code == 201 and r.json()["id"])
            sid = r.json()["id"]

            # ---- 9. 会话列表 ----
            r = await c.get("/sessions", headers=h)
            check("GET /sessions", r.status_code == 200 and any(s["id"] == sid for s in r.json()))

            # ---- 10. 重命名 ----
            r = await c.patch(f"/sessions/{sid}", json={"title": "改名后的会话"}, headers=h)
            check("PATCH /sessions/{id}", r.status_code == 200 and r.json()["title"] == "改名后的会话")

            # ---- 11. 非流式消息 ----
            r = await c.post(
                f"/chat/sessions/{sid}/messages", json={"content": "你好 FastAPI"}, headers=h
            )
            check("POST /chat/.../messages", r.status_code == 200 and r.json()["role"] == "assistant")

            # ---- 12. 消息历史（顺序必须是 user -> assistant）----
            r = await c.get(f"/sessions/{sid}/messages", headers=h)
            roles = [m["role"] for m in r.json()]
            check("GET /sessions/{id}/messages", r.status_code == 200 and roles == ["user", "assistant"], str(roles))

            # ---- 13. SSE 流式 ----
            async with c.stream(
                "POST", f"/chat/sessions/{sid}/stream", json={"content": "流式输出测试"}, headers=h
            ) as resp:
                body = "".join([chunk async for chunk in resp.aiter_text()])
            n_tokens = body.count('"type": "token"')
            check(
                "POST /chat/.../stream (SSE)",
                resp.status_code == 200 and '"type": "done"' in body,
                f"token 事件 {n_tokens} 个",
            )

            # ---- 14. 不存在的会话 -> 404 ----
            r = await c.get("/sessions/no-such-session/messages", headers=h)
            check("不存在会话 -> 404", r.status_code == 404 and r.json()["code"] == 40400)

            # ---- 15. 越权访问别人的会话 -> 404（不泄露存在性）----
            other_sid = (await c.post("/sessions", json={}, headers=h)).json()["id"]
            uname2 = f"other{int(time.time())}"
            t2 = (
                await c.post(
                    "/auth/register",
                    json={"username": uname2, "email": f"{uname2}@example.com", "password": "secret123"},
                )
            ).json()["access_token"]
            r = await c.get(f"/sessions/{other_sid}/messages", headers={"Authorization": f"Bearer {t2}"})
            check("越权访问 -> 404", r.status_code == 404)

            # ---- 16. 文件上传 + 静态下载 ----
            r = await c.post(
                "/upload",
                files={"file": ("学习笔记.txt", "第 2 周学习笔记".encode("utf-8"), "text/plain")},
                headers=h,
            )
            check("POST /upload", r.status_code == 201 and r.json()["url"].startswith("/static/"))
            url = r.json()["url"]
            r = await c.get(url)
            check(f"GET {url}", r.status_code == 200 and "学习笔记" in r.text)

            # ---- 17. 非法文件类型 -> 400 ----
            r = await c.post(
                "/upload",
                files={"file": ("病毒.exe", b"MZ...", "application/octet-stream")},
                headers=h,
            )
            check("非法类型(.exe) -> 400", r.status_code == 400 and r.json()["code"] == 40002)

            # ---- 18. 超大文件（6MB > 5MB 限制）-> 400 ----
            r = await c.post(
                "/upload",
                files={"file": ("大文件.txt", b"x" * (6 * 1024 * 1024), "text/plain")},
                headers=h,
            )
            check("超大文件(6MB) -> 400", r.status_code == 400 and r.json()["code"] == 40001)

            # ---- 19. 并发限流 -> 429（Redis 生效验证）----
            uname3 = f"burst{int(time.time())}"
            t3 = (
                await c.post(
                    "/auth/register",
                    json={"username": uname3, "email": f"{uname3}@example.com", "password": "secret123"},
                )
            ).json()["access_token"]
            h3 = {"Authorization": f"Bearer {t3}"}
            s3 = (await c.post("/sessions", json={}, headers=h3)).json()["id"]

            sem = asyncio.Semaphore(10)  # 不超数据库连接池上限(15)

            async def fire(i: int) -> int:
                async with sem:
                    r = await c.post(
                        f"/chat/sessions/{s3}/stream", json={"content": f"突发第{i}条"}, headers=h3
                    )
                    return r.status_code

            codes = await asyncio.gather(*(fire(i) for i in range(31)))  # 31 > 30 次/分钟
            n429 = codes.count(429)
            check("并发 31 请求触发限流 429", n429 >= 1, f"429 次数: {n429}")

    except httpx.ConnectError as exc:
        print(f"❌ 无法连接 {BASE_URL} —— 服务没启动吗？试试：docker compose ps")
        print(f"   原因: {exc}")
        return 1

    print(f"\n结果: {passed} 项通过, {failed} 项失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
