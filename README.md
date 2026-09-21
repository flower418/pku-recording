# pku-recording

北京大学教学网（Blackboard）课程回放批量下载工具。

输入学号密码 → 自动登录 → 列出当前学期所有课程及回放 → 选择下载。
回放是 HLS（m3u8 + AES-128 加密），工具会自动取密钥、并行下载分片、解密并合成为 mp4。

> 非官方工具，基于教学网现有网页/接口实现，接口变更可能导致失效。

## 特性

- 账号密码登录（北大 IAAA 统一身份认证 → 教学网 SSO），支持手机令牌（OTP）
- 一次登录后会话本地缓存，之后免登录
- 课程/回放列表支持模糊匹配，交互式选择或命令行指定
- 分片并发下载（默认 32，实测最优）、断点续传、自动 AES-128 解密
- 自动用 ffmpeg 合成 mp4（没有 ffmpeg 时保留 .ts，可用 VLC 播放）

## 环境要求

- Python **3.8+**
- 依赖：`requests`
- AES 解密后端（任选其一即可）：
  - `cryptography`（推荐，`pip install ".[fast]"`）
  - `pycryptodome`
  - `openssl` 命令行（macOS / Linux 自带）
- 可选：`ffmpeg`，用于把分片合成 mp4（`brew install ffmpeg` / `apt install ffmpeg`）

## 安装

```bash
git clone <你的仓库地址> && cd pku-recording

# 方式一：pipx（推荐，随处可用 pku-recording 命令）
pipx install ".[fast]"

# 方式二：pip
pip install ".[fast]"

# 方式三：不安装，直接在项目目录里运行
python3 -m pku_recording
```

macOS 用户也可以直接**双击项目里的 `run.command`**（或在终端执行 `./run.command`）进入交互模式；
Windows 用户可双击 `run.bat`。

## 使用

```bash
pku-recording                # 交互模式：选课程 → 选回放 → 下载
pku-recording login          # 登录并缓存会话（会话过期时重新执行）
pku-recording logout         # 清除本地会话
pku-recording overview       # 当前学期每门课有几个回放（--all 含历史课程）
pku-recording list 生成模型   # 列出某门课的回放（课程名支持模糊匹配）
pku-recording download 生成模型                # 交互选择编号
pku-recording download 生成模型 --all          # 下载该课全部回放
pku-recording download 生成模型 --select 1,3-5 # 按编号下载
```

默认下载到 `~/Downloads/北大回放/<课程名>/`。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--all` | 包含历史课程（默认只列当前学期） |
| `--select 1,3-5` | 选择要下载的编号 |
| `--out DIR` | 下载目录（默认 `~/Downloads/北大回放`） |
| `--workers N` | 分片并发数（默认 32；网络好可试 48） |
| `--no-proxy` | 忽略系统代理环境变量，强制直连 |
| `--refresh` | 忽略缓存，强制重新拉取课程/回放列表 |
| `--non-interactive` | 不交互（配合 `--all` / `--select` 用于脚本） |

## 常见问题

**下载速度慢？**
回放存放在校内 CDN，速度受服务器负载影响（实测 5–15 MB/s 波动）。并发数从 16 提到 32 有 2–3 倍提升。
如果本机开了代理（Clash 等），可以试 `pku-recording --no-proxy ...` 直连。

**提示会话过期？**
重新执行 `pku-recording login`。会话保存在 `~/.pku_recording/cookies.txt`，密码不会写入磁盘。

**账号开了手机令牌？**
登录时会提示输入动态验证码。

**没装 ffmpeg？**
分片会合并为 `.ts` 文件保留，VLC 可正常播放；装好 ffmpeg 后重新运行会直接输出 mp4。

**下载中断？**
重新执行同一条命令即可续传，已完成分片保存在 `<文件名>.mp4.parts/`。

## 工作原理（简述）

1. IAAA 认证获取 token → 换取教学网会话（cookies）
2. 教学网首页解析课程列表 → `videoList.action` 解析回放列表
3. 回放页 iframe 重定向拿到参数 → `yjapise` 课程接口返回 m3u8 直链
4. 并发下载分片 → 取 AES-128 密钥解密 → ffmpeg 合成 mp4

## 开发

```bash
python3 -m unittest discover -s tests -v   # 离线单测（不需要账号）
```

目录结构：

```
pku_recording/
├── auth.py         # IAAA + 教学网 SSO 登录、会话缓存
├── blackboard.py   # 课程列表 / 回放列表 / 播放地址解析
├── hls.py          # m3u8 解析、并发下载、合成
├── aes.py          # AES-128-CBC 解密（多后端）
├── util.py         # 配置、缓存、格式化等
└── cli.py          # 命令行 / 交互界面
tests/              # 离线单元测试
```

## 免责声明

仅供下载本人已选修课程的回放用于个人学习。请遵守学校相关规定，请勿传播或用于商业用途。
