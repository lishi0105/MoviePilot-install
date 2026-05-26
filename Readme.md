# MoviePilot 媒体栈部署

## 目录

- [本仓库实现目标](#本仓库实现目标)
- [目录与规则速查](#目录与规则速查)
- [1. 前置准备（NAS）](#1-前置准备nas)
  - [1.1 网络环境](#11-网络环境)
  - [1.2 共享目录](#12-共享目录)
  - [1.3 Docker 支持](#13-docker-支持)
- [2. 脚本说明](#2-脚本说明)
  - [2.1 安装脚本参数表](#21-安装脚本参数表)
  - [2.2 `--github-token` 说明](#22---github-token-说明)
  - [2.3 `--host-ip` 说明](#23---host-ip-说明)
- [3. 一键生成目录与 compose](#3-一键生成目录与-compose)
- [4. 启动容器](#4-启动容器)
- [5. 首次网页初始化](#5-首次网页初始化)
  - [5.1 Emby](#51-emby)
  - [5.2 MoviePilot](#52-moviepilot)
- [6. 配置 `.env` 关键项](#6-配置-env-关键项)
- [7. 初始化 qB](#7-初始化-qb)
- [8. 初始化 Emby 媒体库与 API Key](#8-初始化-emby-媒体库与-api-key)
- [9. 初始化 MoviePilot（下载器/媒体服务器/目录规则）](#9-初始化-moviepilot下载器媒体服务器目录规则)
- [10. 目录与挂载规则核对](#10-目录与挂载规则核对)
- [11. 常用访问端口](#11-常用访问端口)
- [12. 验证清单](#12-验证清单)
- [13. 常用维护命令](#13-常用维护命令)
- [14. MoviePilot 推荐插件（可选）](#14-moviepilot-推荐插件可选)

---

## 本仓库实现目标

NAS 上搭 MoviePilot + qB + Emby 这一套，**自己部署**往往要在多个容器、目录、Web 页面之间来回折腾；**某宝某鱼**找人代部署通常收费不低。本脚本尽量将部署、初始化、配置通过脚本实现，不再依赖手动配置，并提供几个常用插件的设置说明。

在此基础上，脚本还顺带处理了常见工程问题：双 qB 隔离刷流、Emby 不看下载临时目录、媒体库按类型+地区预置、Docker/Compose 环境检查、目录权限等（详见下文「目录与规则速查」）。

适合：**新搭一套 MoviePilot V2 媒体栈**、希望少踩坑、少手工配置的场景。**旧库迁移不在本脚本范围内**。

**推荐顺序**：§1 准备 → §3 生成目录 → §4 启容器 → §5 网页向导 → §6 写 `.env` → §7 init-qb → §8 init-emby → §9 init-mpv2 → §12 验证 → §14 插件（可选）

以下路径默认根目录为 `/volume1/media-data`；Emby 媒体库默认按**二级地区**建库（`EMBY_LIBRARY_MODE=secondary`）。

## 目录与规则速查

### 1. 安装脚本创建的文件夹

`python3 mpv2-install.py` 会创建下载目录与媒体库目录（`--init-mpv2` 还会补建部分媒体子目录）。

**下载目录**


| 路径                  | 用途        | MoviePilot | qB                    |
| ------------------- | --------- | ---------- | --------------------- |
| `downloads/media`   | 日常订阅/搜索下载 | 整理         | qB-media 默认分类         |
| `downloads/brush`   | 刷流保种      | 不整理        | qB-brush 默认分类         |
| `downloads/manual`  | 手动/临时下载   | 整理         | qB-media 分类 `manual`  |
| `downloads/private` | 私密视频暂存    | 不处理        | qB-media 分类 `private` |


**媒体库目录**


| 路径                | 说明                       |
| ----------------- | ------------------------ |
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


> `downloads/private`、`media/小电影` 需自行整理入库；不走 MoviePilot 自动刮削转移。

### 2. Emby 媒体库与文件夹

`python3 mpv2-install.py --init-emby` 默认创建以下媒体库（可在 `.env` 用 `EMBY_LIBRARY_MODE=primary` 切回一级建库）。

**按地区拆分的库（电视节目 / 电影）**


| Emby 媒体库  | 类型   | 对应文件夹             |
| --------- | ---- | ----------------- |
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


| Emby 媒体库  | 类型  | 对应文件夹             |
| --------- | --- | ----------------- |
| 私享影库-国产   | 电影  | `media/私享影库/国产`   |
| 私享影库-日韩   | 电影  | `media/私享影库/日韩`   |
| 私享影库-欧美   | 电影  | `media/私享影库/欧美`   |
| 私享影库-其他地区 | 电影  | `media/私享影库/其他地区` |


### 3. MoviePilot 转移规则

`python3 mpv2-install.py --init-mpv2` 写入的整理策略摘要如下。

**全局 Transfer 设置**


| 项目        | 默认值                                  |
| --------- | ------------------------------------ |
| 整理方式      | 移动（`move`）                           |
| 参与整理的资源目录 | `downloads/media`、`downloads/manual` |
| 排除目录      | `downloads/brush`                    |
| 不处理       | `downloads/private`                  |


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

插件配置文档（见 §14，需在插件市场安装后手工填写）：

```text
插件配置/
├── 站点认证及站点添加.md
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
| `--password`            | 统一设置 `MoviePilot/qB-media/qB-brush` 密码 | 无（随机或由单独参数决定）                 |
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
| `--host-ip`             | 初始化脚本访问 NAS 服务的局域网 IP（见 §2.3）          | 自动探测                          |


注意：

- `--password` 与 `--moviepilot-password/--qb-media-password/--qb-brush-password` 互斥。
- `--init-qb`、`--init-emby`、`--init-mpv2` 前需先 `docker compose up -d`，且容器处于运行状态。
- `--init-mpv2` 前需先完成 `--init-qb` 和 `--init-emby`，且 `.env` 中 `QB_INITIALED=true`、`EMBY_INITIALED=true`。
- NAS 上建议用 **root** 执行安装脚本，以便正确设置目录所有者（`PUID/PGID`）；非 root 时可能只能改权限、不能 `chown`。

### 2.2 `--github-token` 说明

MoviePilot 安装插件、拉取 GitHub 上的规则/资源时会访问 GitHub API。未配置 Token 时容易触发**匿名 API 限流**，表现为插件市场加载失败、索引更新超时等。


| 项目   | 说明                                                                                                                  |
| ---- | ------------------------------------------------------------------------------------------------------------------- |
| 作用   | 写入 `.env` 的 `GITHUB_TOKEN`，并在 `--init-mpv2` 时同步到 MoviePilot 系统配置                                                    |
| 是否必填 | 否；不装插件、不依赖 GitHub 资源时可省略                                                                                            |
| 推荐场景 | 需要从插件市场安装插件、使用依赖 GitHub 的索引/规则时建议配置                                                                                 |
| 获取方式 | GitHub → Settings → Developer settings → Personal access tokens → 生成 **classic** token；勾选 `public_repo`（只读公开仓库一般够用） |
| 写入时机 | 初次 `mpv2-install.py` 时加 `--github-token`，或后续写入 `.env` 再执行 `--init-mpv2`                                             |
| 安全   | Token 存在 `.env`（权限 `600`），不要提交到 git                                                                                 |


示例：

```bash
# 初次安装时一并写入
python3 mpv2-install.py --password 'YourStrongPassword' --github-token 'ghp_xxxxxxxx'

# 已安装后补写：编辑 .env 增加 GITHUB_TOKEN=...，再重新初始化 MoviePilot
python3 mpv2-install.py --init-mpv2 --host-ip 192.168.1.100
```

### 2.3 `--host-ip` 说明

`--host-ip` 只在 `**--init-qb` / `--init-emby` / `--init-mpv2**` 时使用；初次生成目录（§3）不需要。

脚本在**宿主机**上通过「NAS 局域网 IP + 端口映射」访问各服务 API，与容器内 Docker 网络地址（如 `qb-media:7097`）不同。


| 项目     | 说明                                                                       |
| ------ | ------------------------------------------------------------------------ |
| 作用     | 指定 NAS 在局域网中的可达地址，供初始化脚本调用 Web API                                       |
| 是否必填   | 否；默认自动探测本机内网 IP                                                          |
| 自动探测方式 | 向 `223.5.5.5:80` 发起 UDP 连接，取本机出口网卡 IP                                    |
| 填写格式   | 纯 IP，如 `192.168.1.100`；也支持 `http://192.168.1.100`（Emby / MoviePilot 会识别） |


**各 init 命令中的实际用途**


| 命令            | 访问地址（默认）                                        | 说明                                             |
| ------------- | ----------------------------------------------- | ---------------------------------------------- |
| `--init-qb`   | `http://{host-ip}:7097`、`http://{host-ip}:7098` | 从宿主机登录 qB WebUI、写配置                            |
| `--init-emby` | `http://{host-ip}:7096`                         | 创建媒体库、生成 API Key（若 `.env` 已有 `EMBY_URL` 则优先用它） |
| `--init-mpv2` | `http://{host-ip}:9443`                         | 登录 MoviePilot 并写入下载器 / 目录 / 规则                 |


**建议手动指定 `--host-ip` 的情况**

- 自动探测到的 IP 不对（多网卡、VPN、Docker 桥接网卡、`127.x`）
- NAS 有多个局域网段，希望固定用访问 Emby/MoviePilot 的那个 IP
- 通过 SSH 在 NAS 上跑脚本，但要从「你浏览器访问用的那个 IP」做初始化
- 自动探测失败报错：`未能自动获取真实内网 IP，请使用 --host-ip 指定`

**不需要 `--host-ip` 的情况**

- 你在 NAS 本机终端执行脚本，且自动探测打印的 IP 与浏览器访问地址一致

示例：

```bash
# 三个 init 建议使用同一个 IP（换成你实际访问 NAS 的地址）
NAS_IP=192.168.1.100

python3 mpv2-install.py --init-qb   --host-ip $NAS_IP
python3 mpv2-install.py --init-emby --host-ip $NAS_IP
python3 mpv2-install.py --init-mpv2 --host-ip $NAS_IP
```

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

- `--password` 只统一设置密码，用户名统一为admin。
- MoviePilot 用户名通过 `--moviepilot-user` 设置（默认 `admin`）。
- `--password` 不能和 `--moviepilot-password/--qb-media-password/--qb-brush-password` 同时使用。
- 首次安装若计划使用插件市场，可加上 `--github-token`（见 §2.2）。

## 4. 启动容器

```bash
cd /volume1/docker/media-stack
docker compose up -d
```

启动后服务包括：

- MoviePilot
- PostgreSQL
- Redis
- qB-media
- qB-brush
- Emby
- ChineseSubFinder

## 5. 首次网页初始化

### 5.1 Emby

先打开 Emby 完成首次向导并创建管理员账号：

```text
http://NAS-IP:7096
```

## 6. 配置 `.env` 关键项

编辑：

```text
/volume1/docker/media-stack/.env
```

**在 `--init-emby` 之前**，至少写入 Emby 管理员账号（§5.1 向导里创建的）：

```text
EMBY_USER=admin
EMBY_PASSWORD=EmbyPassword
```

MoviePilot 登录（§3 生成 `.env` 时已有，确认即可）：

```text
MOVIEPILOT_USER=admin
MOVIEPILOT_PASSWORD=...
```

可选：

```text
EMBY_API_KEY=...          # 留空时 --init-emby 会尝试自动创建并写回
GITHUB_TOKEN=...          # 插件/ GitHub 资源访问，见 §2.2
```

## 7. 初始化 qB

容器运行中执行：

```bash
python3 mpv2-install.py --init-qb --host-ip 192.168.1.100
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

## 10. 目录与挂载规则核对

详细目录、Emby 库、MoviePilot 规则见文首 **「目录与规则速查」**。此处仅列挂载要点：

挂载原则：

1. MoviePilot、qB 可访问 `/volume1/media-data`。
2. Emby 只映射 `/volume1/media-data/media`（看不到 `downloads`）。
3. ChineseSubFinder 只处理 `/media`（对应 `/volume1/media-data/media`）。

## 11. 常用访问端口


| 服务               | 端口   | 说明         |
| ---------------- | ---- | ---------- |
| MoviePilot Web   | 9443 | 主界面        |
| MoviePilot API   | 3001 | API 服务     |
| qB-media         | 7097 | 日常下载 WebUI |
| qB-brush         | 7098 | 刷流 WebUI   |
| Emby             | 7096 | HTTP       |
| Emby HTTPS       | 7020 | HTTPS      |
| ChineseSubFinder | 7035 | WebUI      |
| ChineseSubFinder | 7037 | 视频列表缩略图    |


CookieCloud（MoviePilot 内置，浏览器插件同步 PT Cookie）：

```text
http://NAS-IP:9443/cookiecloud/
```

## 12. 验证清单

1. `docker compose ps` 全部容器 `Up`。
2. MoviePilot 能连接 qB 与 Emby。
3. Emby 只看到最终媒体目录，不看到 `downloads`。
4. 订阅下载进入 `downloads/media`，整理后进入 `media` 对应分类目录。
5. qB-media 分类 `media` / `manual` / `private` 路径正确。
6. 私享影库、小电影库在 Emby 中已创建；私享影库已对普通账号隐藏权限。

## 13. 常用维护命令

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

## 14. MoviePilot 推荐插件（可选）

脚本**不会**自动安装插件。建议完成 §9 init-mpv2 且 §12 验证通过后，再在 MoviePilot 插件市场按需安装，并参照 `插件配置/` 目录下的中文文档填写。


| 插件（市场名称以实际为准） | 配置文件                                     | 用途                                          |
| ------------- | ---------------------------------------- | ------------------------------------------- |
| 目录实时监控        | [插件配置/目录实时监控插件配置.md](插件配置/目录实时监控插件配置.md) | 监控 `downloads/manual`，手动拷贝/外部导入资源自动识别、刮削、整理 |
| 媒体库刮削         | [插件配置/媒体库刮削插件配置.md](插件配置/媒体库刮削插件配置.md)   | 对已入库历史媒体补海报/NFO；**默认关闭定时**，需要时手动跑一次         |
| 站点刷流          | [插件配置/站点刷流插件配置.md](插件配置/站点刷流插件配置.md)     | 调度 `qB-刷流专用`，资源只进 `downloads/brush`，与日常下载隔离 |


建议安装顺序：

```text
1. 完成 §9 init-mpv2，§12 验证日常下载与整理正常
2. 需要手动整理 → 安装「目录实时监控」，按 插件配置/目录实时监控插件配置.md 配置
3. 需要刷流保种 → 安装「站点刷流」，按 插件配置/站点刷流插件配置.md 配置
4. 历史媒体缺元数据 → 临时安装/启用「媒体库刮削」，按 插件配置/媒体库刮削插件配置.md 手动执行一次
```

注意：插件配置与脚本生成的目录/转移规则一致；不要监控 `downloads/brush` 或最终 `media` 根目录，避免重复整理。