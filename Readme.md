# MoviePilotV2 自动化部署

<p align="center">
  <img src="moviepilotv2-logo.png" alt="MoviePilotV2 自动化部署" width="760">
</p>

## 仓库简介

NAS 上搭 MoviePilot + qB + Emby 这一套，**手动部署**往往要在多个容器、目录、Web 页面之间来回折腾；**某宝某鱼**找人代部署通常收费不低。本仓库用脚本把部署与初始化串成一条流水线，尽量做到**少人工介入**。

**主要功能：**

- **一键部署媒体栈**：生成 Docker Compose、`.env` 与目录结构，启动 MoviePilot、双 qB、Emby、ChineseSubFinder 等容器
- **组件自动初始化**：依次完成 qB WebUI、Emby 媒体库、MoviePilot 下载器/规则/分类、ChineseSubFinder 配置
- **预置目录与整理规则**：下载区与媒体库按类型、地区划分，MoviePilot 自动分类并整理入库
- **Emby 开箱即用**：批量建库、首页排序、库选项同步；挂载仅暴露最终媒体目录，不暴露下载临时目录
- **部署前检查与权限**：Docker / Compose 环境校验、统一密码规则、目录权限修正

适合：**新搭一套 MoviePilot V2 媒体栈**、希望少手工配置的场景。**旧库迁移不在本仓库范围内**。目录与规则细节见下文「目录与规则速查」。

**推荐顺序**：§1 准备 → §3 生成目录 → §4 启容器 → §5 网页向导 → §6 写 `.env` → §7 init-qb → §8 init-emby → §9 init-mpv2 → §10 init-csf → §13 验证 → §15 插件（可选）

以下路径默认根目录为 `/volume1/media-data`；Emby 媒体库默认按**二级地区**建库。

## 目录与规则速查

### 1. 安装脚本创建的文件夹

`python3 mpv2-install.py` 会创建下载目录与媒体库目录（`--init-mpv2` 还会补建部分媒体子目录）。

**下载目录**


| 路径                  | 用途           | MoviePilot | qB                    |
| ------------------- | ----------------- | ---------- | --------------------- |
| `downloads/media`   | 日常订阅/搜索下载 | 整理         | qB-media 默认分类         |
| `downloads/brush`   | 刷流保种          | 不整理        | qB-brush 默认分类         |
| `downloads/manual`  | 手动/临时下载     | 整理         | qB-media 分类 `manual`  |
| `downloads/private` | 私密视频暂存      | 不处理        | qB-media 分类 `private` |


**媒体库目录**


| 路径                   | 说明                       |
| ----------------------- | ------------------------ |
| `media/真人电影/{地区}` | 地区：大陆、港澳台、日韩、欧美、东南亚、其他地区 |
| `media/真人剧集/{地区}` | 同上                       |
| `media/动漫电影/{地区}` | 同上                       |
| `media/动漫剧集/{地区}` | 同上                       |
| `media/综艺/{地区}`   | 同上                       |
| `media/纪录片/{地区}`  | 同上                       |
| `media/短剧`        | 一级目录，无地区子目录              |
| `media/小电影`       | 一级目录，手动维护，MoviePilot 不处理 |
| `media/私享影库/{地区}` | 地区：国产、日韩、欧美、其他地区         |
| `media/未分类/剧集`    | MoviePilot 兜底目录（脚本补建）    |


> `downloads/private`、`media/小电影` 面向非常规发行影片，所以无法自动刮削整理，需自行整理入库；也可搜索或自行实现第三方插件实现对非常规影片的刮削整理，刮削逻辑也比较简单，扫描目录，通过ffmpeg生成缩略图，有条件的通过AI，没条件的生成简单媒体信息转移入库即可。

### 2. Emby 媒体库与文件夹

`python3 mpv2-install.py --init-emby` 默认创建以下媒体库（可在 `.env` 用 `EMBY_LIBRARY_MODE=primary` 切回一级建库）。

**按地区拆分的库（电视节目 / 电影）**


| Emby 媒体库  | 类型   | 对应文件夹             |
| --------------- | ---- | ----------------- |
| 真人电影-{地区} | 电影   | `media/真人电影/{地区}` |
| 动漫电影-{地区} | 电影   | `media/动漫电影/{地区}` |
| 真人剧集-{地区} | 电视节目 | `media/真人剧集/{地区}` |
| 动漫剧集-{地区} | 电视节目 | `media/动漫剧集/{地区}` |
| 综艺-{地区}   | 电视节目 | `media/综艺/{地区}`   |
| 纪录片-{地区}  | 电视节目 | `media/纪录片/{地区}`  |


