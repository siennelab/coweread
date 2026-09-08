# coweread

> 微信读书「人机共读」工具层 —— 让 AI 伙伴能真正读同一本书、在同一句话上写下想法。

coweread 是一个**不含 LLM 的确定性工具层**,把微信读书的「读原文 / 查进度 / 写批注」收成稳定、可复现的接口,供上层 AI(MCP)、程序(REST)与调试(CLI)调用。它不决定想什么、何时读、批注写什么 —— 这些归调用方的模型;它只负责**读得到、写得准、不打扰真人**。

## 为什么需要它

微信读书的官方能力现状,决定了「AI 陪真人读同一本书」不能只靠现成 API:

- 官方 **Agent API Gateway**(`i.weread.qq.com/api/agent/gateway`)提供搜索/书架/目录/进度/我的想法/热门划线等**只读查询**,但**不提供正文原文,也没有任何写入接口**;
- 网页版有正文,但正文以密文下发、由前端 JS 解密后渲染,**无法从 DOM 稳定取到**;
- 要在书里「写想法 / 划线 / 评论」,没有官方开放接口,只能走网页登录态;
- 打开阅读器会自动**上报阅读进度**,若由 AI 来翻书,真人进度会被悄悄覆盖。

| 能力需求 | 现成吗 | coweread 的做法 |
| --- | --- | --- |
| 拿任意章节的明文原文 | ❌ 官方不给 | Playwright 抓解密后的正文 + 字符坐标 |
| 把想法/划线挂到**正确的句子**上 | ❌ 无写接口 | 原文逐字定位出字符 range 再写 |
| 查询进度/想法/热门划线 | ✅ 官方有 | 直接走官方网关(稳定优先) |
| 保护真人阅读进度 | ⚠️ 必须防 | 幽灵模式:主动拦截进度上报 |

## 特性

- **章节明文 + 字符级坐标**:未划线的章节也能拿到正文与 `data-wr-co` 字符索引
- **写想法 / 章节想法 / 评论 / 划线(增·改·删)**:自动定位引用文本,`abstract` 必须在原文中**逐字命中**才写入,匹配失败即拒绝 —— 绝不硬发错位坐标
- **查询统一走官方网关**:书架、目录、进度、我的想法、热门划线,稳定优先
- **幽灵模式**:阅读会话内按方法拦截进度上报接口,永不覆盖真人进度
- **AI 共读进度与真人进度分离**:AI 读到哪一章节独立记录在本地
- **署名可配置**:AI 写的批注默认带署名前缀,`COWEREAD_SIGN` 可自定义或留空取消
- **登录态自愈**:写入 cookie 过期时自动续期并重试一次,仍失败才提示重新扫码
- **资源干净**:浏览器按需启动、用完即关,章节文本落盘缓存,空闲零占用
- **三种入口**:CLI / MCP(stdio 与 streamable-http)/ REST(FastAPI)

## 原理 / 架构

三条通道、一个服务层:

| 通道 | 实现 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| 官方查询 | `weread/official.py` | 只读:书架/目录/进度/我的想法/热门划线 | 官方 Agent API Key(`wrk-…`) |
| 原文抓取 | `weread/reader.py` + `weread/coindex.py` | 未划线章节明文 + 字符坐标 | 网页登录态 cookie |
| 写入 | `weread/client.py` | 写想法 / 评论 / 划线(官方网关无写能力) | 网页登录态 cookie |

`service.py` 把三条通道组装成原子操作(如「抓章节 → 定位 → 写入」),`server/mcp.py` 与 `server/api.py` 把它暴露给外部。调用方看到的永远是同一套语义,双通道细节被屏蔽。

### data-wr-co 坐标系(核心原理)

微信读书网页阅读器把正文每个字符包在带坐标的 span 里:

```html
<span data-wr-co="42">他</span><span data-wr-co="43">人</span>…
```

