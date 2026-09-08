# coweread 🌕

**微信读书人机共读工具层**——让 AI 以共读伙伴的身份，在你的微信读书里写想法、发评论、盖"共读进度"书签。批注自动定位到原文坐标，精确挂在那一句话上。

核心能力：

- **想法（批注）**：AI 给一句原文（`abstract`），coweread 自动换算成微信读书内部的 `data-wr-co` 坐标（`range`），写入后想法就挂在书里那句话下面。原文匹配失败会**拒绝写入**，绝不发错位批注。
- **评论**：读取 / 回复想法下的评论（含"回复某条"引用模式）。
- **划线**：写入划线并指定样式（5 种颜色 × 直线/波浪/荧光笔）。
- **查询**：书架、章节目录、真实阅读进度、自己的想法与划线、热门划线——全部走微信读书官方 API，稳定且低风控。
- **共读进度**：AI 自己的"读到哪一章"书签存在本地，与你的真实微信读书进度**严格分开**。
- **安全默认**：想法默认 `仅自己可见`；"幽灵模式"拦截浏览器会话的阅读进度上报，不会污染你的真实阅读进度。

13 个 MCP 工具一览：`get_shelf` / `get_toc` / `get_progress` / `get_chapter_text` / `add_review` / `add_chapter_review` / `my_reviews` / `best_bookmarks` / `list_comments` / `add_comment` / `get_reader_bookmark` / `set_reader_bookmark` / `reading_context`。

> ⚠️ **使用须知**：本项目通过自动化方式操作个人微信读书账号，可能违反微信读书用户协议，存在风控/限制账号风险。请仅用于个人账号、保持低频，并自行承担使用风险。所有写入默认仅自己可见（`isPrivate=1`）。本项目与腾讯/微信读书官方无任何关联。

---

## 手把手部署（小白版）

以下步骤在 Windows 10/11 上验证过（macOS/Linux 理论可用，登录脚本需自行替换 `.bat`）。

### 第 0 步：装两个前置工具

1. **Python 3.11+**：去 [python.org](https://www.python.org/downloads/) 下载安装，安装时勾选 "Add Python to PATH"。
2. **uv**（Python 包管理器）：按 Win+R 输入 `powershell` 回车，粘贴：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

装完关掉窗口重开一个（让 PATH 生效）。

### 第 1 步：拿代码 + 装依赖

```cmd
git clone https://github.com/IrisNyx/coweread.git
cd coweread
uv sync
uv run playwright install chromium
```

`playwright install chromium` 会下载一个约 120MB 的浏览器（放在用户缓存目录，多个项目共用，不会重复下载）。

### 第 2 步：配置微信读书官方 API Key

微信读书官方有一个 "AI 技能" 接口（和 App 里官方 AI 助手同源），coweread 用它做一切只读查询。你需要一个 `wrk-` 开头的 key：

1. 在项目目录新建一个文本文件，命名 `.env`（注意开头有个点）；
2. 里面写一行：

```
WEREAD_API_KEY=wrk-你的key
```

> 如果你不知道去哪拿 key：它是微信读书官方 AI 技能平台的凭证，随官方 "技能" 体系发放。可以参考 [weread-mcp](https://github.com/) 等同类项目的说明寻找获取途径。

### 第 3 步：扫码登录（微信读书网页版）

双击项目里的 **`login.bat`**，会弹出浏览器和二维码，用微信扫码确认。看到 "登录成功" 即可。

登录态保存在 `state/weread-state.json`（已被 .gitignore 排除，不会上传）。登录态约 3 天会过期，coweread 内置了**自动续期**：写操作发现过期会自己开一次页面续命重试；只有连续失败才需要重新扫码。

### 第 4 步：接入你的 AI 客户端（MCP）

以支持 MCP 的客户端（Claude Desktop、Cherry Studio 等）为例，在 MCP 配置里加：

```json
{
  "mcpServers": {
    "coweread": {
      "command": "uv",
      "args": ["--directory", "C:\\你的路径\\coweread", "run", "coweread", "mcp"]
    }
  }
}
```

重启客户端，看到 13 个 `coweread` 开头的工具即成功。先对 AI 说 "查一下我的微信读书书架" 验证。

### 第 5 步：写第一条想法

对 AI 说类似这样的话：

> 在《万物发明指南》里找到这句话："人类通过不断焚烧一切可烧之物"，在它下面写一条想法："AI 共读第 1 天。"

AI 的动作会是：`get_toc` 拿目录 → `get_chapter_text` 读章节 → 定位这句话的坐标 → `add_review` 写入。打开手机微信读书，想法就挂在那句话上了（仅自己可见）。所有 AI 写的想法和评论都自带 **🌕 署名**，方便你在 App 里筛选。

---

## 远程接入（可选）：HTTPS MCP

如果 AI 客户端和 coweread 不在同一台机器（例如部署在家用服务器、手机端 AI 访问），可以用 HTTP 模式：

```cmd
uv run coweread mcp --http --port 8473 --token 一个长随机字符串
```

- MCP 端点：`http://你的机器:8473/mcp`（MCP Streamable HTTP 传输）
- 客户端连接时带请求头 `Authorization: Bearer 你的token`
- **务必设 token**：不设的话，任何能访问这个端口的人都能写你的微信读书账号

要上 HTTPS，用反向代理套一层，例如 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) 隧道（免费、无需公网 IP）：

```yaml
# cloudflared config.yml
ingress:
  - hostname: coweread.example.com
    service: http://localhost:8473
  - service: http_status:404
```

或 nginx：

```nginx
location /mcp {
    proxy_pass http://127.0.0.1:8473;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_read_timeout 300s;
}
```

---

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 写入报 "登录态失效" 或 errCode -2012 | cookie 过期。先等一次（有自愈重试）；仍失败就重新跑 `login.bat` 扫码 |
| 查询报 errcode 99 | 微信读书官方升级了 Skill 版本。升级 coweread，或手动改 `official.py` 里的 `SKILL_VERSION` |
| 抓不到章节文本（hook 超时） | 微信读书网页前端改版，`preRenderContent` 结构变了。带 `--no-headless` 跑 `coweread dump-chapter` 观察，欢迎提 issue |
| 想法挂错了位置？ | 不会静默挂错——abstract 匹配失败会直接报错拒发。注意 abstract 必须和原文**逐字一致**（含标点） |
| 阅读进度被改了 | 正常情况不会（幽灵模式拦截进度上报）。若遇到请提 issue |

## 架构速览

三条通道，对调用方完全屏蔽：

| 通道 | 用途 |
| --- | --- |
| 官方 Agent Gateway（wrk key） | 一切只读查询 |
| Playwright + innerHTML hook | 章节明文 + `data-wr-co` 坐标（官方不提供未划线原文） |
| Web cookie | 一切写入（想法/评论/划线——官方 API 只读） |

技术细节（坐标系原理、全部端点、踩坑记录）见 [docs/TECHNICAL.md](docs/TECHNICAL.md)。

## License

[MIT](LICENSE)
