# game-qa-kit

跨游戏可复用的 **QA 巡检工具**——从示例游戏汉化巡检沉淀而来。核心引擎无关，
换一个游戏只需在界面上**新建一份 profile**，核心代码一行不改。

## 为什么这样设计

多轮实战验证出的结论：

- **模型驱动游戏又慢又脆**：模型要“截图→回传→识别”，比人肉眼手玩慢得多，而且输入通路
  是最不可移植的部分（每个引擎的焦点/坐标/触发帧都不同）。
- **真正的杠杆是**：**人快速手玩 → 盯屏自动留证/去重 → 模型或人异步复核**。这套对任何
  游戏、任何引擎都成立，也是本工具的主线。

所以工具分成：**通用核心**（截图/盯屏/去重/证据/存档）+ **每游戏 profile**
（进程名、存档、截图区域）；输入/改档为**辅助且默认低优先**。

---

## 快速开始（图形界面，推荐）

1. 安装依赖：`pip install -r requirements.txt`（Pillow + PyQt6）
2. **双击 `start.bat`**（或 `python launch.py`）打开控制台
3. 左上角选择 `example`（或点「新建」建一份你自己游戏的 profile）→ 点「加载」
4. 打开游戏，回到控制台点蓝色卡片「▶ 开始盯屏」
5. **切回游戏正常手玩**——画面变化时工具自动留图，你什么都不用管
6. 看到可疑画面：在「标记备注」写一句，点「★ 标记疑点」（会立刻截当前画面）
7. 玩完点「■ 停止盯屏」→ 点「汇总复核日志」→ 得到一份 `复核日志.txt`，交给模型/自己逐帧复核

界面说明：
- **卡片（主流程 4 步）**：开始盯屏 / 标记疑点 / 截图 / 汇总复核日志
- **左侧栏「工具」**：检测窗口（确认能找到游戏）/ 列出快照 / 打开数据目录
- 右上角**状态胶囊**：未运行（红）/ 运行中（绿）

---

## Profile 是什么

**profile = 一个游戏的配置**。换游戏只需换一份，不用改代码。字段：

| 字段 | 说明 |
|---|---|
| `name` | 名称，同时是文件名 `profiles/<name>.json` |
| `proc` | **进程名（不含 .exe），最关键**。工具按它找游戏窗口 |
| `title_substr` | 窗口标题子串，**可空；留空更安全**（见下方“注意”） |
| `savedata_dir` | 存档目录（只有用到存档快照/改档才需要） |
| `save_globs` | 需快照的存档文件通配，如 `["data*.dat","*.var"]` |
| `data_dir` | QA 数据落盘根目录（截图/盯屏/state）；缺省 `~/.gameqakit/<name>` |
| `regions` | 命名截图区域 `{"名字":[x,y,w,h]}`，可选，**与窗口分辨率相关** |

在 GUI 里点「新建」/「编辑」即可图形化维护，无需手写 JSON。

> ⚠️ **注意（血泪教训）**：`title_substr` 会在**全系统所有窗口**里按标题子串兜底匹配，
> 若填得太泛（如游戏名恰好出现在资源管理器/编辑器标题里），会**静默截错窗口**。
> 进程名通常唯一稳定，**优先只靠 `proc`，把 `title_substr` 留空**。
>
> `regions` 坐标依赖窗口客户区大小；换了窗口尺寸/分辨率需要重设（示例游戏实测客户区
> 为 1500×900，示例里的 1200×720 区域仅供参考）。

---

## 产物在哪

全部落在 profile 的 `data_dir` 下（默认在项目外，不进 Git）：

```
<data_dir>/
├── shots/                     # 单张截图
├── watch/<会话时间戳>/
│   ├── watch_*.png            # 盯屏自动留的“画面变化帧”
│   ├── mark_*.png + marks.jsonl   # 你手动标记的疑点帧
│   ├── index.jsonl            # 全部留图索引
│   └── 复核日志.txt           # 汇总：标记帧在前，时间线在后
└── state/                     # 去重/当前会话指针
```

---

## 命令行 / MCP 用法

CLI（盯屏循环适合命令行常驻）：

```bash
python -m gameqakit.cli watch  --profile example      # 开始盯屏（Ctrl+C 停）
python -m gameqakit.cli mark   --profile example -m "阶段条疑似日文"
python -m gameqakit.cli report --profile example
python -m gameqakit.cli capture --profile example -o shot.png [-r x,y,w,h]
python -m gameqakit.cli profiles
```

作为 MCP 注册（供 pi 等 MCP 客户端调用）：

```json
{
  "gameqakit": {
    "command": "python",
    "args": ["<仓库路径>/gameqakit/mcp_server.py"],
    "env": { "GAMEQAKIT_PROFILE": "example" }
  }
}
```

暴露工具：`profile / win / capture / mark / watch_report / click / key / save / load /
list_saves / kill_game`。改存档、发输入均为危险操作，需显式 `confirm=true`。

---

## 目录结构

```
game-qa-kit/
├── start.bat / launch.py       # Windows 启动器（检查依赖后启动 GUI）
├── requirements.txt            # Pillow + PyQt6
├── gameqakit/
│   ├── profile.py    # GameProfile：每游戏配置 + 派生目录 + 读写
│   ├── win32.py      # 找窗口 + PrintWindow 后台截图（DPI 不感知）
│   ├── dedup.py      # dHash 去重 + 像素级摘要
│   ├── watch.py      # 盯屏 watch / mark / watch_report（主线）
│   ├── saves.py      # 存档快照/恢复(SHA256) + 进程护栏 + kill
│   ├── inputs.py     # 点击/按键：抢鼠标护栏 + 前台快路 + 相对坐标（辅助）
│   ├── icons.py      # 内联线性图标（Feather 风）
│   ├── gui.py        # PyQt6 控制台（LiveAgent 风浅色 UI）
│   ├── cli.py        # 命令行入口
│   └── mcp_server.py # MCP 封装（按激活 profile 暴露）
├── profiles/example.json          # 示例游戏示例 profile
└── tests/test_core.py          # 离线核心测试（无需游戏）
```

## 输入/改档铁律（来自实战教训）

- 很多游戏（如 HSP）只认前台真实输入，`click/key` 必须置顶+移动真光标，会短暂抢鼠标；
  默认带**抢鼠标护栏**（你在操作时自动让路，`force=true` 才强制），并返回 `foreground_confirmed`
  （=是否真点到，“点了没反应”就看它）。
- 改存档前**先游戏内存盘**；恢复存档后**运行中的游戏不重读磁盘**，必须
  `kill_game` → 重启 → 游戏内 AUTO LOAD 才生效。全程 SHA-256 校验 + 覆盖前自动备份。

## 测试

```bash
python -m unittest tests/test_core.py
```
