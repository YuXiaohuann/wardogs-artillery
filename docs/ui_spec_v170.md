# WARDOGS 炮兵助手 · UI 改版设计规范 (ui_spec_v170)

- 日期: 2026-09-28 · 适用: v1.7.0 及后续外观迭代 · 技术栈: Python 标准 tkinter + ttk (theme=clam), Windows 10
- 性质: 仅设计规范, 不改任何代码文件。所有配方默认不碰: 文案、变量名、热键、布局顺序 (回归红线)。
- 可改面: 颜色 / 字体 / 字号 / 字重 / 内边距 / 外边距 / 描边 / 对齐 / 信息层级。
- 风格基准: Linear / Vercel / GitHub Dark 的克制感。拒绝电竞霓虹、渐变、大圆角、阴影堆砌。

---

## 0. 范围与红线

1. 圆角只能靠 canvas 或放弃 → 本规范**窗口内一律直角**, 圆角只出现在图标母版的方砖上。
2. 阴影基本不可用 → 层级靠「色块深浅 + 1px 描边 + 间距」表达, 不靠投影。
3. 强调色只有一个家族: 琥珀 (amber)。状态色仅翡翠绿 / 柔红两枚, 不算强调色。
4. 标题栏必须走 Windows 沉浸式暗色 (见 2.5), 让系统标题栏与窗口底色融为一体。
5. HUD 是置顶半透明鼠标穿透小窗, 单独一节 (第 5 节), 不与主窗口共用字号体系。

## 1. 设计原则

1. **瞟一眼可读**: 任何信息在 Alt-Tab 后 0.5 秒内读到结论, 层级服务「读数」而不是服务「浏览界面」。
2. **暗而沉静**: 近黑底 + 冷灰字 + 单一琥珀强调, 亮度让位于游戏画面, 对比度让位于可读性。
3. **矩形纪律**: 在 tkinter 能力内只用「纯色块、1px 描边、4px 网格」三种语言做出现代感。
4. **状态双通道**: 开/关、在射程/超射程、焦点/失焦一律颜色 + 符号双通道表达, 不依赖单一通道。
5. **零文案改版**: 一切美化只动颜色/字体/间距/描边; 文案、热键、布局顺序是回归保护的红线。

## 2. 设计令牌

### 2.1 颜色令牌 (全部 hex, 暗色)

| token | hex | 用途 |
| --- | --- | --- |
| --bg-app | #0a0c0f | 窗口底色 / HUD 底 / 标题区底 |
| --bg-card | #12151a | 卡片 / 按钮区 ghost 底 / 标定卡 / 日志条 |
| --bg-input | #0d1014 | 输入框内底 / 组合框 readonly 字段内底 |
| --bg-hover | #1a1f26 | 中性控件 (ghost / 开关 OFF) 悬停底 |
| --bg-log | #0b0e13 | 日志正文底 |
| --bg-tip | #161b22 | 鼠标旁说明气泡底 (保留现状) |
| --border-1 | #1f242c | 卡片 / 控件 / 输入框 1px 描边 |
| --border-hover | #2a313c | 描边悬停态 / 下拉选中底 |
| --text-1 | #e9ecef | 主文字 / hero 读数 / 按钮字 |
| --text-2 | #8b95a6 | 次级文字: 标签列、副标题、提示行、HUD 坐标行 |
| --text-3 | #5d6675 | 最弱文字: 禁用态预留 (当前无实例, 备用) |
| --accent | #fbbf24 | 琥珀强调: 坐标读数、徽章字、焦点描边、HUD hero |
| --accent-strong | #f59e0b | 主操作按钮实心填色 |
| --accent-hover | #fbbf24 | 主操作按钮悬停填色 |
| --accent-on | #101317 | 琥珀底上的深色字 (主操作按钮文字) |
| --accent-dim | #2a2013 | 射程徽章底 (琥珀约 10% 感的纯色块, 不用真透明) |
| --success | #34d399 | 翡翠绿: 开关 ON 字 / 在射程 |
| --success-bg | #122b22 | 开关 ON 底 |
| --success-bg-hover | #17382c | 开关 ON 悬停底 |
| --success-border | #1d4634 | 开关 ON 描边 |
| --success-border-hover | #2b6a4b | 开关 ON 悬停描边 |
| --danger | #f87171 | 柔红: 超射程 / 异常 |
| --danger-bg | #2a1517 | 错误底色备用 (日志错误行高亮, 当前可不用) |

