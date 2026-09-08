# coweread 技术文档

面向想理解原理、二次开发或排查故障的开发者。所有结论均来自实测（2026-08 / 2026-09），实测手段：微信读书网页端行为分析 + 官方接口反查比对。

## 总体架构

coweread 是确定性工具层（不含 LLM），批注内容由调用方（LLM/前端）生成。内部三条通道：

| 通道 | 实现 | 用途 | 鉴权 |
| --- | --- | --- | --- |
| 官方查询 | `weread/official.py` | 一切只读查询：书架/目录/进度/我的想法/我的划线/热门划线 | 官方 wrk key |
| 原文抓取 | `weread/reader.py` + `weread/coindex.py` | 未划线章节明文 + `data-wr-co` 坐标索引（官方接口不提供原文） | cookie（storage_state） |
| 写入 | `weread/client.py` | 写想法 / 评论 / 划线（官方 API 只读，写不了） | cookie（storage_state） |

`service.py` 组装三条通道成原子操作，`server/mcp.py` 暴露为 MCP 工具（stdio / streamable-http 双传输）。

## data-wr-co 坐标系（核心原理）

微信读书网页阅读器把正文每个字符包在一个 span 里，`data-wr-co` 是字符级坐标：

```html
<span data-wr-co="42">他</span><span data-wr-co="43">人</span>...
```

实测结论：

1. **坐标是章节内偏移**，每章从 0 重新计数（不是全书绝对偏移）。证据：不同章节的划线 range 均为千级小数字；官方 `bookmarkId` 本身就是 `{bookId}_{chapterUid}_{range}` 格式。
2. **`range="start-end"` 左闭右开**，`end = start + 字符数`。实测：46 字的划线 range 为 `1000-1046`，逐字吻合。
3. **一个 span 可能含多个字符**（常见于标点后跟零宽空格 `\u200b`），解析时必须拆成单字符并**跳过零宽字符**。注意 Python 的 `str.strip()` 去不掉 U+200B（它的 `isspace()` 为 False），必须显式建集合过滤——本项目踩过这个坑。
4. 官方坐标**把空格等不可见字符也计入**，因此划线的 `markText`（规范化显示文本）字符数可能小于 range span，长度校验只能当近似参考。
5. 章节**级**想法（不挂具体句子）用 `range="0-0"`。
6. 正文密文传输、由前端 JS 解密后经 `innerHTML` 注入 `#preRenderContent`。因此抓明文必须开真浏览器 hook `Element.prototype.innerHTML` 的 setter（见下文"原文抓取"）。

### range 定位算法（coindex.py）

- `parse_co_index(html)`：正则提取全部 `(co, 字符)` 对，跳过零宽字符，得到坐标索引。
- `find_range_in_co_index(co_index, abstract)`：滑窗逐字匹配原文，命中返回 `"start-end"`，失配返回 `None`。**调用方必须处理失配**——coweread 的策略是拒绝写入并报错，绝不硬发错位坐标。

## 接口清单

### A. 官方 Agent Gateway（只读查询）

```
POST https://i.weread.qq.com/api/agent/gateway
Headers: Authorization: Bearer wrk-xxx
Body:    {"api_name": "...", "skill_version": "1.0.4", ...参数平铺}
```

| api_name | 参数 | 返回要点 |
| --- | --- | --- |
| `/shelf/sync` | 无 | 书架 |
| `/book/info` | `bookId` | 书籍信息（`deepLink` 的 `v` 参数可作网页阅读器 encodeId） |
| `/book/chapterinfo` | `bookId` | `chapters[]`：chapterIdx / chapterUid / title |
| `/book/getprogress` | `bookId` | `book`：真实阅读进度（progress / chapterUid） |
| `/review/list/mine` | `bookid`（**小写 b！**）, `synckey`, `count` | `reviews[]`，每条为 `{reviewId, review: {...}}` 嵌套 |
| `/book/bookmarklist` | `bookId` | 自己的划线在 **`updated` 字段**（不是 bookmarks） |
| `/book/bestbookmarks` | `bookId`, `chapterUid`（0=全书） | `items[]`：热门划线 |
| `/book/underlines` | `bookId`, `chapterUid` | 划线热度分布 |

- `skill_version` 过期时官方返回 `errcode 99` + `upgrade_info.latest_version`，同步常量即可。
- 官方通道**没有任何写能力**，也没有未划线原文。

### B. Web cookie 通道（写入 + 评论）

依赖网页版登录态 cookie（`wr_vid` + `wr_skey`）。注意 **`wr_skey` 绑定 UA 指纹**：保存和重放登录态时必须使用同一 User-Agent，否则服务端判定未登录。