- 坐标是**章节内偏移**,每章从 `0` 重新计数;官方划线的 `bookmarkId` 本身就是 `{bookId}_{chapterUid}_{range}`;
- 想法/划线上报的 `range="start-end"` **左闭右开**,`end = start + 字数`;
- 一个 span 可能含多个字符(常见于标点后跟零宽空格 `​`),解析必须拆成单字符并**跳过零宽字符**(Python 的 `str.strip()` 去不掉 U+200B);
- 定位算法在字符索引里对引用原文**逐字滑窗匹配**,命中返回 `range`,失配返回空 —— 服务层据此拒写。

### 幽灵模式(保护真人进度)

打开阅读器页面会自动同步阅读进度。coweread 在 Playwright 会话里**只按方法拦截 `POST /web/book/read` 并回假应答**(`GET` 取正文必须放行),这样 AI 翻书不会把真人的进度挪走。这是踩过的坑:若只按 URL 路径拦截,`POST` 会漏过去,真实覆盖过一次用户进度。AI 自己读到哪,单独存在本地书签文件,与官方进度完全隔离。

### 登录态与 skey 自愈

写入通道依赖网页登录 cookie(`wr_skey`),有效期约 3 天且**绑定 User-Agent 指纹**(保存与重放必须用同一 UA)。检测到过期(`-2012`)时,coweread 自动开一次官方首页让登录态续期并重试;仍失败才提示重新执行 `coweread login` 扫码。每次浏览器会话结束都会落盘最新登录态到 `state/weread-state.json`。

> 坐标系推导、全部接口清单、翻章细节与踩坑记录见 [docs/TECHNICAL.md](docs/TECHNICAL.md)。

## 仓库布局

```
src/coweread/
  weread/
    official.py    # 官方 Agent Gateway 查询(稳定通道)
    reader.py      # Playwright 正文抓取 + innerHTML hook + 翻章
    coindex.py     # data-wr-co 字符坐标索引 / range 定位
    client.py      # cookie 写通道(想法/评论/划线)
    auth.py        # 扫码登录 + storage_state 持久化
  service.py       # 服务层:三通道组装成原子操作
  server/
    mcp.py         # MCP server(stdio / streamable-http)
    api.py         # REST API(FastAPI)
  cli.py  paths.py
state/             # 登录态、AI 共读书签(gitignore,不入库)
cache/             # 章节文本与坐标缓存(gitignore)
docs/TECHNICAL.md  # 原理与技术细节
tests/
```

## 快速开始