规则: 窗口内任何颜色必须命中上表; 文件头常量块是唯一事实源, 禁止散落硬编码。

### 2.2 字体令牌 (家族 + 字号 pt + 字重)

| token | 用途 | 家族 | 字号 | 字重 |
| --- | --- | --- | --- | --- |
| font-title | 主标题「WARDOGS 炮兵助手」 | Microsoft YaHei UI | 14 | bold |
| font-meta | 版本副标题 / 行标签 / 字段标签 (武器、标定、读数标签列) | Microsoft YaHei UI | 9 | regular |
| font-meta-sm | 标定子标签 / 底部热键提示行 | Microsoft YaHei UI | 8 | regular |
| font-btn | 主尺寸按钮文字 (primary / toggle / 保存设置) | Microsoft YaHei UI | 10 | primary=bold, 其余 regular |
| font-btn-sm | 标定行小按钮 / 日志折叠按钮 | Microsoft YaHei UI | 8 | regular |
| font-badge | 射程徽章 | Microsoft YaHei UI | 9 | bold |
| font-value-hero | 主窗 距离/方位 行数值 | Consolas | 22 | bold |
| font-value | 仰角/弹道 行数值 | Consolas | 12 | bold |
| font-value-coord | 目标坐标 / 炮位坐标 行数值 | Consolas | 11 | bold |
| font-value-cjk | 装填/射程 行数值 | Microsoft YaHei UI | 10 | bold |
| font-mono-log | 日志正文 / 日志条最近事件 | Consolas | 9 | regular |
| font-hud-hero | HUD 距离行 | Consolas | 28 | bold |
| font-hud-sub | HUD 方位/仰角行 | Microsoft YaHei UI | 11 | regular |
| font-hud-xy | HUD 目标/炮位行 | Consolas | 10 | regular |
| font-hud-tip | HUD 提示行 | Microsoft YaHei UI | 8 | regular |

中英混排建议:
- 纯数字 / 数字主导且实时跳动的串一律 Consolas, 用等宽消抖; 度数符、mil、x/y 在 Consolas 均有字形。
- 中文主导的串 (含 2~4 个汉字 + 数字) 整串用 Microsoft YaHei UI, 不要拆两个 Label 拼接。
- 「数字主导 + 静态中文后缀」(仰角行的「低弹道/高弹道」) 保留 Consolas, 中文回退到系统雅黑: 回退字形是静态后缀, 不随刷新跳动, 视觉可接受。
- Segoe UI / Segoe UI Semibold 不用于含中文的串 (无 CJK 字形, 整串回退雅黑会字重错位); Segoe UI Semibold 仅预留纯拉丁场景, 当前无实例。
- 中文一律雅黑系 (Microsoft YaHei UI); Cascadia Mono 不保证存在, 等宽一律 Consolas 保底。

### 2.3 间距令牌 (4px 网格)

| token | 值 | 落地 (padx / pady 具体值) |
| --- | --- | --- |
| sp-1 | 4 | 按钮网格单元 padx=4 pady=4; 读数行 pady=4; 日志条/日志体 pack pady=(0,4) |
| sp-2 | 8 | 小按钮 padx=8; 徽章 padx=8; 读数数值 Label padx=8; 日志最近事件 padx=(8,12); 标定卡末行 pady=(0,8) |
| sp-3 | 12 | 主尺寸按钮 padx=12; HUD 各行 padx=12; 标定卡内行 padx=12; 标题区上边距 12 |
| sp-4 | 16 | 窗口边距: 所有区块 pack padx=16; 读数卡内行 padx=16; 提示行 padx=16 |

