# 部署到 GitHub 完整指南

> 目标：把 `week2-chat-api` 项目推送到 GitHub，并让推送后自动跑测试（CI）。
> 全程约 10 分钟。所有命令都在**项目根目录**（`week2-chat-api/`）执行。

---

## 第 1 步：准备

### 1.1 注册 GitHub 账号

打开 https://github.com → Sign up → 填邮箱/用户名/密码 → 验证邮箱。

### 1.2 确认本机已装 git

```bash
git --version   # 看到版本号即可，如 git version 2.55.0
```

没有的话去 https://git-scm.com/downloads 下载安装（一路 Next）。

### 1.3 配置身份（只做一次）

```bash
git config --global user.name "你的GitHub用户名"
git config --global user.email "你注册GitHub用的邮箱"
```

> 身份会写进每次 commit 记录里。`--global` 表示对所有仓库生效。

---

## 第 2 步：在 GitHub 上创建空仓库

1. 登录 GitHub → 右上角 **+** → **New repository**
2. Repository name 填 `week2-chat-api`
3. 选 **Public**（公开）或 **Private**（私有，只有你能看）
4. **不要**勾选 "Add a README file"（我们的 README 已经写好了，避免冲突）
5. 点 **Create repository**

创建后会看到一个页面，里面有 `git remote add origin ...` 等命令，下面第三步就用它。

---

## 第 3 步：初始化并推送（核心命令）

在项目根目录执行：

```bash
# ① 把当前目录变成 git 仓库（生成隐藏的 .git 目录）
git init

# ② 查看将要提交的文件（确认 .env、.venv 等已被 .gitignore 排除）
git status

# ③ 把所有文件加入暂存区（. 表示当前目录所有文件）
git add .

# ④ 提交（-m 后面写本次提交说明，中文/英文都行）
git commit -m "第 2 周交付：带会话持久化的流式聊天 API"

# ⑤ 把默认分支名改成 main（GitHub 默认分支叫 main）
git branch -M main

# ⑥ 关联远程仓库（地址替换成你自己的，从第 2 步创建后的页面复制）
git remote add origin https://github.com/你的用户名/week2-chat-api.git

# ⑦ 推送（-u 记住关联，以后直接 git push 即可）
git push -u origin main
```

**每一条都在干什么：**

| 命令 | 作用 |
|---|---|
| `git init` | 初始化仓库，开始跟踪文件 |
| `git add .` | 把改动放进"暂存区"（打包待提交） |
| `git commit -m` | 把暂存区内容固化成一次提交（带说明） |
| `git branch -M main` | 分支重命名为 main |
| `git remote add origin 地址` | 告诉 git 远程仓库在哪（origin 是别名） |
| `git push -u origin main` | 上传；-u 记录默认推送目标 |

推送成功后刷新 GitHub 页面就能看到代码了。

---

## 第 4 步：日常更新流程（以后改代码）

```bash
git add .
git commit -m "说明这次改了什么"
git push
```

三行搞定。养成习惯：**每次提交前 `git status` 看一眼**，确认没有把 `.env`、`uploads/`、`test_chat.db` 之类的东西提交上去（.gitignore 已经拦住了，但养成检查习惯）。

---

## 第 5 步：GitHub Actions 自动测试（CI）

项目里已经放好了 `.github/workflows/ci.yml`。推送后 GitHub 会自动：

1. 开一台 Ubuntu 虚拟机
2. 安装 Python 3.12 和依赖
3. 运行 `pytest -v`（10 个接口测试）

**查看结果**：GitHub 仓库页面 → **Actions** 标签 → 点最新一次运行 → 看测试是否通过。

绿色 ✅ = 测试全过。之后每次推送都会自动跑，别人提 Pull Request 也会触发 —— 相当于免费的代码质检。

---

## 第 6 步（可选）：用 SSH 方式推送（更安全，不用每次输密码）

HTTPS 推送首次要输用户名+Personal Access Token；SSH 用密钥对，配一次永久免密。

### 6.1 生成密钥

```bash
ssh-keygen -t ed25519 -C "你的邮箱"
# 一路回车即可（默认保存到 ~/.ssh/id_ed25519）
```

### 6.2 把公钥添加到 GitHub

```bash
cat ~/.ssh/id_ed25519.pub    # 复制输出的全部内容
```

GitHub → 右上角头像 → **Settings** → **SSH and GPG keys** → **New SSH key** → 粘贴 → 保存。

### 6.3 测试连接并切换远程地址

```bash
ssh -T git@github.com            # 看到 "Hi 你的用户名!" 即成功

git remote set-url origin git@github.com:你的用户名/week2-chat-api.git
git push                         # 之后推送不再要密码
```

---

## 常见问题 FAQ

### Q1：`git push` 被拒绝（non-fast-forward / fetch first）
别人（或网页端）已经改过远程代码，你的本地落后了。

```bash
git pull --rebase origin main    # 拉取远程改动，把你的提交"重放"到最新之上
git push
```

### Q2：提示要登录/输 token
GitHub 已不支持密码推送，HTTPS 方式需要 Personal Access Token：
GitHub → Settings → Developer settings → Personal access tokens → Generate new token
勾选 `repo` 权限 → 生成 → 复制 → 推送时当密码粘贴（token 只显示一次，丢了要重新生成）。

### Q3：不小心把 `.env` 提交上去了（密钥泄露！）
```bash
# 立即改掉 .env 里的密钥（JWT_SECRET 等），然后：
git rm --cached .env     # 从 git 跟踪中移除但保留本地文件
echo ".env" >> .gitignore
git add .gitignore
git commit -m "移除误提交的 .env"
git push
```
**注意**：已经推上 GitHub 的密钥要当作已泄露，务必换新值；公开仓库被别人 fork 后旧内容仍会存在。

### Q4：想删除远程仓库 / 改名
GitHub 仓库页面 → **Settings** → 最底部 **Danger Zone** → Delete this repository。
改名后在本地执行 `git remote set-url origin 新地址`。

### Q5：Windows 换行符（CRLF）警告
git 提示 `LF will be replaced by CRLF` 是正常的，不影响使用。想让仓库统一用 LF：

```bash
git config --global core.autocrlf true    # Windows 推荐
```

### Q6：怎么把 Docker 部署到服务器（进阶）
```bash
# 在服务器上（需安装 Docker）：
git clone https://github.com/你的用户名/week2-chat-api.git
cd week2-chat-api
cp .env.example .env          # 改成生产密钥
docker compose up -d --build  # 服务器上直接起全套
```

---

## 附录：本仓库应该提交 / 不该提交的文件

| 应该提交 | 不应该提交（已被 .gitignore 排除） |
|---|---|
| `app/`、`tests/`、`docs/` 全部代码 | `.env`（含密钥！） |
| `Dockerfile`、`docker-compose.yml` | `.venv/`（本机虚拟环境） |
| `requirements*.txt`、`pytest.ini` | `__pycache__/`、`.pytest_cache/` |
| `.env.example`（**示例**，不含真密钥） | `uploads/`、`logs/`（运行时产物） |
| `.github/workflows/ci.yml` | `test_chat.db`、`dev.db`（本地测试库） |
