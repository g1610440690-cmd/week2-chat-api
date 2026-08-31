"""SSE 流式客户端示例：演示如何消费 /chat/sessions/{id}/stream。

用法（先启动服务并注册拿到 token）：
    python examples/stream_client.py <你的token>
    # 不带 session_id 时自动新建一个会话，然后就可以对话了

这个脚本展示了一个重要概念：流式输出不是一次性拿到全部结果，
而是每收到一个 token 事件就打印一块，形成"打字机"效果。
"""
import json
import sys

import httpx

BASE_URL = "http://localhost:8000"


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python examples/stream_client.py <token> [session_id]")
        sys.exit(1)

    token = sys.argv[1]
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=BASE_URL, headers=headers) as client:
        # 拿到（或新建）一个会话
        if len(sys.argv) >= 3:
            session_id = sys.argv[2]
        else:
            session_id = client.post("/sessions", json={}).json()["id"]
        print(f"会话: {session_id}（Ctrl+C 退出）\n")

        while True:
            prompt = input("你> ").strip()
            if not prompt:
                continue

            print("AI> ", end="", flush=True)
            # 流式读取 SSE
            with client.stream(
                "POST",
                f"/chat/sessions/{session_id}/stream",
                json={"content": prompt},
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    event = json.loads(line[len("data: "):])
                    if event["type"] == "token":
                        print(event["content"], end="", flush=True)
                    elif event["type"] == "done":
                        print("\n")
                        break


if __name__ == "__main__":
    main()