区块外边距纵向节奏 (自上而下, pady 元组):
标题区 (12,4) → 武器行 (4,0) → 读数卡 (8,4) → 按钮区 (4,4) → 标定卡 (0,4) → 日志条 (0,4) → 日志体 (0,4) → 提示行 (4,8)。
全部为 4 的倍数; 现状里的 2 / 6 / 18 等 off-grid 值一并归整。

### 2.4 描边与高亮厚度

| token | 厚度 | 落地 |
| --- | --- | --- |
| stroke-1 | 1px | 卡片/标定卡/日志条: Frame bd=0 + highlightthickness=1 + highlightbackground=--border-1; 按钮 highlightthickness=1; 输入框 highlightthickness=1 |
| stroke-hover | 1px | 悬停只换色不换厚: highlightbackground=--border-hover |
| stroke-focus | 1px | 输入框焦点 highlightcolor=--accent; 组合框焦点 bordercolor/lightcolor/darkcolor=--accent (全窗唯一焦点反馈) |
| stroke-0 | 0 | HUD 全窗无描边; 日志条内折叠按钮 highlightthickness=0; primary 按钮 highlightthickness=1 但描边色=填充色, 视觉为 0 |
| 禁用 | >=2px | 全窗不出现 2px 及以上描边, 不出现双重描边 |

### 2.5 窗口与标题栏 (沉浸式暗色)

- 建窗后 (`update_idletasks()` 之后) 对真实顶层 HWND 调用:
  `ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)`
  其中 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 1809+ / build 17763+)。
- 必须 try/except 静默回落: 老系统上标题栏保持系统默认, 不影响功能。
- 效果: 系统标题栏底色并入 --bg-app, 窗口视觉高度 +1 行, 暗色游戏环境下 Alt-Tab 切换无白边闪跳。
- HUD 子窗为 overrideredirect, 无标题栏, 不适用本属性。

## 3. 组件规范 (tk 可实现配方)

通用: 所有 tk.Button 保持 bd=0 / relief="flat" / cursor="hand2" / takefocus=0; 悬停态沿用现有 Enter/Leave 绑定机制, 本规范只给三态色表。active* 一律等于同状态 hover 值 (按下不额外变暗, tk 无过渡能力, 避免闪跳)。

### C1 主操作按钮 (读取目标 F1 / 读取炮位 F2)

- 常态: bg=#f59e0b, fg=#101317, 描边色=#f59e0b
- 悬停: bg=#fbbf24, fg=#101317, 描边色=#fbbf24
- active: activebackground=#fbbf24, activeforeground=#101317
- font=("Microsoft YaHei UI", 10, "bold")
- padx=12, pady=8
- highlightthickness=1; highlightbackground=highlightcolor=当前 bg (视觉无边)
- 布局不变: grid 两列 sticky="ew", 单元 padx=4 pady=4
- 角色: 全窗唯一实心色块, 琥珀家族最深的一档, 与读数 hero 的白色形成「操作 vs 信息」二分。

### C2 开关按钮 (HUD F5 / 实时跟踪 F8 / QTE 自动 F9) — 三态色表

| 状态 | bg | fg | 描边 (highlightbackground/highlightcolor) |
| --- | --- | --- | --- |
| OFF 常态 | #12151a | #8b95a6 | #1f242c |
| OFF 悬停 | #1a1f26 | #e9ecef | #2a313c |
| ON 常态 | #122b22 | #34d399 | #1d4634 |
| ON 悬停 | #17382c | #34d399 | #2b6a4b |

- active* = 同状态悬停值。
- font=("Microsoft YaHei UI", 10) regular; padx=12, pady=8; highlightthickness=1。
- 状态符号 (● 开 / ○ 关) 与「开/关」字样由现有逻辑生成, 属文案红线, 规范不碰; 颜色是第二通道。
- OFF 用中性灰而不用红: 「关」是常态不是错误。

