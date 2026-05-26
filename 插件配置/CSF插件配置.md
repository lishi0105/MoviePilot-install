# ChineseSubFinder 插件配置

> 将 MoviePilot 与 ChineseSubFinder（CSF）联动：资源 **整理入库到 `media/` 后**，由插件通知 CSF 立即补字幕。

## 前置条件

- 已完成 [Readme.md](../Readme.md) §10 `init-csf`，CSF WebUI 可登录（`http://NAS-IP:7035`）。
- MoviePilot 插件市场已安装「ChineseSubFinder」插件。
- CSF 容器 `mpv2-chinesesubfinder` 与 MoviePilot 同在 `media_net` 网络。

## 适用场景

| 场景 | 说明 |
|------|------|
| 整理完成立刻补字幕 | MoviePilot move 到 `media/` 后，插件触发 CSF 下载字幕 |
| 与 Emby 联动互补 | Emby 入库扫描有延迟时，插件可更早触发一次 |

## 不适用 / 不必依赖

| 场景 | 说明 |
|------|------|
| 新入库自动补字幕（主路径） | 已由 CSF **Emby 联动 + 定时扫目录** 覆盖（`--init-csf` 已配），**不装此插件也能工作** |
| 历史库批量补字幕 | 在 CSF WebUI 手动扫描，或依赖 CSF 定时任务 |
| 刷流 / 私密目录 | `downloads/brush`、`downloads/private` 不参与整理，插件不会处理 |

## 与 `--init-csf` 的关系

```text
【主路径：Emby 联动】（脚本已配置，无需插件）
Emby 新入库 → CSF 拉取近期更新 → 自动补字幕

【可选增强：MP 插件】（本文档）
MoviePilot 整理完成 → 插件调用 CSF API → 对该文件补字幕
```

两者可同时启用，互不冲突。

## 容器挂载与路径映射

本仓库 compose 默认挂载如下（`DATA_DIR` 默认为 `/volume1/media-data`）：

| 容器 | 宿主机 | 容器内路径 |
|------|--------|------------|
| MoviePilot | `${DATA_DIR}` | `${DATA_DIR}`（如 `/volume1/media-data`） |
| ChineseSubFinder | `${DATA_DIR}/media` | `/media` |

因此媒体库在两侧的实际路径为：

| 侧 | 路径 |
|----|------|
| MoviePilot（本地路径） | `/volume1/media-data/media` |
| CSF（远端路径） | `/media` |

插件通过 **字符串替换** 将 MP 路径转为 CSF 路径，例如：

```text
/volume1/media-data/media/真人电影/大陆/某片/某片.mkv
        ↓
/media/真人电影/大陆/某片/某片.mkv
```

若 `.env` 中 `DATA_DIR` 非默认值，本地路径改为 `{DATA_DIR}/media`。

## 配置步骤

### 1. API Key（已由 `--init-csf` 自动生成）

`--init-csf` 会启用 CSF 的 API Key 并写入 `.env` 的 `CSF_API_KEY`。配置 MoviePilot 插件时 **直接复制该值** 即可，无需再去 CSF WebUI 手动开启。

查看方式：

```bash
grep CSF_API_KEY /volume1/docker/media-stack/.env
```

若 `.env` 中已有 `CSF_API_KEY`，重复执行 `--init-csf` 会 **沿用** 该值，不会轮换。

### 2. 插件基本配置

在 MoviePilot → **插件** → **ChineseSubFinder** → **插件配置**：

| 选项 | 建议值 | 说明 |
|------|--------|------|
| 启用插件 | 开 | |
| 服务器 | `http://mpv2-chinesesubfinder:19035` | 容器间访问；用 **19035**（容器端口），不是宿主机 **7035** |
| API密钥 | `.env` 中的 `CSF_API_KEY` | 由 `--init-csf` 写入 |
| 本地路径 | `/volume1/media-data/media` | MoviePilot 容器内媒体库根目录 |
| 远端路径 | `/media` | CSF 容器内媒体库根目录 |

**常见填错：**

| 错误写法 | 原因 |
|----------|------|
| `http://localhost:7035` | MP 容器内 localhost 不是 CSF |
| `http://NAS-IP:7035` | 应走 Docker 网络容器名 + 内部端口 |
| 本地/远端路径写宿主机路径不一致 | 必须与 compose 挂载一致 |

### 3. 保存

点击 **保存**，无需额外 cron（由 MoviePilot 整理事件触发）。

## 验证步骤

```text
1. 确认 .env 中已有 CSF_API_KEY（--init-csf 完成后）
2. 在 MoviePilot 保存插件配置
3. 触发一次整理：订阅下载完成，或向 downloads/media 放入测试资源
4. 确认文件已 move 到 media/ 对应分类目录
5. 打开 CSF WebUI，查看是否出现对应补字幕任务/日志
6. 确认视频同目录下生成 .ass / .srt 等字幕文件
```

查看 CSF 日志：

```bash
docker logs -f mpv2-chinesesubfinder
```

## 与其他流程的关系

```text
【日常订阅下载 + 字幕（完整链路）】
MoviePilot → qB-media → downloads/media → 整理 move → media/
    ├─（可选）MP ChineseSubFinder 插件 → CSF 立即补字幕
    └─ Emby 刷新 → CSF Emby 联动 / 定时扫描 → 补字幕

【手动整理】
downloads/manual → 目录实时监控 → media/ → 同上

【刷流】
站点刷流 → downloads/brush → 不整理、不进 Emby、不触发本插件
```

## 推荐配置汇总

可直接对照插件界面逐项填写：

```text
【ChineseSubFinder 插件】
启用插件：开
服务器：http://mpv2-chinesesubfinder:19035
API密钥：见 .env 的 CSF_API_KEY
本地路径：/volume1/media-data/media
远端路径：/media
```

## 故障排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 插件保存后无任何反应 | 服务器地址填错 | 改为 `http://mpv2-chinesesubfinder:19035` |
| 提示认证失败 | API Key 与 .env 不一致 | 核对 `CSF_API_KEY`，必要时重跑 `--init-csf` |
| CSF 收到任务但找不到文件 | 本地/远端路径与挂载不一致 | 核对 compose 挂载与上表 |
| 只有 Emby 能补字幕、插件不行 | 仅 Emby 联动正常 | 按上文逐项检查 API 与路径 |
| CSF 有任务但搜不到字幕 | 片源识别 / 字幕源配置 | 在 CSF WebUI 检查 TMDB、字幕源等 |

## 注意事项

1. 本插件为 **可选**；`--init-csf` 的 Emby 联动已能覆盖「新入库补字幕」主场景。
2. 服务器地址务必使用 **Docker 容器名** `mpv2-chinesesubfinder`，不要用 `localhost` 或宿主机 IP（除非明确改网络模式）。
3. 本地路径、远端路径必须分别对应 **MP 容器内**、**CSF 容器内** 的媒体库根，不是 NAS 文件管理器里看到的路径写法（若与容器内一致则相同）。
4. CSF 密码规则见 [Readme.md](../Readme.md) §2 / `password_utils.py`（`A-Za-z0-9!@#%-*`），与 WebUI 登录无关，但安装时统一密码需注意。