**一级库（无地区子目录）**


| Emby 媒体库 | 类型   | 对应文件夹       |
| -------- | ---- | ----------- |
| 短剧       | 电视节目 | `media/短剧`  |
| 小电影      | 电影   | `media/小电影` |


**私享影库（建议单独做账号权限）**


| Emby 媒体库       | 类型  | 对应文件夹             |
| ----------------- | --- | ----------------- |
| 私享影库-国产     | 电影  | `media/私享影库/国产`   |
| 私享影库-日韩     | 电影  | `media/私享影库/日韩`   |
| 私享影库-欧美     | 电影  | `media/私享影库/欧美`   |
| 私享影库-其他地区 | 电影  | `media/私享影库/其他地区` |


### 3. MoviePilot 转移规则

`python3 mpv2-install.py --init-mpv2` 写入的整理策略摘要如下。

**全局 Transfer 设置**


| 项目               | 默认值                                  |
| ------------------ | ------------------------------------ |
| 整理方式           | 移动（`move`）                           |
| 参与整理的资源目录 | `downloads/media`、`downloads/manual` |
| 排除目录           | `downloads/brush`                    |
| 不处理             | `downloads/private`                  |


**目录规则（资源目录 → 媒体库）**

每条带地区的分类均有两套规则：`downloads/media`（下载器监控）和 `downloads/manual`（手动目录监控，规则名前缀 `手动-`）。


| 分类        | 媒体类型 | 目标路径                                      |
| --------- | ---- | ----------------------------------------- |
| 真人电影-{地区} | 电影   | `media/真人电影/{地区}`                         |
| 动漫电影-{地区} | 电影   | `media/动漫电影/{地区}`                         |
| 真人剧集-{地区} | 电视剧  | `media/真人剧集/{地区}`                         |
| 动漫剧集-{地区} | 电视剧  | `media/动漫剧集/{地区}`                         |
| 综艺-{地区}   | 电视剧  | `media/综艺/{地区}`                           |
| 纪录片-{地区}  | 电视剧  | `media/纪录片/{地区}`                          |
| 短剧        | 电视剧  | `media/短剧`                                |
| 私享影库/{地区} | 电影   | `media/私享影库/{地区}`（TMDB `adult=true` 自动匹配） |


**兜底规则**


| 规则            | 目标路径            |
| ------------- | --------------- |
| 未分类剧集         | `media/未分类/剧集`  |
| 私享影库电影-日韩-未识别 | `media/私享影库/日韩` |


**不在 MoviePilot 规则内**

- `media/小电影`：无对应转移规则
- `downloads/private`：不参与整理

---

## 1. 前置准备（NAS）

### 1.1 网络环境

部署前确认 NAS 网络环境满足：

1. **上网环境**：能稳定访问 Docker Hub、GitHub、TMDB 等海外服务（镜像拉取、MoviePilot 插件/索引、刮削元数据均依赖外网）。
2. **可拉取 Docker Hub 镜像**：Container Manager / Docker 已配置可用镜像源或代理，能正常 `docker pull`（首次 `docker compose up` 需下载多个镜像）。
> 上网环境改造可通过nas安装openwrt虚拟机，虚拟机安装服务插件，然后将nas网关设置为虚拟机IP，这样通过旁路由方式实现上网环境改造

### 1.2 共享目录

先在 NAS 系统里创建 1 个共享文件夹，作为媒体数据根目录。

建议：

- 共享文件夹名：`media-data`
- 挂载路径：`/volume1/media-data`

该目录会存放：

- 下载目录：`/volume1/media-data/downloads`
- 最终媒体库：`/volume1/media-data/media`

### 1.3 Docker 支持

在 NAS 上确认：

1. Docker / Container Manager 可用，且当前用户可执行 `docker`（脚本启动时会自动检查 Docker 权限与 Compose 支持）。
2. 有可执行 Python3 环境。
3. 当前用户对 `/volume1/docker` 和 `/volume1/media-data` 有读写权限。

建议目录：

```text
/volume1/docker/media-stack
/volume1/media-data
```

## 2. 脚本说明

进入脚本目录：

```bash
cd /path/to/mpv2/install
```

当前脚本：

