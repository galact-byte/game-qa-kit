# game-qa-kit

跨游戏可复用的 **QA 巡检工具核心**——从示例游戏汉化巡检沉淀而来。核心引擎无关，
换一个游戏只需加一份 profile。

## 为什么这样设计

多轮实战验证出的结论：

- **模型驱动游戏又慢又脆**：模型要“截图→回传→识别”，比人肉眼手玩慢得多，而且输入通路
  是最不可移植的部分（每个引擎的焦点/坐标/触发帧都不同，HSP 更是只认前台真实 SendInput）。
- **真正的杠杆是**：**人快速手玩 → 盯屏自动留证/去重 → 模型或人异步复核**。这套对任何
  游戏、任何引擎都成立，也是本工具的主线。

所以本工具把能力分成：**通用核心**（截图/盯屏/去重/证据/存档）与**每游戏 profile**
（进程名、存档文件、截图区域、QA 数据目录）；输入/改档为**辅助且默认低优先**。

## 目录结构

```
game-qa-kit/
├── gameqakit/
│   ├── profile.py     # GameProfile：每游戏配置 + 派生目录
│   ├── win32.py       # 找窗口 + PrintWindow 后台截图（DPI 不感知）
│   ├── dedup.py       # dHash 去重 + 像素级摘要
│   ├── watch.py       # 盯屏 watch / mark / watch_report（主线）
│   ├── saves.py       # 存档快照/恢复(SHA256校验) + 进程护栏 + kill
│   ├── inputs.py      # 点击/按键：抢鼠标护栏 + 前台快路 + 相对坐标（辅助）
│   ├── cli.py         # 命令行入口（盯屏工作流）
│   └── mcp_server.py  # MCP 封装（按激活 profile 暴露能力）
├── profiles/
│   └── example.json      # 示例游戏作为第一份 profile
└── tests/test_core.py # 离线核心测试（无需游戏）
```

## 安装

```bash
pip install -r requirements.txt   # 仅 Pillow
```

## 推荐工作流（人玩 + 盯屏 + 异步复核）

```bash
# 1) 选定游戏 profile（或设环境变量 GAMEQAKIT_PROFILE=example）
python -m gameqakit.cli profiles

# 2) 开始盯屏，然后你正常手玩；画面变化才自动留图（Ctrl+C 停）
python -m gameqakit.cli watch --profile example

# 3) 玩时看到可疑画面，另开一个终端随手标记（复核优先看）
python -m gameqakit.cli mark --profile example -m "阶段条疑似日文フェーズ"

# 4) 汇总成复核日志，交给模型/人逐帧复核
python -m gameqakit.cli report --profile example
```

繁日同形字（済/濟、絵/繪、齢/齡…）判读**必须像素级核对**，不能信视觉模型的自动结论。

## 作为 MCP 使用

注册到 MCP 客户端（如 pi）的 `mcp.json`：

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

## 新增一个游戏

在 `profiles/` 加一份 JSON：

```json
{
  "name": "mygame",
  "proc": "mygame",
  "title_substr": "My Game",
  "savedata_dir": "D:/Games/MyGame/save",
  "save_globs": ["*.sav"],
  "data_dir": "D:/qa-work/mygame",
  "regions": { "bottom_dialog": [0, 560, 1200, 160] }
}
```

核心代码不用改。

## 存档改动铁律（来自实战教训）

- 改档前**先游戏内存盘**：磁盘档只反映最后一次游戏内保存，内存未落盘进度会随关进程丢失。
- 恢复存档后**运行中的游戏不重读磁盘**：必须 `kill_game` → 重启 → 游戏内 AUTO LOAD 才生效。
- 覆盖存档前自动备份 + 全程 SHA-256 校验，绝不用损坏快照覆盖。

## 测试

```bash
python -m unittest tests/test_core.py
```