| 端点 | 方法 | payload | 返回 |
| --- | --- | --- | --- |
| `/web/review/add` | POST | `{bookId, chapterUid, content, type:1, isPrivate:1, abstract, range}` | `{errCode:0, reviewId, createTime}`；`-2012`=登录超时 |
| `/web/review/commentloadmore` | GET | `?reviewId=&maxIdx=0&count=100&isExpandAll=true` | 平铺评论列表（无嵌套楼层） |
| `/web/review/comment` | POST | `{reviewId, content, commentId?}` | 新 `commentId`；带 `commentId` 即"回复某条"（引用模式，非楼中楼） |
| `/web/book/addBookmark` | POST | `{bookId, chapterUid, range, markText, colorStyle, style, type:1}` | `{bookmarkId}`。**`markText` 必须 Base64 编码（UTF-8）** |
| `/web/book/updateBookmark` | POST | 未实测，字段同名 | 存在此端点，可改样式 |
| `/web/book/removeBookmark` | POST | `{bookmarkId}` | `{succ:1}` |

划线样式编号表（手机端逐一样式实认）：

- `colorStyle`（颜色）：`1` 红 / `2` 紫 / `3` 蓝 / `4` 绿 / `5` 黄；`0`、`6`、`7` 无效（不渲染）
- `style`（线型）：`0` 直线 / `1` 荧光笔 / `2` 波浪（`3` 无效，不渲染）
- 官方 `bookmarklist` 回查**不回显 `style`**，只回显 `colorStyle`。
- ⚠️ 线型对照务必**单变量实验**（同色只变 style，逐条目认）：早期实验曾因双变量 + 目击串行得出错误映射（1/2 记反），2026-09-09 用同色三线对照定案。

## 原文抓取（reader.py）

微信读书网页端正文密文下发、前端解密后以 `innerHTML` 注入 `#preRenderContent`。抓取方案：

1. Playwright 打开阅读器页（`https://weread.qq.com/web/reader/{encodeId}`）；
2. `add_init_script` 在页面加载前 hook `Element.prototype.innerHTML` 的 **setter**，过滤 id/class 含 `preRenderContent` 且长度 >100 的赋值，把解密后的完整 HTML 存入 `window.__coweread_caps`；
3. 从页面读回捕获结果。

### 幽灵模式（保护真实进度）

打开阅读器会触发阅读进度同步，覆盖用户真实进度。对策：路由层拦截 **`POST /web/book/read`** 返回假应答 `{"errCode":0}`。

> 踩坑记录：`/web/book/read` 的 **GET 是拉取章节内容**（必须放行，否则渲染不出正文），**POST 是上报进度**（必须拦截）。只按 URL 路径拦截会把 POST 放过去——我们因此真实污染过一次用户进度，教训是**按方法+路径拦截**。

### 翻章

- `k{n}` 形式的章节深链实测 **404**，不可用；
- 正确姿势：点击阅读器内目录按钮（`.readerControls_item.catalog`），面板内章节项为 `li.readerCatalog_list_item`，按官方章节标题精确匹配后点击；
- **必须用 Playwright 真实点击**（完整鼠标事件序列）；页面 JS 里 `el.click()` 不触发路由跳转。
- 翻章后新章节正文仍走 `preRenderContent` 的 innerHTML 注入，hook 照常命中。

## skey 过期自愈

`wr_skey` 约 3 天过期。实测发现：带着仍有效的完整登录态开一次微信读书页面，前端会在几秒内自动续期 `wr_skey`，`storage_state` 存回即可续命。coweread 在写操作收到 `-2012` 时自动执行"开页面续期 → 重试一次"，连续失败才向上抛错要求人工扫码。

## 资源策略

- 浏览器按需启动、用完即关，空闲时零占用；
- 章节 co-index 落盘缓存（`cache/chapters/{bookId}_{chapterUid}.json`），同一章只开一次浏览器；`refresh=True` 强刷（书籍更新版本后缓存会陈旧）；
- bookId → encodeId 映射也落盘缓存。

## 已知边界

- abstract 在一章内出现多次时，想法挂在第一处；
- MCP 并发调用会开多个浏览器实例（无锁串行化未做，低风险）；
- hook 依赖 `#preRenderContent` 结构与目录/笔记面板类名，前端改版需同步更新（失效时报错明确，不会静默错）；
- 评论为平铺列表，无嵌套楼中楼（`commentId` 参数是引用回复标记）；
- `updateBookmark` 端点存在但未实测。