- `mpv2-install.py`
- `init-qb.py`
- `init-emby.py`
- `init-mpv2.py`
- `init-csf.py`

插件配置文档（见 §15，需在插件市场安装后手工填写）：

```text
插件配置/
├── 站点认证及站点添加.md
├── ChineseSubFinder插件配置.md
├── 目录实时监控插件配置.md
├── 媒体库刮削插件配置.md
└── 站点刷流插件配置.md
```

### 2.1 安装脚本参数表

`mpv2-install.py` 常用参数如下：


| 参数                      | 说明                                     | 默认值                           |
| ----------------------- | -------------------------------------- | ----------------------------- |
| `--stack-dir`           | `docker-compose.yml` 与服务配置目录           | `/volume1/docker/media-stack` |
| `--data-dir`            | 下载与媒体库根目录                              | `/volume1/media-data`         |
| `--puid`                | 容器运行用户 UID                             | 当前用户 UID                      |
| `--pgid`                | 容器运行用户 GID                             | 当前用户 GID                      |
| `--moviepilot-user`     | MoviePilot 管理员用户名                      | `admin`                       |
| `--password`            | 统一设置 `MoviePilot/qB-media/qB-brush/CSF` 密码 | 无（随机或由单独参数决定）                 |
| `--moviepilot-password` | 单独设置 MoviePilot 密码                     | 随机                            |
| `--qb-media-password`   | 单独设置 qB-media WebUI 密码                 | 随机                            |
| `--qb-brush-password`   | 单独设置 qB-brush WebUI 密码                 | 随机                            |
| `--github-token`        | 写入 `.env` 的 GitHub Token（插件/资源访问）      | 空                             |
| `--postgres-db`         | PostgreSQL 数据库名                        | `moviepilotv2`                |
| `--postgres-user`       | PostgreSQL 用户名                         | `moviepilotv2`                |
| `--postgres-password`   | PostgreSQL 密码                          | 随机                            |
| `--redis-password`      | Redis 密码                               | 随机                            |
| `--force`               | 覆盖已有 `.env` 和 `docker-compose.yml`     | 关闭                            |
| `--stop`                | 仅停止容器                                  | 关闭                            |
| `--clean`               | 停止容器并清理脚本生成目录/下载目录内容                   | 关闭                            |
| `--init-qb`             | 初始化两个 qB 实例                            | 关闭                            |
| `--init-emby`           | 初始化 Emby 媒体库与 API Key                  | 关闭                            |
| `--init-mpv2`           | 初始化 MoviePilot 下载器/目录/规则               | 关闭                            |
| `--init-csf`            | 写入 ChineseSubFinder 配置并重启容器             | 关闭                            |
| `--csf-user`            | ChineseSubFinder WebUI 用户名                  | `admin`                       |
| `--csf-password`        | ChineseSubFinder WebUI 密码（`A-Za-z0-9!@#%-*`） | 随机（或沿用 `--password`）         |
| `--host-ip`             | 初始化脚本访问 NAS 服务的局域网 IP（见 §2.3）          | 自动探测                          |


注意：

- `--password` 与 `--moviepilot-password/--qb-media-password/--qb-brush-password/--csf-password` 互斥。
- 所有脚本管理的密码（含 `--postgres-password` / `--redis-password`）统一规则：仅英文大小写、数字及 `!@#%-*`（不支持 `_`、`$` 等符号）；校验逻辑见 `password_utils.py`。
- `--init-qb`、`--init-emby`、`--init-mpv2`、`--init-csf` 前需先 `docker compose up -d`，且容器处于运行状态。
- `--init-mpv2` 前需先完成 `--init-qb` 和 `--init-emby`，且 `.env` 中 `QB_INITIALED=true`、`EMBY_INITIALED=true`。
- NAS 上建议用 **root** 执行安装脚本，以便正确设置目录所有者（`PUID/PGID`）；非 root 时可能只能改权限、不能 `chown`。

### 2.2 `--github-token` 说明

MoviePilot 安装插件、拉取 GitHub 上的规则/资源时会访问 GitHub API。未配置 Token 时容易触发**匿名 API 限流**，表现为插件市场加载失败、索引更新超时等。
> 获取方式: GitHub → Settings → Developer settings → Personal access tokens → 生成 **classic** token；勾选 `public_repo`（只读公开仓库一般够用）
> 可以通过--github-token将token写入.env文件，由脚本自行填充到moviepilot设置中，也可自行在moviepilot中设置