### C3 次要按钮 (ghost) — 三种尺寸同一色表

色表: 常态 bg=#12151a fg=#e9ecef 描边=#1f242c; 悬停 bg=#1a1f26 fg=#e9ecef 描边=#2a313c; active=悬停值。

| 实例 | font | padx/pady | highlightthickness |
| --- | --- | --- | --- |
| 保存设置 | ("Microsoft YaHei UI", 10) | 12 / 8 | 1 |
| 日志折叠按钮 (▸/▾ 运行日志) | ("Microsoft YaHei UI", 9) | 8 / 4 | 0 (日志条自带 1px 描边) |
| 标定行小按钮 (取距离/记录/清空) | ("Microsoft YaHei UI", 8) | 8 / 4 | 1 |

### C4 读数卡片与五行读数

- 卡片容器: Frame bg=#12151a, bd=0, highlightthickness=1, highlightbackground=#1f242c; pack fill="x" padx=16 pady=(8,4)。
- 行容器: Frame bg=#12151a, pack fill="x" padx=16, pady 见下表。
- 标签列: Label font=("Microsoft YaHei UI", 9), fg=#8b95a6, width=9, anchor="w" (不变; 标签列是「尺子」, 不随行层级变大变小)。
- 数值列: Label anchor="w", padx=8, 字体/颜色见下表。

| 行 | 数值 font | 数值颜色 | 行 pady |
| --- | --- | --- | --- |
| 距离 / 方位 (hero) | ("Consolas", 22, "bold") | #e9ecef | (8,4) |
| 目标坐标 | ("Consolas", 11, "bold") | #fbbf24 | 4 |
| 炮位坐标 | ("Consolas", 11, "bold") | #fbbf24 | 4 |
| 仰角 / 弹道 | ("Consolas", 12, "bold") | #e9ecef | 4 |
| 装填 / 射程 | ("Microsoft YaHei UI", 10, "bold") | 动态: 在射程 #34d399 / 超射程 #f87171 / 无解 #8b95a6 | (4,8) |

- 空值 "--" 沿用各行颜色; 装填行无解时复位 #8b95a6 (保留现有复位逻辑, 防止残留红)。

### C5 输入框 (标定距离 / 密位 / 距离修正)

- tk.Entry: bg=#0d1014, fg=#e9ecef, insertbackground=#e9ecef, relief="flat", bd=0,
  font=("Consolas", 10), highlightthickness=1, highlightbackground=#1f242c, highlightcolor=#fbbf24。
- 焦点反馈 = 1px 琥珀描边 (highlightcolor), 全窗唯一焦点视觉。
- Entry 无 padx/pady 选项: 垂直手感由字号 + 1px 描边控制, 不加 ipady; 水平宽度沿用 width 字符数 (7/6/6)。
- 输入值用 --text-1 而非琥珀: 琥珀留给「读数与徽章」, 输入区保持中性, 焦点时才亮琥珀。

### C6 只读组合框 (武器选择, ttk clam 逐项配置)

```python
st = ttk.Style(root); st.theme_use("clam")
st.configure("TCombobox",
             fieldbackground="#0d1014",   # readonly 字段内底
             background="#12151a",        # 箭头按钮区
             foreground="#e9ecef",
             arrowcolor="#8b95a6",
             bordercolor="#1f242c",
             lightcolor="#1f242c",        # clam 焦点环(左上)
             darkcolor="#1f242c",         # clam 焦点环(右下)
             padding=(8, 4),
             font=("Microsoft YaHei UI", 10))
st.map("TCombobox",
       fieldbackground=[("readonly", "#0d1014")],
       foreground=[("readonly", "#e9ecef")],
       bordercolor=[("focus", "#fbbf24")],
       lightcolor=[("focus", "#fbbf24")],
       darkcolor=[("focus", "#fbbf24")],
       arrowcolor=[("active", "#e9ecef")])
for _k, _v in (("background", "#12151a"), ("foreground", "#e9ecef"),
               ("selectBackground", "#2a313c"), ("selectForeground", "#e9ecef")):
    root.option_add("*TCombobox*Listbox." + _k, _v)
```