> 需要:**Python 3.11+** 与 [uv](https://docs.astral.sh/uv/)(Windows 安装:`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`)、一个微信读书账号。耗时约 5 分钟。

### 1. 克隆并安装依赖

```bash
git clone https://github.com/IrisNyx/coweread.git
cd coweread
uv sync
uv run playwright install chromium     # 首次安装无头浏览器
```

### 2. 申请官方 API Key

前往微信读书官方入口 **[https://weread.qq.com/r/weread-skills](https://weread.qq.com/r/weread-skills)** 获取你的 API Key(格式 `wrk-xxxx…`)。这是腾讯官方渠道;接口与字段约定见官方仓库 **[Tencent/WeChatReading](https://github.com/Tencent/WeChatReading)**。

> 只打算「扫码抓原文 + 写批注」的 CLI 用法可以暂时跳过;但书架/目录/进度查询,以及把章节明文与书目、写入关联起来的服务层能力,需要这把 key。

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env,填入 WEREAD_API_KEY
```

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `WEREAD_API_KEY` | 查询/服务层必需 | 官方 Agent API Key,`wrk-` 开头 |
| `COWEREAD_SIGN` | 否 | AI 批注署名前缀,默认 `🌕`;留空则不加署名 |
| `COWEREAD_API_TOKEN` | 否 | 暴露 REST / HTTP MCP 时强烈建议设置;设置后所有请求需带 `Authorization: Bearer <token>` |
| `COWEREAD_HOME` | 否 | `state/`、`cache/` 所在根目录,默认项目目录 |

`.env`、`state/`、`cache/` 已在 `.gitignore` 中——登录 cookie 与 API Key 属于凭据,**不要提交、不要外传**。

### 4. 扫码登录(首次)

```bash
uv run coweread login
```

弹出浏览器窗口后用微信 App 扫码授权,登录态自动保存到 `state/weread-state.json`。

### 5. 验证:抓一章节正文

```bash
uv run coweread dump-chapter "https://weread.qq.com/web/reader/<encodeId>"
```

会在 `cache/<encodeId>/` 下写出章节 HTML 与 `chapter_*.co_index.json`(字符坐标索引),并打印明文预览与坐标范围。

## 使用

### CLI

| 命令 | 说明 |
| --- | --- |
| `uv run coweread login` | 扫码登录(默认等 180s,`--timeout` 可调) |
| `uv run coweread dump-chapter <阅读页URL或encodeId>` | 抓当前章节明文 + co 索引 |
| `… dump-chapter <URL> --chapter "章节标题"` | 翻到指定章节再抓(`--no-headless` 可调试观察) |
| `uv run coweread find-range <co_index.json> --text "原文"` | 在已缓存索引里定位某段原文的 range |
| `uv run coweread mcp` | 启动 MCP server(stdio) |
| `uv run coweread mcp --http --port 8473 --token XXX` | 启动 MCP streamable-http,建议带 token |
| `uv run coweread serve --port 8480` | 启动 REST API |

Windows 下可直接双击仓库里的 `login.bat` / `serve.bat`(已处理控制台编码)。

### MCP(供 AI 调用,16 个工具)

| 分组 | 工具 | 说明 |
| --- | --- | --- |
| 查询 | `get_shelf` / `get_toc` / `get_progress` | 书架 / 章节目录 / 真实阅读进度(官方通道) |
| 原文 | `get_chapter_text(book_id, chapter_uid, refresh?)` | 章节明文 + 坐标索引(缓存优先) |
| 写想法 | `add_review(book_id, chapter_uid, abstract, content)` | 引句想法:abstract 逐字摘自原文,自动算 range,匹配失败拒绝 |
| 写想法 | `add_chapter_review(book_id, chapter_uid, content)` | 章节级想法(挂在整章上,无引句) |
| 评论 | `list_comments(review_id)` / `add_comment(review_id, content, reply_comment_id?)` | 某条想法下的评论;`reply_comment_id` 为引用式回复 |
| 划线 | `add_bookmark` / `update_bookmark_style` / `remove_bookmark` | 划线增改删;默认 `colorStyle=5, style=2`(黄波浪) |
| 防重 | `my_reviews(book_id)` / `best_bookmarks(book_id, chapter_uid?)` | 已有想法 / 热门划线,写前防重复、取素材 |
| 进度 | `get_reader_bookmark` / `set_reader_bookmark` | AI 自己的共读进度(本地,与真人进度无关) |
| 打包 | `reading_context(book_id, chapter_uid)` | 一次打包:进度+书签+明文+已有想法+热门划线(便捷用,不作默认上下文) |

划线样式编号(手机端实测):颜色 `colorStyle` = 1红 / 2紫 / 3蓝 / 4绿 / 5黄(0/6/7 无效);线型 `style` = 0直线 / 1荧光笔 / 2波浪(3 无效)。

一次典型共读调用序列:

1. `get_shelf` → 选中书,拿到 `bookId`;
2. `get_toc(bookId)` → 拿到当前想读章节的 `chapterUid`;
3. `get_chapter_text(bookId, chapterUid)` → 读明文,想批注的句子逐字摘作 `abstract`;
4. `add_review(bookId, chapterUid, abstract, content)` → 想法自动挂在正确句子;
5. `set_reader_bookmark(bookId, chapterUid)` → 记住 AI 读到哪(与真人进度分开)。

### REST API(16 个能力)

| 方法 | 端点 | 说明 |
| --- | --- | --- |
| GET | `/api/shelf` | 书架 |
| GET | `/api/toc/{book_id}` | 章节目录 |
| GET | `/api/progress/{book_id}` | 真实阅读进度 |
| GET | `/api/chapter/{book_id}/{chapter_uid}?refresh=` | 章节明文 + 坐标 |
| POST | `/api/review` | 写想法 `{bookId, chapterUid, abstract, content, isPrivate?, sign?}` |
| POST | `/api/chapter_review` | 写章节想法 |
| GET | `/api/reviews/{book_id}` | 我的想法 |
| GET | `/api/best_bookmarks/{book_id}?chapterUid=` | 热门划线 |
| GET | `/api/comments/{review_id}` | 想法下的评论 |
| POST | `/api/comment` | 发评论 `{reviewId, content, replyCommentId?}` |
| POST | `/api/bookmark` | 划线 `{bookId, chapterUid, abstract, colorStyle?, style?}` |
| PATCH | `/api/bookmark/{bookmark_id}` | 改划线样式 |
| DELETE | `/api/bookmark/{bookmark_id}` | 删划线 |
| GET/POST | `/api/reader_bookmark` | 读/记 AI 共读进度 |
| GET | `/api/reading_context/{book_id}/{chapter_uid}` | 便捷打包 |

设置 `COWEREAD_API_TOKEN` 后,请求需带 `Authorization: Bearer <token>`(否则 401)。示例:

```bash
uv run coweread serve --port 8480 &
curl -H "Authorization: Bearer $COWEREAD_API_TOKEN" \
  http://127.0.0.1:8480/api/progress/<book_id>
```

## 接入 AI(MCP 客户端配置)

stdio(本机,MCP 客户端以子进程方式运行):

```json
{
  "mcpServers": {
    "coweread": {
      "command": "uv",
      "args": ["run", "coweread", "mcp"],
      "cwd": "/你的/coweread/目录",
      "env": { "WEREAD_API_KEY": "wrk-xxx" }
    }
  }
}
```

远程接入用 streamable-http:

```bash
uv run coweread mcp --http --port 8473 --token 一串随机token
```

> ⚠️ HTTP 模式**务必配 token**:该服务能读写你的微信读书账号,裸奔在可达端口上等于把账号交给访问者。token 只建议通过加密通道/内网传递,不要走明文外发。

## 已知边界

- `abstract` 在一章内出现多次时,写入会挂在**第一处**;写前建议先 `my_reviews` / 取上下文核对,避免重复。
- `abstract` 必须与原文**逐字一致**才写(空格、换行也要对得上)。定位失败时 coweread 拒绝写入并返回明确错误,不会硬发错位坐标。
- 章节文本缓存默认长期有效;书更新版本后缓存会陈旧,需要 `refresh=True` / `--refresh` 强刷。
- MCP 并发调用会各自开浏览器实例(无锁串行化),低并发无碍,但别开太多并行请求。
- 正文抓取依赖微信读书前端把解密正文注入 `#preRenderContent` 的机制及目录面板的 DOM 结构,**前端改版需要同步适配**;失效时会显式报错,不会静默返回错内容。
- 评论是**平铺列表**,无嵌套「楼中楼」;`reply_comment_id` 只是「回复某人」的引用标记。
- 官方「我的划线」回查**不回显** `style` 字段(只回显 `colorStyle`)。
- 写入 cookie 约 3 天过期;单测过、线上以「自动续期重试一次 → 失败才让用户重扫码」兜底,但极低概率仍需人工介入。
- 各入口的 `add_bookmark` 默认线型不完全一致(CLI/MCP 默认黄波浪 `style=2`,REST 默认值不同):想画特定样式请**显式传 `colorStyle`/`style`**。

## 安全与免责

- **非腾讯官方项目**。由作者个人自用需求驱动并开源;请自行遵守微信读书用户协议与官方 Skills 使用条款。**建议个人低频自用**,涉及网页私有接口与写操作的自动化,风控后果自担。
- 不要把 `state/`、`.env`、`cache/` 里的登录态、API Key、书摘数据提交或发送给任何第三方。
- 本项目不含 LLM,也不会把你的书摘上传到任何非微信读书的服务。

## License

[MIT](LICENSE) © IrisNyx

官方查询能力基于腾讯 [WeChatReading](https://github.com/Tencent/WeChatReading)(Apache-2.0)公开的 Agent Skills 接口约定实现。