示例：

```bash
# 初次安装时一并写入
python3 mpv2-install.py --password 'YourStrongPassword' --github-token 'ghp_xxxxxxxx'

# 已安装后补写：编辑 .env 增加 GITHUB_TOKEN=...，再重新初始化 MoviePilot
python3 mpv2-install.py --init-mpv2
```

### 2.3 `--host-ip` 说明

`--host-ip` 只在 `**--init-qb` / `--init-emby` / `--init-mpv2**` 时使用；初次生成目录（§3）不需要。

脚本在**宿主机**上通过「NAS 局域网 IP + 端口映射」访问各服务 API，与容器内 Docker 网络地址（如 `qb-media:7097`）不同。

> 未指定设备内网IP时，脚本会自行探测并输出探测结果，如果探测失败请通过--host-ip指定

> **注意**：`--host-ip` 只影响**初始化脚本**怎么连 API；MoviePilot 连接 qB/Emby 走的是 Docker 内网地址（`.env` 里 `MP_QB_MEDIA_HOST=http://qb-media:7097` 等），与 `--host-ip` 无关。

## 3. 一键生成目录与 compose

执行初始化（会生成目录、`.env`、`docker-compose.yml`）：

```bash
# 方式1：统一设置 qb-media、qb-brush、MoviePilot 的密码；用户名统一为admin（推荐）；
python3 mpv2-install.py --password 'YourStrongPassword'

# 方式2：用户名统一为admin，分别设置密码
python3 mpv2-install.py \
  --moviepilot-user admin \
  --moviepilot-password 'MpPassword' \
  --qb-media-password 'QbMediaPassword' \
  --qb-brush-password 'QbBrushPassword'
```

如需指定路径：

```bash
python3 mpv2-install.py \
  --stack-dir /volume1/docker/media-stack \
  --data-dir /volume1/media-data \
  --moviepilot-user admin \
  --moviepilot-password 'MpPassword' \
  --qb-media-password 'QbMediaPassword' \
  --qb-brush-password 'QbBrushPassword'
```

说明：

- `--password` 只统一设置密码，用户名统一为 admin；同时写入 CSF 密码。
- MoviePilot 用户名通过 `--moviepilot-user` 设置（默认 `admin`）。
- `--password` 不能和 `--moviepilot-password/--qb-media-password/--qb-brush-password/--csf-password` 同时使用；所有密码参数均须符合统一字符规则（`A-Za-z0-9!@#%-*`）。
- 首次安装若计划使用插件市场，可加上 `--github-token`（见 §2.2）。

## 4. 启动容器

```bash
cd /volume1/docker/media-stack
docker compose up -d
```

启动后服务包括：

| 服务     | 作用                                                       |
| ---------- | ---------------------------------------------------------- |
| MoviePilot | 媒体栈中枢：订阅/搜索、资源识别、元数据刮削、文件整理入库，并同步 Emby |
| PostgreSQL | MoviePilot V2 后端数据库，保存用户配置、订阅、站点、历史记录等 |
| Redis   | MoviePilot 缓存与任务队列，支撑后台调度与状态存储 |
| Emby | 媒体服务器：刮削展示、播放、权限与首页库排序 |
| qB-media | 日常订阅与搜索下载的 BT 客户端（对应 `downloads/media` 等目录） |
| qB-brush | 刷流保种专用 BT 客户端，与 qB-media 隔离，下载内容不参与自动整理 |
| ChineseSubFinder | 扫描 `media` 目录，自动下载/匹配中文字幕，并可联动 Emby 补字幕 |

## 5. 首次网页初始化
只需要对emby做webui的手动初始化；

### 5.1 Emby

先内网打开`http://NAS-IP:7096` Emby webui 网页 选择语言等完成首次向导并创建管理员账号：


## 6. 配置 `.env` 关键项

编辑：

```text
/volume1/docker/media-stack/.env
```

**在 `--init-emby` 之前**，必须写入 Emby 管理员账号和密码（§5.1 向导里创建的）：

```text
EMBY_USER=admin
EMBY_PASSWORD=EmbyPassword
```

## 7. 初始化 qB

容器运行中执行：

```bash
python3 mpv2-install.py --init-qb
```

说明：

- 首次启动从容器日志读取 qB 临时密码并完成 WebUI 初始化。
- 若日志中已无临时密码，会回退使用 `.env` 中的 `QB_MEDIA_PASSWORD` / `QB_BRUSH_PASSWORD`；能登录则视为已初始化，**只更新配置、不改密码**。