- width=11 / state="readonly" / 下拉值来源一律不变。
- 下拉列表 relief 保持默认 flat 观感: 不给 Listbox 加 solid 描边 (Tk 的 solid 描边色不可控, 易出黑边)。

### C7 射程徽章

- Label: bg=#2a2013, fg=#fbbf24, font=("Microsoft YaHei UI", 9, "bold"), padx=8, pady=4。
- 无描边、无悬停 (Label); pack side="right"。
- 语义: 琥珀 10% 感的纯色块 + 琥珀字, 是强调色家族里最轻的一档, 与主操作实心琥珀首尾呼应。
- 空文本时保留空 Label 占位 (现状行为), 避免武器行高度跳变。

### C8 折叠日志条与日志正文

- 日志条: Frame bg=#12151a, bd=0, highlightthickness=1, highlightbackground=#1f242c; pack fill="x" padx=16 pady=(0,4)。
- 折叠按钮: 见 C3 中尺寸; highlightthickness=0。
- 最近事件: Label font=("Consolas", 9), fg=#8b95a6, anchor="w", pack side="left" fill="x" expand=True padx=(8,12)。
- 日志正文: ScrolledText bg=#0b0e13, fg=#8b95a6, font=("Consolas", 9), wrap="word",
  relief="flat", bd=0, highlightthickness=1, highlightbackground=#1f242c,
  insertbackground=#e9ecef, padx=8, pady=4; width=56 height=6 不变; pack padx=16 pady=(0,4)。
- 日志是 chrome 层: 9pt 等宽保证时间戳对齐, CJK 回退雅黑可接受。

### C9 底部热键提示行

- Label: bg=#0a0c0f, fg=#8b95a6, font=("Microsoft YaHei UI", 8), anchor="w", justify="left"。
- pack fill="x" padx=16 pady=(4,8) (现状 padx=18 归整到 16)。
- 三行提示是「查阅层」不是「操作层」: 8pt + 次级灰, 不与读数争对比度。

### C10 标题区 (主标题 + 版本副标题)

- 容器: Frame bg=#0a0c0f, pack fill="x" padx=16 pady=(12,4)。
- 主标题: Label font=("Microsoft YaHei UI", 14, "bold"), fg=#e9ecef, anchor="w", side="left"。
- 副标题: Label font=("Microsoft YaHei UI", 9), fg=#8b95a6, side="right" (文本含版本号与「鼠标取点」, 不变)。
- 不加下划线分隔、不加色块、不加内嵌 logo: 沉浸式暗色标题栏已把系统标题栏并入底色, 系统图标是唯一的品牌点; 分隔靠 2.3 的纵向节奏。

## 4. 信息层级 (五行读数)

