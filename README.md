# 机票价格追踪（网页版，全部免费）

手机浏览器打开的网页：每天自动查你设置的航线价格，记录走势，低于目标价时给你发邮件。
航线、目标价、筛选条件都在网页里改，不用改代码。

- 价格数据：SerpApi（Google Flights 数据）
- 自动运行：GitHub Actions（每天一次）
- 网页托管：GitHub Pages
- 邮件：Gmail
- 签证：按**中国护照**，用公开数据集（passport-index）判断，仅供参考
- 托运行李：根据价格数据里的文字推测，经常是“未知”，订票前请自己确认

---

## 一、一次性设置（约 20 分钟）

### 1. 注册 GitHub，建仓库
1. 到 github.com 注册免费账号。
2. 右上角 **+ → New repository**，名字例如 `flights`，选 **Public**（免费的网页托管要求公开），点 Create。
   - 注意：公开仓库里别人能看到你追踪的航线和价格，但**看不到**你的邮箱和密钥。

### 2. 上传文件
1. 解压我发给你的 zip。
2. 在仓库页面点 **Add file → Upload files**，把解压出来的 `docs`、`scripts`、`tests` 三个文件夹和 `README.md` 拖进去，点 Commit changes。
3. 再点 **Add file → Create new file**，文件名栏里输入：`.github/workflows/check.yml`
   （输入 `/` 会自动变成文件夹），把 zip 里 `workflow-check.yml` 的内容整个复制进去，点 Commit changes。
   - 原因：以点开头的文件夹拖拽上传经常丢失，所以这个文件手动建。

### 3. 开启网页
仓库 **Settings → Pages → Build and deployment**：Source 选 **Deploy from a branch**，Branch 选 `main`，文件夹选 `/docs`，Save。
1–2 分钟后网页地址是：`https://你的用户名.github.io/flights/`

### 4. 价格数据密钥（SerpApi）
1. 到 serpapi.com 注册免费账号，复制 API key。
2. 免费额度每月有限，**请在他们网站上确认当前额度**，我没法核实。
3. 仓库 **Settings → Secrets and variables → Actions → New repository secret**，名字 `SERPAPI_KEY`，值粘贴密钥。

### 5. 邮件提醒（Gmail）
1. Google 账号开启“两步验证”，然后到 myaccount.google.com/apppasswords 创建一个“应用专用密码”（16 位）。
2. 同样位置再添加两个 secret：
   - `SMTP_USER`：你的 Gmail 地址
   - `SMTP_PASS`：刚才的 16 位应用专用密码（不是登录密码）
   - （可选）`ALERT_TO`：想接收提醒的邮箱，不填则发给 SMTP_USER 自己

### 6. 让网页能修改航线（令牌）
1. GitHub 右上角头像 → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**。
2. Repository access 选 **Only select repositories**，只选 `flights`。
3. Permissions 里把 **Contents** 和 **Actions** 都设为 **Read and write**。
4. 生成后复制令牌（只显示一次）。到网页右上角 **设置**，粘贴到“GitHub 令牌”。令牌只保存在这台手机的浏览器里。
5. 令牌最长一年有效，到期要重新生成。

---

## 二、日常使用
- **添加航线**：点“添加航线”，填机场代码、日期、目标价。
- **立即检查**：点右上角，约 1–3 分钟后刷新页面。
- **筛选**：顶部三个开关——无需提前签证（免签/落地签）、含托运行李、仅直飞。
- **提醒规则**：最低价低于目标价时发邮件；同一价格 7 天内不重复发，更低会再发。
  每条航线可勾选“只算含托运行李的票”“需要提前办签证的目的地不提醒”。
- **每天查询次数**（设置里）：所有航线轮流查，次数越少越省免费额度。

## 三、需要知道的限制
1. 免费额度：SerpApi 每月有限。航线多就把每天查询次数调低。
2. 托运行李：很多报价没写明，会显示“托运未知”。这个筛选宁可漏掉也不会把“未知”当成“含托运”。
3. 签证：来自公开数据集，可能不是最新，e-visa / ETA 算“需要提前申请”。出发前务必看官方信息。
4. 这个网页追踪的是**固定日期**的价格走势，不是“某个月最便宜的一天”。
5. GitHub 对长期没有活动的仓库会停掉定时任务；本程序每天会提交一次数据，所以通常不会触发。
6. 如果每日检查失败，GitHub 会给你发邮件；可以在仓库 **Actions** 页面看日志。

## 四、文件说明
- `docs/` 网页本身，以及 `routes.json`（你的航线）、`data/prices.json`（价格历史）
- `scripts/check_prices.py` 每天运行的检查程序（只用 Python 标准库）
- `.github/workflows/check.yml` 定时任务
- `tests/` 自带测试