完成后会写入：

```text
QB_INITIALED=true
QB_MEDIA_API_KEY=...
QB_BRUSH_API_KEY=...
```

## 8. 初始化 Emby 媒体库与 API Key

确认 `.env` 已配置 `EMBY_USER`、`EMBY_PASSWORD` 后执行：

```bash
python3 mpv2-install.py --init-emby
```

脚本会按固定顺序创建二级媒体库，并为 **所有 Emby 用户** 写入首页排序：

```text
真人电影{地区} → 真人剧集{地区} → 动漫电影{地区} → 动漫剧集{地区}
→ 综艺{地区} → 纪录片{地区} → 私享影库{地区} → 短剧 → 小电影
```

地区顺序：`大陆 → 港澳台 → 日韩 → 欧美 → 东南亚 → 其他地区`（可通过 `.env` 的 `EMBY_REGIONS` 调整列表，排序仍按此优先级）。

同时会为脚本管理的全部媒体库同步 **LibraryOptions**（新建与已存在库均适用）：

- 元数据/图片语言：`zh-CN`，国家：`CN`
- 实时监控：开
- 字幕下载语言：`chi`、`zho`；不随媒体保存外挂字幕（由 CSF 处理）
- 私享影库 / 小电影：启用成人元数据（`EnableAdultMetadata`）

完成后会写入：

```text
EMBY_INITIALED=true
EMBY_API_KEY=...          # 若脚本自动创建成功
```

## 9. 初始化 MoviePilot（下载器/媒体服务器/目录规则）

确保 `.env` 里 `QB_INITIALED=true` 且 `EMBY_INITIALED=true` 后执行：

```bash
python3 mpv2-install.py --init-mpv2
```

完成后建议重启 MoviePilot，使 `category.yaml` 生效：

```bash
docker restart mpv2-moviepilot
```

## 10. 初始化 ChineseSubFinder（字幕）

建议在 `--init-emby` 完成后执行（需 `.env` 中已有 `EMBY_API_KEY`）。脚本会写入 `chinesesubfinder/config/ChineseSubFinderSettings.json`，跳过 Web 向导，并配置：

- WebUI 账号（`CSF_USER` / `CSF_PASSWORD`）
- **API Key**（`CSF_API_KEY`，供 MoviePilot 插件调用）
- 电影 / 连续剧扫描目录（容器内 `/media/...`）
- **Emby 联动**：从 Emby 拉取近期入库视频并自动补字幕

```bash
python3 mpv2-install.py --init-csf --csf-user admin --csf-password 'YourCsfPassword'
```

也可与安装时统一密码：

```bash
python3 mpv2-install.py --init-csf --password 'YourStrongPassword'
```

脚本固定写入以下项（不通过 `.env` 配置）：

- 电影目录：`/media/真人电影`、`/media/动漫电影`、`/media/小电影`、`/media/私享影库`
- 连续剧目录：`/media/真人剧集`、`/media/动漫剧集`、`/media/综艺`、`/media/纪录片`、`/media/短剧`
- Emby 联动：启用，`http://emby:8096`，路径映射 `{DATA_DIR}/media` → `/media`
- API Key：启用，写入 `.env` 的 `CSF_API_KEY`
- 目录扫描间隔：`@every 6h`

`.env` 会写入：`CSF_USER`、`CSF_PASSWORD`、`CSF_API_KEY`、`CSF_INITIALED=true`（`EMBY_API_KEY` 由 `--init-emby` 写入）。

完成后会重启 `mpv2-chinesesubfinder`。WebUI：`http://NAS-IP:7035`。

> 新入库补字幕依赖 **Emby API 联动 + 定时扫目录**，无需 MoviePilot 额外插件。若希望在 **整理完成瞬间** 也触发补字幕，可安装 MoviePilot「ChineseSubFinder」插件，见 [插件配置/ChineseSubFinder插件配置.md](插件配置/ChineseSubFinder插件配置.md)。

## 11. 目录与挂载规则核对

详细目录、Emby 库、MoviePilot 规则见文首 **「目录与规则速查」**。此处仅列挂载要点：

挂载原则：

1. MoviePilot、qB 可访问 `/volume1/media-data`。
2. Emby 只映射 `/volume1/media-data/media`（看不到 `downloads`）。
3. ChineseSubFinder 只处理 `/media`（对应 `/volume1/media-data/media`）。