- hero = 距离 / 方位行: Alt-Tab 瞟一眼的第一个结论是「打多远、朝哪打」, 给它全窗唯一 22pt + 纯白。
- L2 = 仰角 / 弹道: 装定炮口前的最后一个参数, 12pt 纯白。
- L3 = 目标坐标 / 炮位坐标: 参照信息, 需要核对时才看, 降为 11pt + 琥珀 (强调色但小字号 = 「重要但不急」)。
- L4 = 装填 / 射程: 状态行, 10pt, 颜色即状态 (绿/红/灰), 文字本身退居其次。
- 字号序列 (数值列): 22 → 12 → 11 → 10, 四档; 标签列恒 9pt 次级灰, 不随行层级变化。
- 同一语法复用到 HUD: 距离永远是最大 + 琥珀, 其余行逐级降。
- 对比度兜底: 暗底 (#12151a) 上 #e9ecef 约 13:1, #8b95a6 约 6.5:1, #fbbf24 约 10:1, 均过 WCAG AA; 琥珀不做正文长句色, 只做短读数与徽章。

## 5. HUD 规范 (置顶半透明小窗)

窗口属性 (不变, 仅记录): overrideredirect + topmost + toolwindow; alpha=0.72 (按住 F7 拖拽时 0.95);
WS_EX_TRANSPARENT 鼠标穿透; 尺寸沿用「最坏文案测量」自适应逻辑。

- 背景: 纯色 --bg-app #0a0c0f, 不用 --bg-card (半透明叠加下再抬亮会发灰); 透明度靠窗口 alpha, 不做逐控件透明。
- 圆角: **否**。overrideredirect + layered 窗若用 transparentcolor 抠圆角, 会与统一 alpha、鼠标穿透冲突, 收益不值得; 直角 + 深底在游戏画面上已经足够「浮」。
- 描边: 无 (stroke-0)。
- 内边距: 各行 padx=12; 纵向节奏见下表。

| 行 | 示例文案 | font | 颜色 | padx / pady |
| --- | --- | --- | --- | --- |
| 1 距离 hero | "1234 m" | ("Consolas", 28, "bold") | #fbbf24; 超射程 #f87171 | 12 / (8,0) |
| 2 方位·仰角 | "方位 234.5°  仰角 812 mil" | ("Microsoft YaHei UI", 11) | #e9ecef; 超射程 #f87171 | 12 / 0 |
| 3 目标 | "目标 x12.34 y56.78" | ("Consolas", 10) | #8b95a6 | 12 / 0 |
| 4 炮位 | "炮位 x12.34 y56.78" | ("Consolas", 10) | #8b95a6 | 12 / 0 |
| 5 提示 (chrome) | "射程 780~2629m · 按住F7拖动 · 双击关闭" | ("Microsoft YaHei UI", 8) | #8b95a6 | 12 / (4,8) |

- 可读性分层: 0.72 alpha 叠在亮色地图 (雪地/沙漠) 上, 次级灰会沉; 因此「必读层」只保留两行 (琥珀 hero + 白色副行), 坐标与提示是「需要再看层」, 沉下去是设计意图不是缺陷。
- 行数纪律: 数据 4 行 + chrome 1 行封顶, 不再加行; 新信息优先并入副行文案 (文案红线内由功能侧决定)。

## 6. 图标艺术指导: PZH-2000 自行火炮侧面剪影

废弃现瞄准环图标; 新图标延续琥珀家族, 任务栏 / Alt-Tab 识别连续。
母版 1024x1024, 单色琥珀剪影置于深色圆角方砖; 供工程师用 PIL 确定性绘制 (禁生成式模型)。

### 6.1 母版几何 (1024 坐标系, 绘制后整体平移 (-44, +40) 光学居中)

| 部件 | 几何 | 备注 |
| --- | --- | --- |
| 方砖 | rounded_rectangle [0,0,1024,1024], radius=190, fill=#0b0e12 | 圆角只在这里出现; 无描边无渐变 |
| 履带 | 体育场形 rounded_rectangle [164,614,696,728], radius=57 | fill=#fbbf24 |
| 负重轮 (仅 >=64px 母版) | 6 个负空间圆 r=44, 圆心 y=671, x=220,304,388,472,556,640 | 用方砖色 #0b0e12 挖空 |
| 车体 | polygon (184,616) (692,616) (692,566) (636,534) (184,534) | 右端斜切 = 前装甲倾角 |
| 炮塔 | polygon (396,534) (636,534) (636,472) (596,448) (416,448) (396,472) | 前后微斜切, 高约 86 |
| 炮管 | 旋转矩形: 枢轴 (600,486), 仰角 38°, 长 430, 根宽 56 → 口宽 42 | 四点: (582.8,463.9) (925.9,204.6) (951.7,237.6) (617.2,508.1); 口部平切, 无炮口制退器 (PzH 2000 实炮无证退器, 保持考据) |

- 炮管方向: 朝右上方 (阅读方向 = 前进方向); 仰角 38° 是「行军警戒到射击之间」的标志性角度, 不取最大仰角 65° (过陡, 16px 下糊成竖线)。
- 履带/轮子表现: 母版用体育场形履带 + 负空间负重轮; 轮子是唯一负空间细节, >=64px 才画。
- 平移后包围盒约 x[120,908] y[245,768], 四边留白 >=104, 炮管尖端不贴边。
- 剪影单色 #fbbf24; 不使用第二色、不加高光、不加地面阴影。

### 6.2 尺寸矩阵与 16px 简化版

| 尺寸 | 母版 | 差异 |
| --- | --- | --- |
| 16 / 24 / 32 | 简化版 | 车体+炮塔合并为单一 polygon: (184,616) (692,616) (692,566) (636,534) (636,472) (596,448) (416,448) (396,472) (396,534) (184,534); 炮管改单条直线 (同枢轴同仰角同长), 线宽 = max(2px, round(0.09 * size)); 无负重轮、无锥度、无负空间; 方砖 radius 按 18.5% 缩放 (16px 时约 3px) |
| 48 | 完整版 | 无负重轮 |
| 64 / 128 / 256 / 1024 | 完整版 | 含负重轮 |

- 16px 保留清单: 方砖 + 合并车体块 + 一条 2px 炮管线。其余全部丢弃; 规则: 任何特征在目标尺寸下 <2px 即删除。
- PIL 确定性绘制: 4x 超采样 (4096) 上 ImageDraw.polygon / rounded_rectangle, LANCZOS 降采样; 无随机数、无抗锯齿开关差异; 生成脚本参数表即本节表格 (建议命名 mk_icon3.py, 沿用 v1.7.0 手写 ICO 容器: 按尺寸嵌入对应母版 PNG 帧)。

## 7. 「不要做」清单

1. 不要渐变、不要阴影、不要窗口内圆角 (圆角只在图标方砖); 现代感 = 1px 描边 + 色块层级 + 4px 网格。
2. 不要引入第二强调色: 状态只有翡翠绿 / 柔红两枚, 禁止蓝紫青霓虹「科技感」; 琥珀家族是唯一强调。
3. 不要给按钮 / 徽章加装饰图标或 emoji (保留现有 ●/○/▸/▾ 状态符与 F 键提示, 它们是信息不是装饰)。
4. 不要全局放大字号: hero 只允许主窗距离行与 HUD 距离行两处, 其余读数 <=12pt —— 全都大 = 全都不大。
5. 不要动画与过渡: tk 无原生过渡能力, 状态切换一律瞬时换色, 悬停换色是唯一「动效」。

## 8. 给工程师的落地顺序 (先改哪几个常量收益最大)

1. **文件头颜色常量块** (BG/PANEL/PANEL_IN/BORDER/HOVER/GHOST_H_BG/PRIMARY_*/TOG_*/BADGE_BG 对齐第 2 节令牌, 新增 --text-3/--danger-bg 备用): 一次提交完成全窗底色与卡片/输入深度, 收益最大。
2. **`_btn_apply` 三态色表** (C1/C2/C3 的 bg/fg/描边 + hover): 按钮区是操作最频繁区域, 第二步即见整体气质变化。
3. **读数卡 rows 元组** (C4 字体/字号/颜色 + hero 行 pady=(8,4)): 核心「瞟一眼」体验, 改动仅 5 行元组。
4. **ttk 样式块 + Entry highlightcolor** (C5/C6): 消灭最后一处「系统控件感」(组合框白底残影、焦点黑环)。
5. **HUD 五行 font/fg/padx** (第 5 节表): 副屏 / Alt-Tab 体验收尾。
6. **标题区与提示行字号/间距归整** (C9/C10 + 2.3 纵向节奏), 并确认沉浸式暗色调用在位 (2.5)。
7. **图标脚本独立并行** (mk_icon3.py, 第 6 节), 不阻塞 1-6; 产出 app.ico/app.png 后按 v1.7.0 三处接线不变。

每步独立可提交、可截图对比; 回归只锁文案 / 热键 / 布局顺序, 上述全部改动都在安全区内。
