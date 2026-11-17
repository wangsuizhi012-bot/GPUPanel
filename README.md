# GPUPanel · GPU 全清面板

8G 显存单卡上，ComfyUI 与本地 LLM 栈互斥。本面板提供"关机式"一键全清 / 一键恢复：
点红按钮把**应用窗口 + AI 后台**全部清掉（显存归零），自己启动 ComfyUI 独享整张卡；
点绿按钮把整套栈和被关应用拉回来。

## 按钮语义

- **一键清场（红）**：弹窗列清单（应用按窗口标题、后台按端口/进程名/命令行），点"是"才执行。
  1. 调 llama-swap 官方 `POST /api/models/unload` 卸载全部模型；
  2. 向应用窗口发 `WM_CLOSE`（与 Windows 关机同一机制），宽限 `app_grace_s` 秒后仍活着的强制结束；
  3. 结束 AI 后台：端口监听者（8188/9292/1235/1236/1237/5678/3080/8787/8790）、
     `llama-swap.exe`/`llama-server.exe`、命令行匹配（重命名工具、agent-hub、自定义脚本），连同子进程；
  4. 关前拍快照（exe + 完整命令行）供恢复。
- **恢复后台（绿）**：`start-stack.bat`（n8n + 网关 + DSH）+ 嵌入服务 + agent-hub ensure +
  `restore_cmds` + 按快照重启被关应用（同名已在跑的跳过防双开）。ComfyUI 留给用户自己启动。

## 安全设计

- 确认弹窗前列全清单，点"否"零副作用；忙时按钮禁用、面板拒关。
- 永久豁免：桌面 explorer、设置、输入法外壳、NVIDIA 驱动伴侣、Qoder（会话宿主）、面板自身。
- 子进程须"出生晚于父进程"，防 PID 复用误杀。
- Win32 句柄声明 `argtypes`，防 64 位截断关错窗口。
- 全程写 `gpu_panel.log`；无权限的进程明确记日志（需管理员启动面板）。
- **未保存内容会丢失**（与关机相同）；浏览器标签/编辑器会话能否还原由应用自身决定。

## 安装与启动

依赖：Python 3.12+、`psutil`（tkinter 随 Python 自带）。
普通启动：`pythonw gpu_panel.py`；管理员启动：双击 `gpu_panel_admin.vbs`（UAC 一次）。
桌面快捷方式指向 vbs，即管理员启动。

## 配置 gpu_panel.json

- `kill_ports` / `kill_names` / `kill_patterns`：后台名单（ patterns 为命令行子串，不区分大小写）。
- `keep_apps`：豁免的应用进程名。
- `app_grace_s`：应用关闭宽限秒数。
- `restore_cmds`：恢复时额外拉起的命令（`{"name","cmd","cwd"}` 或字符串）。

## 自动守卫 gpu_guard.py（可选，无界面）

每 10 秒探测一次 ComfyUI（8188）：
- **起来** → 调 llama-swap 官方卸载 API + 写暂停旗标 `E:\AI\_scripts\gpu_pause.flag`
  （重命名批次读到旗标后原地协作暂停，不中断不丢进度）；暂停期间若发现又有模型被加载，再卸一次。
- **消失** → 删旗标，暂停的批次自动续跑。

只做这两件事：**不杀应用、不冻结进程、不自愈复活网关**，与手动按钮互不冲突。
无界面后台运行：`start "" pythonw gpu_guard.py`；端口 9294 单实例锁；ASCII 日志 `gpu_guard.log`。
本机登录自启：启动文件夹 `Startup\gpu_guard.vbs`（无需管理员；删除该文件即取消自启）。

## 机器适配点

`STACK_DIR` 常量指向本机栈脚本目录（`start-stack.bat` / `start-embedding-server.bat`）。
换机器使用时改该常量与 `kill_ports` 即可；面板本体不依赖任何私有服务。

## 相关

图像重命名工具见 [ImageAutoRenamer](https://github.com/wangsuizhi012-bot/ImageAutoRenamer)。