## 12. 常用访问端口


| 服务               | 端口   | 说明              |
| ------------------ | ------ | ----------------- |
| MoviePilot Web     | 9443   | 主界面            |
| MoviePilot API     | 3001   | API 服务          |
| qB-media           | 7097   | 日常下载 WebUI    |
| qB-brush           | 7098   | 刷流 WebUI        |
| Emby               | 7096   | HTTP              |
| Emby HTTPS         | 7020   | HTTPS             |
| ChineseSubFinder   | 7035   | WebUI             |
| ChineseSubFinder   | 7037   | 视频列表缩略图    |


CookieCloud（MoviePilot 内置，浏览器插件同步 PT Cookie）：

```text
http://NAS-IP:9443/cookiecloud/
```

## 13. 验证清单

1. `docker compose ps` 全部容器 `Up`。
2. MoviePilot 能连接 qB 与 Emby。
3. Emby 只看到最终媒体目录，不看到 `downloads`。
4. 订阅下载进入 `downloads/media`，整理后进入 `media` 对应分类目录。
5. qB-media 分类 `media` / `manual` / `private` 路径正确。
6. 私享影库、小电影库在 Emby 中已创建；私享影库已对普通账号隐藏权限。
7. ChineseSubFinder WebUI 可登录，`CSF_INITIALED=true`，`.env` 含 `CSF_API_KEY`，Emby 联动已启用。

## 14. 常用维护命令

停止：

```bash
python3 mpv2-install.py --stop
```

清理（会删除 compose 配置、服务数据目录、`downloads` 下内容；`**media` 媒体库需手动输入 Y 才删除**）：

```bash
python3 mpv2-install.py --clean
```

重建后重新启动：

```bash
python3 mpv2-install.py
cd /volume1/docker/media-stack
docker compose up -d
```

## 15. MoviePilot 推荐插件（可选）

脚本**不会**自动安装插件。建议完成 §9 init-mpv2、§10 init-csf 且 §13 验证通过后，先按 [站点认证及站点添加.md](插件配置/站点认证及站点添加.md) 完成 PT 站点配置，再按需安装下列插件并参照 `插件配置/` 目录文档填写。


| 项目            | 配置文件 | 用途 |
|-----------------|----------|------|
| 站点认证与添加   | [插件配置/站点认证及站点添加.md](插件配置/站点认证及站点添加.md) | 用户认证、PT 站点 Cookie/Token 添加、RSS 与影视订阅（**非插件**，使用搜索/订阅前建议先完成） |
| ChineseSubFinder | [插件配置/ChineseSubFinder插件配置.md](插件配置/ChineseSubFinder插件配置.md) | MP 整理完成后通知 CSF 补字幕（**可选**；Emby 联动已覆盖主场景） |
| 目录实时监控     | [插件配置/目录实时监控插件配置.md](插件配置/目录实时监控插件配置.md) | 监控 `downloads/manual`，手动拷贝/外部导入资源自动识别、刮削、整理 |
| 媒体库刮削       | [插件配置/媒体库刮削插件配置.md](插件配置/媒体库刮削插件配置.md) | 对已入库历史媒体补海报/NFO；**默认关闭定时**，需要时手动跑一次 |
| 站点刷流         | [插件配置/站点刷流插件配置.md](插件配置/站点刷流插件配置.md) | 调度 `qB-刷流专用`，资源只进 `downloads/brush`，与日常下载隔离 |


建议安装顺序：

```text
1. 完成 §9 init-mpv2，§10 init-csf，§13 验证日常下载与整理正常
2. 配置 PT 站点 → 按 插件配置/站点认证及站点添加.md（认证、添加站点、RSS、订阅）
3. （可选）整理完成立刻补字幕 → 安装「ChineseSubFinder」插件，按 插件配置/ChineseSubFinder插件配置.md 配置
4. 需要手动整理 → 安装「目录实时监控」，按 插件配置/目录实时监控插件配置.md 配置
5. 需要刷流保种 → 安装「站点刷流」，按 插件配置/站点刷流插件配置.md 配置
6. 历史媒体缺元数据 → 临时安装/启用「媒体库刮削」，按 插件配置/媒体库刮削插件配置.md 手动执行一次
```

注意：插件配置与脚本生成的目录/转移规则一致；不要监控 `downloads/brush` 或最终 `media` 根目录，避免重复整理。
