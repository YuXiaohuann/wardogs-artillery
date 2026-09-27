# -*- coding: utf-8 -*-
"""
WARDOGS 炮兵助手 - 图形前端 (tkinter)
=====================================
功能:
  * 大字读数: 目标坐标 / 炮位坐标 / 距离 / 方位 / 仰角 mil / 弹道 / 装填 / 射程窗口判定
  * 射表标定: 录入游戏内实测 (距离, 密位) 样本 -> 写入 range_tables.json 的 table, 分段线性插值
  * 鼠标取点: 全屏地图下鼠标旁显示白色悬停坐标标签, F1 读目标 / F2 读炮位 (同一通道)
  * 热键: F1 读目标  F2 读炮位  F5 HUD开关  F7按住拖拽HUD  F8 实时跟踪开关 (全局)
  * v2026-09-22: 按用户要求移除 语音播报功能(含F6) 与 6 个按钮 (3秒后读鼠标处目标/
    复制坐标/复制射击诸元/语音开关/QTE读一次/离线自检); F10 单次读 hotkey 保留
  * 距离 HUD: 置顶半透明小窗实时显示距离与目标/炮位坐标; 周期性强置顶+鼠标穿透, 无边框/窗口化游戏之上可见
  * v2026-09-14: 按用户要求移除 聊天框OCR炮位通道 / 框选ROI / 连续截屏监控
打包: pyinstaller --onefile --windowed --add-data "ocr_win.ps1;." gui_app.py
"""
import copy
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import shutil
import sys
import subprocess
import threading
import time
import io
import contextlib
import queue
import cv2
import tkinter as tk
from tkinter import ttk, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artillery_tool as core

# 版本号单一事实源: 窗口标题 / UI 副标题 / 单实例弹框都从这里取,
# 免得三处各写各的 (用户截图里就出现过"标题 v1.6.6 / 副标题 v1.1"这种不一致)。
APP_VER = "v1.6.12"

# 必须在 tk.Tk() 之前: 让进程从第一行起就是 DPI 感知, 与 mss 截屏 /
# GetPhysicalCursorPos 同处物理像素坐标系。否则 mss 会在后台轮询线程里
# 把感知状态"半路翻转"(本机 125% 缩放 => 桌面尺寸当场从 2880x1152 变 3600x1440),
# 期间保存/读取的 hud_pos 与窗口几何量都会错位。
core.enable_dpi_awareness()
core.enable_timer_resolution(1)   # v1.3: 定时器 1ms 格, QTE 快速窗/按键时长不被 15.6ms 量化

# --------------------------------------------------------------- 路径(冻结后)
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    for name in ("config.json", "range_tables.json"):
        dst, src = os.path.join(BASE, name), os.path.join(core.SCRIPT_DIR, name)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy(src, dst)
    core.CONFIG_PATH = os.path.join(BASE, "config.json")
    core.TABLES_PATH = os.path.join(BASE, "range_tables.json")
else:
    BASE = core.SCRIPT_DIR

BG, PANEL, FG, DIM, GOLD, GREEN, RED = "#0e1116", "#161a21", "#e8e6e0", "#8a8f98", "#d4a24a", "#7dd87d", "#e06c5a"
# v1.6.7 前台闸: "不是游戏"必须连续保持这么久才真的停摆 (瞬时抖动不闸)
FG_FLAP_MS = 400
BORDER, HOVER = "#262c37", "#39424f"   # 卡片描边 / 控件悬停 (v1.1)

def virtual_screen():
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))  # 虚拟屏(多显示器)原点与尺寸

class App:
    def __init__(self):
        self.cfg = core.load_config()
        # v1.4: 限制 OpenCV 内部并行度, 必须在任何 cv2 重活之前。
        # 默认用满 12 逻辑核时, 一次全横带识别烧 66 ms CPU 只换 18.7 ms 墙钟,
        # 而且每秒 20 次唤醒全部核心去和游戏的 worker 线程抢核; 限到 4 线程后
        # CPU 少 33%(全横带)/58%(窄条带), 墙钟几乎不变。0 = 沿用 OpenCV 默认。
        self._cv2_threads = core.configure_cv2_threads(self.cfg)
        self.q = queue.Queue()
        self.gun, self.target = None, None
        self.hover_last, self.hover_last_t = None, 0.0
        self._poll_on = True
        self._poll_warned = False
        self.hud = None
        self._hud_size = (320, 180)
        self._hud_passthrough = True
        self._qte_pressed, self._qte_empty = [], 0
        self._qte_hist = []
        self._qte_row = []
        self._qte_prim, self._qte_warned = None, False
        self._qte_last, self._qte_acted = [], False   # 上一帧画面 / 该帧是否已按
        self._qte_fast = 0.0                          # 按完后的快速重轮询截止时刻
        self._qte_act_ts = 0.0                        # 上次实际发键时刻 (补发计时用)
        self._qte_retries = 0                         # 本行已补发次数 (防键涌)
        self._qte_stalls = 0                          # 本行卡死重决策已用次数
        self._qte_band, self._qte_band_base = None, None   # 紧凑轮询窗 + 学它时的全横带
        self._qte_sweep_at = 0.0                      # 下次强制全横带扫描的时刻
        self._qte_stray_full_at = 0.0                # v1.6.10: 杂散单箭触发全扫的限流点
        self._qte_prev_dirs = None                    # v1.6.11: 上一帧读到的尾段 (消耗跃迁用)
        self._qte_open_head = None                    # v1.6.12: 开行防抖盯的行首方向
        self._qte_open_head_ts = 0.0                  # v1.6.12: 行首首次出现时刻
        self._qte_prev_dirs_t = 0.0                   # v1.6.11: 上一帧尾段的时刻
        # ---- v1.4 CPU 开销控制 ----
        self._qte_seen_at = 0.0                       # 上次真的看到箭行的时刻
        self._qte_full_at = 0.0                       # 下次强制全横带兜底扫描的时刻
        self._qte_zone_save = 0.0                     # 上次把窄条带写盘的时刻 (限流)
        self._qte_cpu_ema = None                      # 每轮扫描的纯 CPU 开销 EMA (秒)
        self._qte_duty = 0.0                          # 占空比 EMA (单核口径, 0~1)
        self._qte_region_now = None                   # 本轮实际截屏区 (界面读数用)
        # ---- v1.5 空闲 CPU 开销控制 ----
        self._fg_cache = {}                           # (hwnd, pid) -> 前台进程名
        # ---- v1.6 错误率控制 ----
        self._qte_red_seen_at = 0.0                   # 红行上次被认出的时刻 (快轮询窗)
        self._qte_red_probe_at = 0.0                  # 红探针上次跑的时刻 (空闲限流)
        self._qte_red_until = 0.0                   # v1.6.6 红行否决: 此时刻前禁止一切发键
        self._qte_red_logged = False                # v1.6.6 死行提示去重 (每次真发键后复位)
        self._fg_bad_since = 0.0                    # v1.6.7 前台闸迟滞: "不是游戏"起算点
        self._fg_bad_name = ""                      # v1.6.7 是谁抢了前台 (诊断日志)
        self._fg_log_at = 0.0
        self._qte_new_ts = 0.0                        # 当前候选新行首次读到的时刻 (稳定防抖)
        self._qte_margin = 1.0                        # 本帧选中行最小 margin (按键置信度)
        self._qte_last_key_ts = 0.0                   # 上一次实际 keydown 的计划时刻
        self._qte_cand = None                       # v1.6.1 按键候选 (d,idx,total)
        self._qte_cand_ts = 0.0                     # 候选首次出现时刻 (每键稳定窗)
        self._qte_retry_cand = None                 # 补发二次确认候选 (dirs 元组)
        self._qte_retry_cand_ts = 0.0
        self._qte_fg_back = 0.0                     # 上次回到游戏前台的时刻 (settle 窗)
        self._qte_gated_last = False                # 上一轮前台闸是否放行
        self._qte_trace_fh = None                   # qte_trace.log 句柄 (懒开)
        self._qte_alt = None                      # v1.6.2 与记账行不吻合的稳定读法
        self._qte_alt_ts = 0.0                    # 它首次出现的时刻 (复位闸门计时)
        self._qte_row_pend = None                 # v1.6.3 待确认的"增长行"(未提交)
        self._qte_row_pend_ts = 0.0               # 它首次出现的时刻 (TTL 作废计时)
        self._qte_conf_ts = 0.0                   # v1.6.4 最近一次"读法打架"(读花/噪声)的时刻
        self._qte_trim_cand = None                # v1.6.4 记账行尾部自愈: 候选修剪长度
        self._qte_trim_ts = 0.0
        self._qte_ghost = None                  # v1.6.4 复位时"还欠游戏的那一行"
        self._qte_ghost_ts, self._qte_ghost_off = 0.0, 0
        self._qte_stucks = 0                    # v1.6.4 卡死破局次数 (每行独立计)
        self._qte_adapt_probe_at = 0.0          # v1.6.5 自适应亮度通道上次探测时刻 (空闲限流)
        self._qte_adapt_src = None              # v1.6.5 当前接管的方法名 (用于一次性日志)

        self.root = tk.Tk()
        self.root.title("WARDOGS 炮兵助手 " + APP_VER)
        self.root.configure(bg=BG)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("TButton", background=PANEL, foreground=FG, borderwidth=1, focusthickness=0, padding=6)
        st.map("TButton", background=[("active", "#2a3140"), ("pressed", "#33405a")])
        st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG, arrowcolor=GOLD)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)])
        st.map("TCombobox", foreground=[("readonly", FG)])
        st.configure("TLabelframe", background=BG, foreground=GOLD)
        st.configure("TLabelframe.Label", background=BG, foreground=GOLD)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            import keyboard
            keyboard.on_press_key("f1", lambda e: self.root.after(0, self.do_target))
            keyboard.on_press_key("f2", lambda e: self.root.after(0, self.do_gun))
            keyboard.on_press_key("f5", lambda e: self.root.after(0, self.toggle_hud))
            keyboard.on_press_key("f7", lambda e: self._hud_hold(True))
            keyboard.on_release_key("f7", lambda e: self._hud_hold(False))
            keyboard.on_press_key("f8", lambda e: self.root.after(0, self.toggle_live))
            keyboard.on_press_key("f9", lambda e: self.root.after(0, self.toggle_qte))
            keyboard.on_press_key("f10", lambda e: self.root.after(0, self.qte_once))
        except Exception as ex:
            self.log("热键不可用: %s" % ex)
        # 后台悬停轮询线程放在最后启动: 它内部要调 self.root.after(),
        # 早于 tk.Tk() 启动的那几百毫秒里会抛 AttributeError 并被 except 静默吞掉。
        threading.Thread(target=self._hover_poller, daemon=True).start()
        threading.Thread(target=self._qte_poller, daemon=True).start()
        self.root.after(200, self.drain)
        self.root.after(2000, self._stat_tick)        # v1.4: 实时 CPU 读数
        self.log("就绪。游戏全屏地图: 鼠标移到目标坐标标签旁按 F1, 移到炮位坐标标签旁按 F2。")
        # v1.6.4: 旧版 config.json 里的 QTE 调参会把新护栏覆盖掉 (旧 exe 退出时写回),
        # 所以启动时按 cfg_ver 自动迁移; 这里把改了什么告诉用户, 免得"我明明调过"。
        mig = list(getattr(core, "LAST_MIGRATION", ()) or ())
        if mig:
            head = ", ".join("%s %s→%s" % (k, o, n) for k, o, n in mig[:6])
            self.log("已把 %d 项 QTE 调参迁移到 v%d 基线 (旧版遗留值): %s%s"
                     % (len(mig), getattr(core, "CFG_VER", 0), head,
                        " …" if len(mig) > 6 else ""))
        # v1.6.6: 迁移真改了值才会有 .bak; 告诉用户去哪找自己调过的旧值
        _bak = str(getattr(core, "LAST_BACKUP", "") or "")
        if _bak:
            self.log("旧配置已原样备份 (要对回自己调过的值就打开它): %s"
                     % os.path.basename(_bak))
        self._sync_toggles()
        if self.cfg.get("hud_on"):
            self.root.after(600, lambda: self.toggle_hud() if not self.hud else None)

    # ------------------------------------------------------------ UI
    @staticmethod
    def _ui_scale():
        """系统缩放系数(100%->1.0, 125%->1.25); 保留给回归测试/诊断用。"""
        try:
            return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
        except Exception:
            return 1.0

    def _btn_apply(self, b):
        """按钮配色 = kind(角色) x 状态 x 悬停:
        开关类开=绿色高亮(●), 关=灰(○); 主操作=蓝色; 次要=描边灰。"""
        kind, on, hover = getattr(b, "_kind", "ghost"), getattr(b, "_on", False), getattr(b, "_hover", False)
        if kind == "primary":
            bg, fg, bd = (("#2a4a77", "#dbe7ff", "#6ea2ff") if hover
                          else ("#1f3355", "#dbe7ff", "#4c8dff"))
        elif kind == "toggle" and on:
            bg, fg, bd = (("#1f4a2c", "#a7f3c4", "#34d877") if hover
                          else ("#153823", "#7ee2a0", "#22c55e"))
        else:
            bg, fg, bd = (("#1d232c", FG, HOVER) if hover
                          else (PANEL, DIM if kind == "toggle" else FG, BORDER))
        b.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                    highlightbackground=bd, highlightcolor=bd, highlightthickness=1)

    def _mk_btn(self, parent, text, cmd, kind="ghost", font=("Microsoft YaHei UI", 9),
                padx=10, pady=7):
        b = tk.Button(parent, text=text, command=cmd, bd=0, padx=padx, pady=pady,
                      font=font, cursor="hand2", takefocus=0)
        b._kind, b._on, b._hover = kind, False, False
        b.bind("<Enter>", lambda e: (setattr(b, "_hover", True), self._btn_apply(b)))
        b.bind("<Leave>", lambda e: (setattr(b, "_hover", False), self._btn_apply(b)))
        self._btn_apply(b)
        return b

    def _toggle_log(self):
        """运行日志面板开合: 收起时只留一条"最近事件"状态行, 窗口变紧凑。"""
        open_ = not bool(self.cfg.get("ui_log_open"))
        self.cfg["ui_log_open"] = open_
        if open_:
            self.logw.pack(fill="both", expand=True, before=self.hint, padx=16, pady=(0, 2))
            self.log_btn.configure(text="▾  运行日志")
        else:
            self.logw.pack_forget()
            self.log_btn.configure(text="▸  运行日志")

    def _build_ui(self):
        # --- 标题
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 2))
        tk.Label(head, text="WARDOGS 炮兵助手", bg=BG, fg=FG, anchor="w",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        tk.Label(head, text=APP_VER + " · 鼠标取点", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        # --- 武器行 (组合框 + 射程徽章)
        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=(2, 0))
        tk.Label(top, text="武器", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.weapon_var = tk.StringVar(value=self.cfg.get("weapon", "SPH-2"))
        try:
            names = list(core.load_tables()["weapons"].keys())
        except Exception:
            names = []
        if names and self.weapon_var.get() not in names:
            # config.json 存了射表里没有的武器名时 solve_shot 会 KeyError,
            # 表现为"读数/HUD 不再更新"; 这里回落到第一个可用武器。
            self.weapon_var.set(names[0])
        cb = ttk.Combobox(top, textvariable=self.weapon_var, width=11, state="readonly",
                          values=names)
        cb.pack(side="left", padx=(6, 8))
        cb.bind("<<ComboboxSelected>>", lambda e: (self.save_cfg(), self._weapon_range_sync()))
        self.wrange_lbl = tk.Label(top, text="", bg="#1a2010", fg=GOLD, padx=8, pady=2,
                                   font=("Microsoft YaHei UI", 9, "bold"))
        self.wrange_lbl.pack(side="right")

        # --- 读数卡片: 第一行放最常看的 距离/方位 (大字)
        card = tk.Frame(self.root, bg=PANEL, bd=0, highlightthickness=1,
                        highlightbackground=BORDER)
        card.pack(fill="x", padx=16, pady=(8, 4))
        self.vars = {}
        rows = [("dist", "距离 / 方位", FG, 17), ("target", "目标坐标", GOLD, 12),
                ("gun", "炮位坐标", GOLD, 12), ("elev", "仰角 / 弹道", FG, 12),
                ("extra", "装填 / 射程", DIM, 11)]
        for key, label, color, fs in rows:
            f = tk.Frame(card, bg=PANEL)
            f.pack(fill="x", padx=14, pady=3)
            tk.Label(f, text=label, bg=PANEL, fg=DIM, width=9, anchor="w",
                     font=("Microsoft YaHei UI", 9)).pack(side="left")
            v = tk.Label(f, text="--", bg=PANEL, fg=color, anchor="w",
                         font=("Consolas", fs, "bold"))
            v.pack(side="left", padx=8)
            self.vars[key] = v

        # --- 按钮区: 主操作(蓝) + 三个状态开关(开=绿高亮●) + 保存(描边)
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=16, pady=4)
        spec = [("读取目标", "F1", self.do_target, "primary"),
                ("读取炮位", "F2", self.do_gun, "primary"),
                ("HUD", "F5", self.toggle_hud, "toggle"),
                ("实时跟踪", "F8", self.toggle_live, "toggle"),
                ("QTE 自动", "F9", self.toggle_qte, "toggle"),
                ("保存设置", "", self.save_cfg, "ghost")]
        self.btn_map = {}
        for i, (base, hot, cmd, kind) in enumerate(spec):
            txt = "%s  %s" % (base, hot) if hot else base
            b = self._mk_btn(btns, txt, cmd, kind)
            b._base, b._hot = base, hot
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
            self.btn_map[base] = b
        self.hud_btn = self.btn_map["HUD"]
        self.live_btn = self.btn_map["实时跟踪"]
        self.qte_btn = self.btn_map["QTE 自动"]
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        # --- 标定卡片 (两行, 小号控件)
        cal = tk.Frame(self.root, bg=PANEL, bd=0, highlightthickness=1,
                       highlightbackground=BORDER)
        cal.pack(fill="x", padx=16, pady=(0, 4))
        row1 = tk.Frame(cal, bg=PANEL)
        row1.pack(fill="x", padx=12, pady=(6, 2))
        tk.Label(row1, text="标定", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.cal_d = tk.StringVar(); self.cal_m = tk.StringVar()
        tk.Label(row1, text="距离", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        tk.Entry(row1, textvariable=self.cal_d, width=7, bg="#0d1017", fg=GOLD,
                 insertbackground=FG, relief="flat", font=("Consolas", 10),
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=2)
        tk.Label(row1, text="m  密位", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        tk.Entry(row1, textvariable=self.cal_m, width=6, bg="#0d1017", fg=GOLD,
                 insertbackground=FG, relief="flat", font=("Consolas", 10),
                 highlightthickness=1, highlightbackground=BORDER).pack(side="left", padx=2)
        for txt, cmd in (("取距离", self.cal_use_dist), ("记录", self.cal_add),
                         ("清空", self.cal_clear)):
            self._mk_btn(row1, txt, cmd, "ghost",
                         font=("Microsoft YaHei UI", 8), padx=7, pady=2).pack(side="left", padx=2)
        row2 = tk.Frame(cal, bg=PANEL)
        row2.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(row2, text="距离修正", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.cal_off = tk.StringVar(value="0")
        self.cal_off_e = tk.Entry(row2, textvariable=self.cal_off, width=6, bg="#0d1017",
                                  fg=GOLD, insertbackground=FG, relief="flat",
                                  font=("Consolas", 10), highlightthickness=1,
                                  highlightbackground=BORDER)
        self.cal_off_e.pack(side="left", padx=(6, 2))
        self.cal_off_e.bind("<Return>", lambda ev: self.cal_set_offset())
        self.cal_off_e.bind("<FocusOut>", lambda ev: self.cal_set_offset())
        tk.Label(row2, text="m  真实 = 地图 + 修正", bg=PANEL, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        self.cal_lbl = tk.Label(row2, text="", bg=PANEL, fg=DIM,
                                font=("Microsoft YaHei UI", 8))
        self.cal_lbl.pack(side="left", padx=(4, 0))

        # --- 可折叠日志: 收起时只留"最近事件"一行
        logbar = tk.Frame(self.root, bg=PANEL, bd=0, highlightthickness=1,
                          highlightbackground=BORDER)
        logbar.pack(fill="x", padx=16, pady=(0, 2))
        self.log_btn = self._mk_btn(logbar, "▸  运行日志", self._toggle_log, "ghost",
                                    font=("Microsoft YaHei UI", 9), padx=8, pady=3)
        self.log_btn.configure(highlightthickness=0)
        self.log_btn.pack(side="left")
        self.log_last = tk.Label(logbar, text="就绪", bg=PANEL, fg=DIM, anchor="w",
                                 font=("Consolas", 8))
        self.log_last.pack(side="left", fill="x", expand=True, padx=(8, 10))
        self.logw = scrolledtext.ScrolledText(self.root, width=56, height=6, bg="#0b0e13", fg="#a8b3a8",
                                              insertbackground=FG, font=("Consolas", 9),
                                              wrap="word", relief="flat",
                                              highlightthickness=1, highlightbackground=BORDER)
        if self.cfg.get("ui_log_open"):
            self.logw.pack(fill="both", expand=True, padx=16, pady=(0, 2))
            self.log_btn.configure(text="▾  运行日志")

        self.hint = tk.Label(self.root, bg=BG, fg=DIM, anchor="w", justify="left",
                             font=("Microsoft YaHei UI", 8), text=self._hint_text())
        self.hint.pack(fill="x", padx=18, pady=(2, 8))
        self._weapon_range_sync()

    def _weapon_range_sync(self):
        """顶栏 + HUD 提示行 显示当前武器射程窗口 (数据源 range_tables.json)。
        L81 迫击炮 = 132~684 m (最远 684 m 已游戏内实测确认)。"""
        txt = ""
        try:
            w = core.load_tables()["weapons"][self.weapon_var.get()]
            rmin, rmax = w["range_m"]
            txt = "射程 %.0f~%.0fm" % (rmin, rmax)
        except Exception:
            pass
        if hasattr(self, "wrange_lbl"):
            self.wrange_lbl.configure(text=txt)
        self._cal_sync()
        if getattr(self, "hud_tip", None) is not None:
            try:
                self.hud_tip.configure(text="%s · 按住F7拖动 · 双击关闭" % txt if txt
                                       else "WARDOGS HUD · 按住F7拖动 · 双击关闭")
            except Exception:
                pass

    # ------------------------------------------------------------ 悬浮说明
    def _show_tip(self, text, ms=6000):
        """在鼠标附近弹一个无边框说明气泡 (自动消失), 不抢焦点、不影响游戏。"""
        self._hide_tip()
        try:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            try:
                t.attributes("-alpha", 0.96)
            except Exception:
                pass
            lab = tk.Label(t, text=text, bg="#161b22", fg="#c9d4c9", justify="left",
                           font=("Microsoft YaHei", 9), padx=10, pady=7,
                           highlightthickness=1, highlightbackground="#39424f")
            lab.pack()
            t.update_idletasks()
            x = self.root.winfo_pointerx() + 14
            y = self.root.winfo_pointery() + 18
            sw = t.winfo_screenwidth(); sh = t.winfo_screenheight()
            w = t.winfo_reqwidth(); h = t.winfo_reqheight()
            if x + w > sw - 6:
                x = max(6, sw - w - 6)
            if y + h > sh - 6:
                y = max(6, self.root.winfo_pointery() - h - 10)
            t.geometry("+%d+%d" % (x, y))
            self._tip_win = t
            self._tip_after = self.root.after(ms, self._hide_tip)
        except Exception:
            self._tip_win = None

    def _hide_tip(self):
        for a in ("_tip_after",):
            try:
                if getattr(self, a, None):
                    self.root.after_cancel(getattr(self, a)); setattr(self, a, None)
            except Exception:
                pass
        w = getattr(self, "_tip_win", None)
        if w is not None:
            self._tip_win = None
            try:
                w.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------ 射表标定
    def _save_tables(self, tb):
        with open(core.TABLES_PATH, "w", encoding="utf-8") as f:
            json.dump(tb, f, ensure_ascii=False, indent=2)
        core.invalidate_tables_cache()

    def _cal_sync(self):
        if not hasattr(self, "cal_lbl"):
            return
        wp = self.weapon_var.get()
        try:
            _off = float((core.load_tables()["weapons"][wp].get("calibration") or {})
                         .get("distance_offset_m", 0.0) or 0.0)
        except Exception:
            _off = 0.0
        if hasattr(self, "cal_off"):
            _want = "%g" % _off if _off else "0"
            try:
                _focus = self.root.focus_get() is self.cal_off_e
            except Exception:
                _focus = False
            if not _focus and self.cal_off.get().strip() != _want:
                self.cal_off.set(_want)
        try:
            w = core.load_tables()["weapons"][wp]
            n = len(w.get("table") or [])
            ft = w.get("firing_tables") or {}
            nc = len(ft.get("single") or ft.get("low") or [])
        except Exception:
            n, nc = 0, 0
        if nc:
            self.cal_lbl.configure(text="社区%d点+实测%d点" % (nc, n))
        else:
            self.cal_lbl.configure(text="%d点 %s" % (n, "分段插值" if n >= 2 else "端点插值"))
        self.cal_lbl.configure(cursor="hand2")
        tip = ("%s 仰角射表: 社区完整射表 %d 点 (wardogshub.gg, 2026-09-10)\n"
               "游戏内实测样本 %d 个, 在 ±1 m 内覆盖社区点并叠加修正;\n"
               "SPH-2 双弹道分别给 低弧/高弧 密位, L81 单弹道。\n"
               "想在射击场复核/修正时: 取距离 -> 填瞄具密位 -> 记录。\n"
               "距离修正: 真实距离 = 地图距离 + 修正值 (当前 %+g m, 2026-09-15 实测)。"
               % (wp, nc, n, _off))
        try:
            self.cal_lbl.unbind("<Enter>")
        except Exception:
            pass
        self.cal_lbl.bind("<Enter>", lambda e, t=tip: self._show_tip(t))
        self.cal_lbl.bind("<Leave>", lambda e: self._hide_tip())

    def cal_use_dist(self):
        s = self.shot()
        if s:
            self.cal_d.set("%.0f" % s["d"])
            self.log("已填入当前距离 %.0f m, 请填游戏内瞄具实测密位后点 [记录]" % s["d"])

    def cal_add(self):
        try:
            d = float(self.cal_d.get().strip()); m = float(self.cal_m.get().strip())
        except Exception:
            self.log("射表标定: 距离/密位必须是数字"); return
        if d <= 0:
            self.log("射表标定: 距离需 > 0"); return
        wp = self.weapon_var.get()
        tb = copy.deepcopy(core.load_tables()); w = tb["weapons"][wp]
        tab = [x for x in (w.get("table") or []) if abs(float(x["range_m"]) - d) > 1.0]
        tab.append({"range_m": round(d, 1), "mil": round(m, 1)})
        tab.sort(key=lambda x: float(x["range_m"]))
        w["table"] = tab
        self._save_tables(tb)
        self._cal_sync(); self.refresh()
        self.log("射表标定: %s @ %.0f m -> %.0f mil (共 %d 点, 已写入 %s)"
                 % (wp, d, m, len(tab), os.path.basename(core.TABLES_PATH)))

    def cal_clear(self):
        wp = self.weapon_var.get()
        tb = copy.deepcopy(core.load_tables()); tb["weapons"][wp]["table"] = []
        self._save_tables(tb)
        self._cal_sync(); self.refresh()
        self.log("已清空 %s 实测射表, 回到端点线性插值" % wp)

    def cal_set_offset(self):
        """距离标定: 真实距离 = 地图距离 * scale + offset (米)。写入 range_tables.json。"""
        try:
            v = float(self.cal_off.get().strip() or "0")
        except Exception:
            self.log("距离修正必须是数字 (米)"); return
        wp = self.weapon_var.get()
        tb = copy.deepcopy(core.load_tables()); w = tb["weapons"][wp]
        c = w.setdefault("calibration", {})
        if v:
            c["distance_offset_m"] = round(v, 2)
        else:
            c.pop("distance_offset_m", None)
        self._save_tables(tb); self._cal_sync(); self.refresh()
        self.log("距离修正已保存: %s 地图距离%+gm 后查表/显示/播报" % (wp, v))

    def _hint_text(self):
        return ("F1 目标 · F2 炮位 · F5 HUD · 按住 F7 拖动 HUD · F8 跟踪 · F9 QTE · F10 QTE 单次\n"
                "鼠标取点: 地图里光标放在坐标标签旁按键即读; 实时跟踪开启时悬停即自动更新"
                + self._stat_text())

    def _stat_text(self):
        """提示区第三行: QTE 的实时 CPU 占空比与识别区大小 (v1.4, 开销自查)。

        占空比 = 扫描消耗的 CPU 时间 / (扫描墙钟 + sleep), 以**单核**为 100%。
        调速器 (qte_cpu_budget) 就是拿它闭环的, 显示出来便于确认没有超标。"""
        if not self.cfg.get("qte_on"):
            return "\nQTE 关 · 轮询线程休眠中 · CPU ≈ 0"
        r = getattr(self, "_qte_region_now", None)
        mp = (r[2] * r[3] / 1e6) if r else 0.0
        bud = self.cfg.get("qte_cpu_budget", 25)
        nth = getattr(self, "_cv2_threads", "?")
        zone = "已学窄条带" if self.cfg.get("qte_zone") else "未学到条带(全横带)"
        return ("\nQTE 开 · CPU ≈ %.1f%% 单核 (预算 %s%%) · 识别区 %.2f MP · %s · cv2 %s 线程"
                % (100.0 * self._qte_duty, bud, mp, zone, nth))

    def _stat_tick(self):
        """每 2 s 刷新提示区的 CPU 读数 (Tk 标签赋值, 开销可忽略)。"""
        try:
            txt = self._hint_text()
            if self.hint.cget("text") != txt:
                self.hint.configure(text=txt)
        except Exception:
            pass
        self.root.after(2000, self._stat_tick)

    def log(self, msg):
        line = time.strftime("[%H:%M:%S] ") + msg
        if getattr(self, "log_last", None) is not None:  # 日志收起时的"最近事件"状态行
            try:
                t = line if len(line) <= 92 else line[:91] + "…"
                self.log_last.configure(text=t)
            except Exception:
                pass
        self.logw.insert("end", line + "\n")
        self.logw.see("end")
        try:  # 控件行数上限: 实时跟踪挂机一整晚也不会把内存吃满
            n = int(self.logw.index("end-1c").split(".")[0])
            if n > 3000:
                self.logw.delete("1.0", "%d.0" % (n - 2000))
        except Exception:
            pass
        try:  # 落盘日志: 游戏中按键后无需截图即可回看读取结果/失败原因
            p = os.path.join(BASE, "artillery.log")
            try:  # 超过 1MB 轮转一次, 否则 artillery.log 会无限增长
                if os.path.getsize(p) > 1024 * 1024:
                    os.replace(p, p + ".1")
            except OSError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d ") + line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ 动作
    def _own_rects(self):
        """仅 HUD 的物理像素矩形: HUD 置顶盖在游戏上, 光标停其上时会把 HUD
        自己显示的数字读回去。主窗在游戏中被盖住, 屏幕上是游戏画面, 不需避让。
        本函数在后台轮询线程里执行: 只用建窗时缓存的 HWND + Win32, 不碰 Tk
        (跨线程 winfo_id() 要么被 _tkinter 阻塞编组, 要么在主线程忙时卡住轮询)。"""
        rects = []
        hw = getattr(self, "_hud_hw", 0)
        if hw:
            try:
                r = wt.RECT()
                ctypes.windll.user32.GetWindowRect(hw, ctypes.byref(r))
                rects.append((r.left, r.top, r.right, r.bottom))
            except Exception:
                pass
        return rects

    def _cursor_over_self(self):
        """光标落在本工具窗口上时不写缓存, 避免把 HUD 自己显示的数字读回去。"""
        u = ctypes.windll.user32
        pt = wt.POINT()
        if not u.GetPhysicalCursorPos(ctypes.byref(pt)):
            u.GetCursorPos(ctypes.byref(pt))
        for l, t, r, b in self._own_rects():
            if l <= pt.x <= r and t <= pt.y <= b:
                return True
        return False

    def _hover_poller(self):
        """后台轮询鼠标旁悬停标签并缓存最近一次读数。
        按键瞬间标签若被游戏隐藏/偏移, F1/F2 仍可用缓存兜底 (2 s 内有效)。

        v1.4 自适应周期: 光标 2.5 s 内动过 -> 0.6 s (与原行为完全一致);
        一直没动 -> 放慢到 hover_idle_s (默认 1.5 s), 悬停 OCR 次数减 60%。
        注意**只能放慢不能停掉**: 游戏里镜头平移时, 光标不动标签内容也会变,
        停掉就再也追不上; 1.5 s 的滞后对"鼠标取点"这个用法无感。

        v1.5 又加两道空闲闸 —— 一轮"鼠标旁没标签"的空读实测 86 ms CPU
        (本进程 61 ms + OCR worker 25 ms), 是挂机时的头号开销:
          * 前台闸 hover_gate: 前台不是游戏进程 -> 0.5 s 轻量复查, 完全不截屏;
          * 静帧闸 hover_static_skip: 光标静止且鼠标近旁画面与上轮逐位相同
            -> 读数不可能变, 跳过 OCR 并把缓存时间戳顺延 (F1/F2 兜底照旧)。
        两道闸只影响**轮询**; F1/F2 的实时读取 (_read_hover) 不过闸。"""
        last_pos, last_move = None, time.time()
        sig_key = sig_img = sig_pos = None
        gated = False            # 启动即"未暂停": 不然第一轮会打一条多余的"恢复轮询"
        while self._poll_on:
            slow, idle = 1.5, False      # 异常路径也要有值: 末尾 sleep 用
            try:
                pos = core.mouse_pos()
                if pos != last_pos:
                    last_pos, last_move = pos, time.time()
                idle = time.time() - last_move >= 2.5
                try:
                    slow = max(0.6, float(self.cfg.get("hover_idle_s", 1.5)))
                except (TypeError, ValueError):
                    slow = 1.5
                if self.cfg.get("hover_gate", True) and not self._gate_open():
                    if gated is not True:      # 只在状态翻转时提示一次, 不刷屏
                        gated = True
                        self.q.put(("log", "前台不是游戏 (前台=%s): 暂停悬停轮询 "
                                           "(省 CPU); F1/F2 仍会实时读取"
                                           % (self._fg_bad_name or self._fg_who())))
                    sig_key = sig_img = sig_pos = None
                    time.sleep(0.5)
                    continue
                if gated:
                    gated = False
                    self.q.put(("log", "回到游戏前台: 恢复悬停轮询"))
                if idle and self.cfg.get("hover_static_skip", True):
                    k, im = core.hover_signature(self.cfg, pos)
                    if (im is not None and k == sig_key and pos == sig_pos
                            and sig_img is not None and im.shape == sig_img.shape
                            and core.np.array_equal(im, sig_img)):
                        if self.hover_last is not None and not self._cursor_over_self():
                            self.hover_last_t = time.time()   # 读数仍有效, 顺延缓存
                        time.sleep(slow)
                        continue
                    sig_key, sig_img, sig_pos = k, im, pos
                t = core.read_hover_target(self.cfg)
                if t and not self._cursor_over_self():
                    self.hover_last, self.hover_last_t = t, time.time()
                    if self.cfg.get("live_target", True) and t != self.target:
                        self.root.after(0, lambda t=t: self._live_target(t))
                self._poll_warned = False
            except Exception as ex:
                # 以前这里 except: pass —— OCR worker 挂掉/截屏失败时,
                # 用户只看到"实时跟踪不动了", 界面上一个字都不提示。
                sig_key = sig_img = sig_pos = None   # 作废静帧基线: 下一轮必须重试
                if not self._poll_warned:
                    self._poll_warned = True
                    self.q.put(("log", "后台悬停轮询异常: %s: %s" % (type(ex).__name__, ex)))
            # 关掉实时跟踪时不必追 0.6 s 的跟手周期: 那时没人消费读数, 只喂 2 s 缓存
            time.sleep(slow if (idle or not self.cfg.get("live_target", True)) else 0.6)

    def _dump_debug(self, dbg):
        """读取失败时把三次裁图与 OCR 原文存到 debug_hover/, 便于精确调框。"""
        try:
            d = os.path.join(BASE, "debug_hover")
            os.makedirs(d, exist_ok=True)
            for i, item in enumerate(dbg):
                core.cv2.imwrite(os.path.join(d, "crop%d.png" % i), item["img"])
                with open(os.path.join(d, "crop%d.txt" % i), "w", encoding="utf-8") as f:
                    f.write(json.dumps(item["box"]) + "\n" + (item["text"] or "(空)"))
        except Exception:
            pass

    def _read_hover(self, what):
        t, src = None, "实时"
        dbg = []
        try:
            t = core.read_hover_target(self.cfg, debug=dbg)
        except Exception as ex:
            self.log("读%s失败: %s" % (what, ex)); return None
        if not t and self.hover_last and (time.time() - self.hover_last_t) < 2.0:
            t, src = self.hover_last, "悬停缓存"
        if t:
            self.log("%s: x%.2f y%.2f (%s)" % (what, t[0], t[1], src))
        else:
            self._dump_debug(dbg)
            self.log("鼠标旁没读到悬停标签 (全屏地图里把鼠标移到坐标标签旁再试; 诊断图已存 debug_hover/)")
        return t

    def do_target(self):
        t = self._read_hover("目标")
        if t:
            self.target = t
        self.refresh()

    def do_gun(self):
        """F2 鼠标取点读炮位: 与 F1 读目标同一通道 (鼠标旁悬停坐标标签)。"""
        g = self._read_hover("炮位")
        if g:
            self.gun = g
        self.refresh()

    def shot(self):
        if not (self.target and self.gun):
            self.log("需要同时有目标与炮位"); return None
        g = self.gun
        d = math.hypot(self.target[0] - g[0], self.target[1] - g[1]) * 100.0
        az = core.azimuth_deg(g[0], g[1], self.target[0], self.target[1])
        sol = core.solve_shot(core.load_tables(), self.weapon_var.get(), d)
        de = float(sol.get("dist_eff_m", d))   # 标定后真实距离
        txt = ("距离%.0fm 方位%.1f° 仰角%s %s%s"
               % (de, az, sol["mil_txt"], sol["arc"],
                  "" if sol["in_range"] else " " + sol.get("range_note", "")))
        return {"d": de, "d_raw": d, "az": az, "sol": sol, "text": txt}

    def refresh(self):
        """冻结版(--windowed)没有控制台, Tk 回调里抛的异常会被静默丢弃,
        表现就是"实时跟踪/HUD 不再更新"。统一兜住并写进 artillery.log。"""
        try:
            self._refresh()
        except Exception as ex:
            self.log("刷新异常: %s: %s" % (type(ex).__name__, ex))

    def _refresh(self):
        v = self.vars
        v["target"].configure(text=("x%.2f  y%.2f" % self.target) if self.target else "--")
        v["gun"].configure(text=("x%.2f  y%.2f" % self.gun) if self.gun else "--")
        s = self.shot() if (self.target and self.gun) else None
        if s:
            v["dist"].configure(text="%.0f m   %.1f°" % (s["d"], s["az"]))
            v["elev"].configure(text="%s   %s" % (s["sol"]["mil_txt"], s["sol"]["arc"]))
            ok = s["sol"]["in_range"]
            v["extra"].configure(text="装填 %ss   %s" % (s["sol"]["reload_s"],
                                 s["sol"].get("range_note", "在射程内" if ok else "!! 超射程")),
                                 fg=GREEN if ok else RED)
            if s["sol"]["mil_low"] is not None and s["sol"]["mil_high"] is not None:
                sub = "方位 %.1f°  低%.0f/高%.0f mil" % (s["az"], s["sol"]["mil_low"], s["sol"]["mil_high"])
            else:
                sub = "方位 %.1f°  仰角 %s" % (s["az"], s["sol"]["mil_txt"])
            self._hud_update("%.0f m" % s["d"], sub,
                             ok=ok, state=s["sol"].get("range_state", "ok"),
                             xy1="目标 x%.2f y%.2f" % self.target,
                             xy2="炮位 x%.2f y%.2f" % self.gun)
        else:
            v["dist"].configure(text="--"); v["elev"].configure(text="--")
            v["extra"].configure(text="--", fg=DIM)   # 复位颜色, 别留着上次的超射程红
            self._hud_update("-- m", "方位 --  仰角 --", ok=None, state="",
                             xy1=("目标 x%.2f y%.2f" % self.target) if self.target else None,
                             xy2=("炮位 x%.2f y%.2f" % self.gun) if self.gun else None)

    # ------------------------------------------------------------ HUD / 语音
    def _sync_toggles(self):
        """开关按钮状态可视: 开=绿色高亮 ●, 关=灰色 ○ (文本+颜色双通道, 一眼可读)。"""
        for name, on in (("hud_btn", self.hud),
                         ("live_btn", self.cfg.get("live_target", True)),
                         ("qte_btn", self.cfg.get("qte_on"))):
            b = getattr(self, name, None)
            if b is None:
                continue
            b._on = bool(on)
            txt = "%s  %s %s  %s" % (b._base, "●" if on else "○",
                                      "开" if on else "关", b._hot)
            b.configure(text=txt)
            self._btn_apply(b)

    def _game_foreground(self):
        """前台窗口是否属于游戏进程。QTE 闸门 (最高 20 Hz) 与悬停闸门 (2~10 Hz) 共用。

        v1.5 加 (hwnd, pid) -> 进程名 缓存: 未命中要三个 Win32 调用 (~55 us),
        命中只剩 GetForegroundWindow + 一次字典查 (~1 us), 而"前台不变"正是挂机常态。
        pid 一起进 key: hwnd 会被系统回收复用, 只按 hwnd 缓存可能把新窗口认成旧进程。
        任何异常都当作"是游戏"(True): 闸门只用来省 CPU, 绝不能挡住功能。"""
        try:
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            key = (hwnd, pid.value)
            name = self._fg_cache.get(key)
            if name is None:
                name = self._fg_name(pid.value)
                if len(self._fg_cache) > 32:
                    self._fg_cache.clear()
                self._fg_cache[key] = name
            want = str(self.cfg.get("qte_game_exe",
                                    "WardogsClient-Win64-Shipping.exe")).lower()
            return name == want
        except Exception:
            return True

    @staticmethod
    def _fg_name(pid):
        """pid -> 进程映像名 (小写, 不含路径); 拿不到返回空串。"""
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            n = wt.DWORD(520)
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(n)):
                return ""
            return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)

    def _fg_who(self):
        """诊断: 当前前台窗口所属进程名 (拿不到就报 pid)。只读, 不改任何窗口状态。"""
        try:
            u = ctypes.windll.user32
            hwnd = u.GetForegroundWindow()
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return self._fg_name(pid.value) or ("pid:%d" % pid.value)
        except Exception:
            return "?"

    def _qte_row_active(self):
        """v1.6.7: 行进行中 (已按过键 / 1 s 内见过箭行) -> 前台闸不许中途关。"""
        if bool(getattr(self, "_qte_pressed", ())):
            return True
        return (time.time() - getattr(self, "_qte_seen_at", 0.0)) < 1.0

    def _gate_open(self):
        """前台闸 (v1.6.7): 迟滞 + 行内宽限 + 诊断。

        原来 _game_foreground() 一帧返回 False 就立刻停摆, 实机上被各种瞬时前台
        抖动打断 —— 日志里"前台不是游戏 / 回到游戏前台"每 1~2 s 翻一次, QTE 刚按
        1~3 支就被闸住好几秒, 正是用户报的"输入一两个/三个之后不动了, 卡几秒才继续"。
        两道补丁:
          ①迟滞 —— "不是游戏"必须连续保持 FG_FLAP_MS 才真的停摆, 抖动直接忽略;
          ②行内宽限 —— 本行已经开始 (按过键 / 1 s 内见过箭行) 时一律不许中途关闸,
            整行必须一口气打完。
        闸门的本意是挡"切去 QQ/浏览器乱发方向键", 不是拿来打断闭环的。发键前那道
        **严格**前台检查 (_qte_send_one 直接问 _game_foreground) 仍然原样保留。"""
        now = time.time()
        if self._game_foreground() or self._qte_row_active():
            self._fg_bad_since = 0.0
            return True
        if not self._fg_bad_since:
            self._fg_bad_since = now
            self._fg_bad_name = self._fg_who()
            if now - self._fg_log_at > 2.0:
                self._fg_log_at = now
                self.q.put(("log", "前台不是游戏 (前台=%s): 迟滞 %d ms 内继续扫, "
                                   "连续超时才停摆" % (self._fg_bad_name, FG_FLAP_MS)))
        return (now - self._fg_bad_since) * 1000.0 < FG_FLAP_MS

    def _qte_gate_ok(self):
        """前台窗口必须是游戏进程才跑 QTE 识别: 桌面/浏览器/聊天窗口里的亮色小图标
        偶尔能凑出"方向箭行", 不关闸会在切出游戏时乱按方向键。qte_gate=false 可关
        (样张联调用)。v1.5: 判定抽成 _game_foreground(), 与悬停轮询闸门共用带缓存的实现。"""
        if not self.cfg.get("qte_gate", True):
            return True
        return self._gate_open()

    # ------------------------------------------------------------ QTE 自动输入
    QTE_CN = {"up": "上", "down": "下", "left": "左", "right": "右"}

    def _qte_full_region(self):
        """QTE 的**全横带**截屏区: 主显示器中央一带 (仅作兜底全扫用)。
        虚拟屏全带 (含副屏) 太宽, 纯浪费 CPU; QTE 箭行只出现在游戏画面中央一带。
        v1.4: 常规轮询已不再用这里, 改用 _qte_zone_rect 学到的窄条带 (省 3 倍 CPU)。"""
        if self._qte_prim is None:
            try:
                import mss
                with mss.mss() as sct:
                    m = sct.monitors[1]          # mss 约定: [1] = 主显示器
                self._qte_prim = (m["left"], m["top"], m["width"], m["height"])
            except Exception:
                self._qte_prim = virtual_screen()
        x, y, w, h = self._qte_prim
        return (x + 0.12 * w, y + 0.30 * h, 0.76 * w, 0.55 * h)

    def _qte_zone_rect(self, full):
        """**持久窄条带** (v1.4 CPU 优化的主力): 历次真实识别学到的箭行 Y 范围。

        QTE 箭行是固定 UI 位置, 学到一次就一直有效 -> 存进 config.json 跨会话复用。
        存的是"相对全横带高度的比例", 换分辨率/换缩放自动跟着换算, 不会失配。
        实测 (i7-8700K / 3600x1440 / cv2 4 线程):
            全横带 2.17 MP  墙钟 45.8 ms  CPU 44.4 ms
            窄条带 0.61 MP  墙钟 21.0 ms  CPU 13.8 ms
        即 **CPU 少 69%、墙钟还快 25 ms** —— 省 CPU 与降延迟这次是同向的。
        条带数据不可信 (太窄/几乎等于全带/损坏) 时返回 None, 自动退回全横带。"""
        z = self.cfg.get("qte_zone")
        if not isinstance(z, (list, tuple)) or len(z) != 2:
            return None
        try:
            a, b = float(z[0]), float(z[1])
        except (TypeError, ValueError):
            return None
        if not (0.0 <= a < b <= 1.0) or (b - a) < 0.02 or (b - a) > 0.80:
            return None
        fx, fy, fw, fh = full
        return (fx, fy + a * fh, fw, (b - a) * fh)

    def _qte_learn_zone(self, region, row, full):
        """从本轮识别到的箭行学/更新持久窄条带, 并限流写回 config.json。"""
        try:
            hh = float(core.np.median([max(b["w"], b["h"]) for b in row]))
            pad = max(3.0 * hh, float(self.cfg.get("qte_zone_pad", 0.06))
                      * float(self._qte_prim[3]))
            y0 = region[1] + min(b["y"] - b["h"] / 2.0 for b in row) - pad
            y1 = region[1] + max(b["y"] + b["h"] / 2.0 for b in row) + pad
            fy, fh = full[1], full[3]
            a, b = (y0 - fy) / fh, (y1 - fy) / fh
            old = self.cfg.get("qte_zone")
            if isinstance(old, (list, tuple)) and len(old) == 2:
                try:                      # 取并集: 覆盖历次出现过的所有 Y 位置
                    a, b = min(a, float(old[0])), max(b, float(old[1]))
                except (TypeError, ValueError):
                    pass
            if b - a > 0.80:              # 并集快撑满全带 -> 丢掉历史只留本次
                a, b = (y0 - fy) / fh, (y1 - fy) / fh
            new = [round(max(0.0, a), 4), round(min(1.0, b), 4)]
            # old 可能是 None(没学过)/list(学过): 统一成"和上次不一样才写盘"
            changed = (new != list(old)) if isinstance(old, (list, tuple)) else True
            if changed:
                self.cfg["qte_zone"] = new
                now = time.time()
                if now - self._qte_zone_save >= 5.0:   # 限流: 至多 5 秒写一次盘
                    self._qte_zone_save = now
                    core.save_config(self.cfg)
        except Exception:
            pass

    def _qte_poller(self):
        """F9 自动模式后台线程: 抓屏 -> 识别方向箭行 -> 交给闭环状态机。

        v1.4 轮询节奏分三档:
          * QTE 关闭     : 0.5 s 空转 (原来仍按 50 ms 醒 20 次/秒, 纯浪费)
          * 闸门未开     : 0.1 s 轻量复查 (只查前台窗口, 实测 55 us/次)
          * 空闲扫描     : qte_poll_ms (默认 50 ms), 并受 qte_cpu_budget 调速器约束
          * 快速窗(~0.45s): qte_fast_ms (默认 2 ms), **不受调速器约束** ——
            这 0.45 s 是延迟关键期, 而每行 QTE 一共只持续 1~2 秒, CPU 影响有限。
        截屏区分三级 (tight / zone / full), 见 _qte_scan。"""
        while self._poll_on:
            p0, w0 = time.process_time(), time.perf_counter()
            if not self.cfg.get("qte_on"):
                self._qte_duty = 0.0
                time.sleep(0.5)
                continue
            gated = False
            try:
                gated = self._qte_gate_ok()
                if gated and not self._qte_gated_last:
                    self._qte_fg_back = time.time()   # v1.6.1: 回前台 settle 起点
                self._qte_gated_last = gated
                if gated:
                    self._qte_scan()
            except Exception as ex:
                if not self._qte_warned:
                    self._qte_warned = True
                    self.q.put(("log", "QTE 轮询异常: %s: %s" % (type(ex).__name__, ex)))
            if not gated:
                # 不喂 _qte_duty_ema: 这种轮次没有抓屏开销, 会把 CPU EMA 拉向 0,
                # 等闸门打开后调速器要好几轮才收敛回来。占空比读数直接衰减到 0。
                self._qte_duty *= 0.8
                time.sleep(0.1)          # 前台不是游戏: 不抓屏, 只轻量复查
                continue
            # 按完一键后短暂快速重轮询: 闭环靠"画面变化"推进,
            # 越早看到游戏消耗那一帧, 下一键就越早发出去。
            fast = time.time() < self._qte_fast
            delay = (float(self.cfg.get("qte_fast_ms", 2)) / 1000.0 if fast
                     else self._qte_idle_delay())
            if time.time() - self._qte_red_seen_at < 1.0:
                # v1.6 红行活跃期 (v1.6.6 起 = 失败反馈活跃期): 红行的出现/消失只有
                # 颜色通道看得见, 轮询加下限 (默认 8ms), 既能在红行消失的第一时间
                # 抢下一条白行, 又把这段的 CPU 占空比压在 ~13% 单核内。
                delay = max(delay, float(self.cfg.get("qte_red_poll_ms", 8)) / 1000.0)
            self._qte_duty_ema(p0, w0, delay)
            time.sleep(delay)

    def _qte_idle_delay(self):
        """空闲轮询间隔 = 配置值, 再被 CPU 预算调速器抬到刚好满足占空比上限。

            duty = cpu / (cpu + sleep) <= budget   =>   sleep >= cpu * (1/budget - 1)

        这是给**换分辨率**上的保险: 4K/8K 下全横带识别的 CPU 开销是 1440p 的 4 倍,
        没有调速器就会把一个核心吃满 (占空比 >100% 意味着根本追不上轮询间隔)。
        只在空闲态生效 —— 1 秒内看到过箭行或按过键时不降频, 否则会把首键/
        按键间隔拖长; 延迟关键期优先于省 CPU。
        上限 0.35 s: 极端花屏下也不会把检测拖到失去意义。

        (曾用 `_qte_band is not None` 当"正在跟踪", 但小窗一旦学到就永不作废,
         结果整局游戏调速器都是关闭的 —— 现改成按时间窗判断。)"""
        poll = max(0.04, float(self.cfg.get("qte_poll_ms", 50)) / 1000.0)
        try:
            budget = float(self.cfg.get("qte_cpu_budget", 25)) / 100.0
        except (TypeError, ValueError):
            budget = 0.0
        now = time.time()
        tracking = (now - self._qte_seen_at < 1.0
                    or now - self._qte_act_ts < 1.0)
        if budget > 0.0 and not tracking and self._qte_cpu_ema:
            poll = max(poll, self._qte_cpu_ema * (1.0 / budget - 1.0))
        return min(poll, 0.35)

    def _qte_duty_ema(self, p0, w0, delay):
        """记录本轮的纯 CPU 开销与 (扫描+sleep) 占空比, 供调速器和界面读数用。"""
        cpu = time.process_time() - p0
        wall = time.perf_counter() - w0 + delay
        e = self._qte_cpu_ema
        self._qte_cpu_ema = cpu if e is None else 0.8 * e + 0.2 * cpu
        d = cpu / wall if wall > 1e-6 else 0.0
        self._qte_duty = 0.85 * self._qte_duty + 0.15 * d

    def _qte_scan(self):
        """抓一帧、识别、喂给闭环状态机; 顺带维护截屏区与持久窄条带。

        三级截屏区 (v1.2 引入两级, v1.4 在中间加入跨会话学到的窄条带):
          1. tight = 已锁定箭行周围一圈 (~700x300 ≈ 0.2 MP, 墙钟 ~13 ms)
          2. zone  = 持久窄条带 (~2736x220 ≈ 0.6 MP, 墙钟 ~21 ms / CPU ~14 ms)
          3. full  = 主屏中央全横带 (0.76w x 0.55h ≈ 2.2 MP, 墙钟 ~46 ms / CPU ~44 ms)
        优先级 tight > zone > full; full 只作兜底, 每 qte_idle_sweep_ms (默认 1000)
        扫一次, 用来兜住"箭行跑到条带外 / 条带是从一次误识别学来的 / 换了分辨率"。
        锁定箭行后每隔 qte_sweep_ms 再全扫一次兜底 (箭行换位 / 右侧还在弹出新箭);
        本行已按过键且画面正常推进时放宽到 qte_sweep_relax_ms (v1.3)。
        刚按过键却抓空 -> 立刻全扫复核, 不在小窗里干等。
        **闭环状态机 _qte_maybe_send 的语义自 v1.1 起零改动。**"""
        full = self._qte_full_region()
        band = self._qte_band
        if band is not None and self._qte_band_base != full:
            band = self._qte_band = None       # 分辨率/主屏变了, 旧窗作废
        # v1.6.10 硬上限: 距上次全带兜底扫描超过 qte_idle_sweep_ms 就**必定**全扫一次,
        # 不管紧凑窗多"活跃"。旧代码里杂散单箭每帧都在把 _qte_sweep_at 往后推,
        # 实测 3470 s 里 full 只跑了 109 次 (tight 10504 次) -> 真行出现在别处时
        # 要等几秒才被偶发的一次全扫发现。这里到点就掐掉 tight/zone, 强制走 full。
        if time.time() >= self._qte_full_at:
            self._qte_sweep_at = 0.0
        tight = band is not None and time.time() < self._qte_sweep_at
        if tight:
            region, kind = band, "tight"
        else:
            zone = self._qte_zone_rect(full)
            if zone is not None and time.time() < self._qte_full_at:
                region, kind = zone, "zone"
            else:
                region, kind = full, "full"
        self._qte_region_now = region
        # v1.6 恒截彩色: 红填充箭行在灰度图上与它自己的描边同灰度, 完全不可见,
        # 颜色通道是唯一入口。BGRA->BGR + BGR2GRAY 比旧 BGRA2GRAY 每帧只贵 ~0.3~1ms。
        img = core.grab(region)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # 红箭探针限流: 空闲 (1s 内没见过箭行/没按过键) 时每 qte_red_probe_ms 才探一次
        # (探针自身带 stride-4 存在性闸, 无红帧 ~0.2ms, 4Hz ≈ 0.1% 单核);
        # 箭行/按键后 1s 内每轮都探, 保证红箭行出现的第一时间就能锁上。
        now = time.time()
        red_ok = (now - self._qte_red_seen_at < 1.0
                  or now - self._qte_seen_at < 1.0
                  or now - self._qte_act_ts < 1.0
                  or now - self._qte_red_probe_at
                  >= float(self.cfg.get("qte_red_probe_ms", 250)) / 1000.0)
        if red_ok:
            self._qte_red_probe_at = now
        # v1.6.5 自适应亮度通道限流: 与红探针同一套节奏 —— 1s 内见过箭行/按过键时
        # 每轮都允许 (延迟关键期不能漏), 空闲时按 qte_adapt_probe_ms 探一次。
        # 注意"允许"不等于"付费": find_qte_arrows 里它只在灰度+红都抓空时才真跑。
        adapt_ok = bool(self.cfg.get("qte_adapt", True)) and (
            now - self._qte_seen_at < 1.0
            or now - self._qte_act_ts < 1.0
            or now - self._qte_adapt_probe_at
            >= float(self.cfg.get("qte_adapt_probe_ms", 400)) / 1000.0)
        adapt_methods = None
        if adapt_ok:
            self._qte_adapt_probe_at = now
            _am = str(self.cfg.get("qte_adapt_methods", "") or "")
            adapt_methods = tuple(x.strip() for x in _am.replace(";", ",").split(",")
                                  if x.strip()) or None
        stats = {}
        # min_arrows=1: 行尾"已按的变绿消失"后只剩一支高亮箭, 也要能返回
        seq, boxes = core.find_qte_arrows(gray_img=gray,
                                          color_img=img if red_ok else None,
                                          min_arrows=1, debug=True, stats=stats,
                                          adapt=adapt_ok, adapt_methods=adapt_methods)
        self._qte_margin = stats.get("min_margin", 1.0) if seq else 1.0
        _src = stats.get("src")
        if stats.get("red_n") or stats.get("red_row"):
            # v1.6.6: 认出红箭 (哪怕只是否决) 就续快轮询窗 —— 红行消失/下一条白行
            # 出现的那一帧必须尽快看到, 否则首键要等空闲轮询周期。
            self._qte_red_seen_at = now
        if stats.get("red_row"):
            self._qte_red_veto(kind)
            return                  # 否决帧: 不学窗、不喂状态机、绝不发键
        if stats.get("left_clip") and not self._qte_pressed:
            # v1.6.7 左裁边护栏: 紧凑轮询窗把最左一支裁掉了 -> 这一帧读出来的是
            # 真值的**后缀**, 拿它开新行等于把第二支当首键发出去, 游戏判错整行变红
            # (v1.6.5 README 里点名的那个"首键失手全是后缀"病灶)。还没开行就不用
            # 这一帧, 并立刻退回全扫复核; 已经开行的照常推进 (后缀对已开行无害)。
            self._qte_sweep_at = 0.0
            return
        if seq and _src == "red":
            self._qte_red_seen_at = now   # 仅 red_press A/B 旧语义会走到这里
        # v1.6.5: 自适应通道接管时提示一次 (画面亮度超出主通道绝对窗), 便于事后
        # 从日志判断"这一行是主通道读到的还是兜底读到的"。主通道恢复即复位, 允许再提示。
        if seq and isinstance(_src, str) and _src.startswith("adapt"):
            if self._qte_adapt_src != _src:
                self._qte_adapt_src = _src
                self.q.put(("log", "QTE 自适应亮度通道接管 (%s): 画面亮度超出主通道窗口"
                            % _src))
        elif self._qte_adapt_src:
            self._qte_adapt_src = None
        # 兜底全扫周期 v1.3: 已锁小窗且本行已按过键(=状态机确认画面在正常推进)时
        # 放宽到 qte_sweep_relax_ms(默认 900), 省掉每 250ms 一次、单次 ~42ms 的
        # 全横带离群帧; 行消失/抓空/还没按过键 都会自然回到 qte_sweep_ms(250)。
        relax = (tight and len(self._qte_pressed) > 0
                 and time.time() - self._qte_act_ts < 1.5)
        sweep = float(self.cfg.get("qte_sweep_relax_ms", 900) if relax
                      else self.cfg.get("qte_sweep_ms", 250)) / 1000.0
        if seq:
            self._qte_seen_at = time.time()   # 调速器据此判断"是否在延迟关键期"
        # v1.6.10: 只有**像一行**的读法才允许学紧凑窗/窄条带、才允许把兜底全扫往后推。
        #   * >=2 支 = 多半是真行 (或正在消耗的行);
        #   * 1 支且记账行进行中 = 行尾最后一支, 照常跟着走;
        #   * 1 支且没有进行中的行 = 杂散误检 (short-ignore/lone-ignore 本来就会拒掉它),
        #     旧代码照样用它重学窗 + 推迟全扫 -> 真行在别处时被藏住几秒, 这就是本次病根。
        inrow = bool(self._qte_pressed or self._qte_row)
        trackable = len(seq) >= 2 or (bool(seq) and inrow)
        if trackable:
            # 只用**选中那一行**的 bbox 学小窗: boxes 是全图所有过检候选,
            # 拿它整份算会把远处干扰物一起圈进来, 窗就白缩小了。
            selx = set(x for x, _d in seq)
            row = [b for b in boxes if b["x"] in selx] or boxes
            nb = core.qte_track_region(region, row)
            if nb:
                self._qte_band, self._qte_band_base = nb, full
            self._qte_learn_zone(region, row, full)
            self._qte_sweep_at = time.time() + sweep
        elif not seq:
            if tight and time.time() - self._qte_act_ts < 1.0:
                self._qte_sweep_at = 0.0       # 按过键却抓空 -> 下一帧全扫复核
            elif not tight:
                self._qte_sweep_at = time.time() + sweep
        if (seq and not trackable and not inrow
                and time.time() >= self._qte_stray_full_at):
            # 杂散单箭 + 空闲 -> 真行多半在紧凑窗之外, 立刻退回全带找一遍。
            # 限流 250 ms 一次, 免得持续杂散把 CPU 打满 (全带一次 ~45 ms)。
            self._qte_stray_full_at = time.time() + 0.25
            self._qte_sweep_at = 0.0
            self._qte_full_at = 0.0
        if kind == "full":
            self._qte_full_at = time.time() + max(
                0.2, float(self.cfg.get("qte_idle_sweep_ms", 1000)) / 1000.0)
        self._qte_maybe_send(seq, kind)

    def _qte_red_veto(self, kind):
        """v1.6.6: 红色箭行 = 上一行输入失败的反馈, **不是**可按的提示。

        调用方 (_qte_scan) 在否决帧直接 return, 本函数负责收口: 把 qte_red_hold_ms
        设成发键禁闭期 (开新行/推进/补发/看门狗/发送线程在途键全停), 躲开失败动画的
        白残影与半帧; 闭环里若还有记账行, 记成死行清空, 等下一条白行重新走首键防抖。
        v1.6~v1.6.5 的病根就是把红行当 prompt 发键 (src="red" 直接进按键管线),
        等于向已经判死的行补键 —— 用户截图里整行变红正是这么来的。"""
        now = time.time()
        self._qte_red_until = now + float(self.cfg.get("qte_red_hold_ms", 250)) / 1000.0
        self._qte_empty = 0
        if self._qte_row or self._qte_pressed:
            self._qte_trace(kind, [], "red-fail-ignore")
            if not self._qte_red_logged:
                self._qte_red_logged = True
                self.q.put(("log", "QTE 红色箭行 = 上一行输入失败: 已忽略, 等下一行重新识别"))
            self._qte_clear()
        else:
            self._qte_trace(kind, [], "red-idle-ignore")

    def _qte_clear(self):
        """箭行消失: 清空闭环状态 (含画面闸), 下一条行重新防抖开始。"""
        self._qte_pressed, self._qte_row, self._qte_hist = [], [], []
        self._qte_last, self._qte_acted = [], False
        self._qte_act_ts, self._qte_retries = 0.0, 0
        self._qte_stalls = 0
        self._qte_new_ts = 0.0
        self._qte_cand, self._qte_cand_ts = None, 0.0
        self._qte_retry_cand, self._qte_retry_cand_ts = None, 0.0
        self._qte_alt, self._qte_alt_ts = None, 0.0
        self._qte_row_pend, self._qte_row_pend_ts = None, 0.0
        self._qte_trim_cand, self._qte_trim_ts = None, 0.0
        self._qte_conf_ts = 0.0
        self._qte_ghost, self._qte_ghost_ts, self._qte_ghost_off = None, 0.0, 0
        self._qte_stucks = 0
        self._qte_prev_dirs, self._qte_prev_dirs_t = None, 0.0
        self._qte_open_head, self._qte_open_head_ts = None, 0.0

    def _qte_trace(self, kind, dirs, dec):
        """v1.6.1 黑匣子: 每轮 QTE 决策写一行到 <exe 目录>/qte_trace.log。

        只在本轮有箭行或状态机非空时写 (空闲零开销); 4 MB 轮转一次。出错复盘时
        拿它对齐 artillery.log: 这里记"画面读到了什么 + 状态机为什么这么决定",
        artillery.log 只记"最终发了什么键", 两者相减就是错误来源。"""
        if not self.cfg.get("qte_trace", True):
            return
        try:
            if self._qte_trace_fh is None:
                p = os.path.join(os.path.dirname(core.CONFIG_PATH), "qte_trace.log")
                if os.path.exists(p) and os.path.getsize(p) > 4 * 1024 * 1024:
                    try:
                        os.replace(p, p + ".1")
                    except Exception:
                        pass
                self._qte_trace_fh = io.open(p, "a", encoding="utf-8", newline="")
            fh = self._qte_trace_fh
            fh.write("%.3f %-5s m=%.2f %-28s %s\n" % (
                time.time(), kind, self._qte_margin, ",".join(dirs) or "-", dec))
            fh.flush()
        except Exception:
            self._qte_trace_fh = None

    def _qte_maybe_send(self, seq, kind=""):
        """边检测边按 (视觉闭环), 每轮询至多一键。

        WARDOGS 实测行模型 = 消耗型: 按对的箭立刻变色(绿)而不再被检测到,
        所以"检测到的行"就是"还没按的尾段"。本函数以**画面为准**推进,
        不以"我按了几次"为准 (后者在显示滞后一个轮询周期时会失步)。

        v1.6.1 错误率收口 (用户报"很多输入错误", 逐帧复盘 02:12 日志定位):
        - **尾段读花不复位**: 已按 P 支后画面尾段首向与期望一致、只是后面几支
          读花 (动画/撕裂) -> 沉默等稳定读, 不再当"无关新行"复位重按首键
          (旧逻辑在这里连发重复首键 -> 游戏判错整行重来);
        - **未消耗不复位**: 画面首向 == 刚按过的那支且长度吻合 (游戏还没吃掉)
          -> 沉默, 交给补发/看门狗, 不复位;
        - **补发二次确认**: 画面不变满 qte_retry_ms 后再连续两轮读到同一尾段
          才补发, 单轮读屏卡顿不再触发误补发 (误补发 = 多发一支错键);

        v1.6.2 再加两道 (复盘 02:12 日志里 "右 (1/3)" 连按三次的完整成因):
        - **切片护栏**: 画面尾段只要是记账行的连续切片 (尾部读少几支 / 已按那支
          还没被吃掉 / 淡出残影) 就绝不复位; 若切片起点正好是下一支该按的键,
          照常按下 (以前这些形状会掉进 newrow-reset -> 下一帧读回完整行 ->
          把首键当新行再按一次 = 游戏判错整行重来);
        - **复位闸门**: 行没按完时, 与记账行不吻合的读法要连续稳定
          qte_row_switch_ms 才认作"无关新行"并复位 -> 瞬态读花 (撕裂/爆炸亮块/
          动画半帧, 1~3 帧) 不再引发复位重按;
        - 每次按键前过 _qte_press_gated 三道门 (回前台 settle / margin /
          候选稳定窗), 发送线程在 keydown 前再查一次前台闸 (键发去 QQ 的元凶)。

        v1.6.3 修掉逐键归因抓到的头号真缺陷 (蒙特卡洛 26 个错键里 17 个来自它):
        - **幻影增长行**: grow 分支原来只要**单帧**读到 "记账行 + 多一支" 就立刻
          把记账行撑长, 而"多检一支"读花与"弹出动画没走完"形状完全一样 -> 撑长后
          工具去按那支不存在的幻影箭 (错键 + 整行重来), 且这一行永远按不完。
          现改为**待确认 + 消耗佐证**: 增长读法先记候选, 只有后续某帧画面真的等于
          候选[off:] (游戏确实吃掉了键) 才提交; 幻影箭拿不到佐证, TTL 后作废;
        - **行长上限 qte_row_max (默认 6)**: 真行 2~4 支, 7/8 支必是粘连/读花;
        - **首键稳定窗 70 -> 110ms**: 盖住能稳定存在 120~300ms 的持续读花。

        v1.6.4 收口 (mc2 蒙特卡洛加入**弹出动画/行增长**建模后重新归因, 900 行):
        - **放行最后一支** (qte_slice_press_last): 画面尾段是记账行的切片且起点正好是
          下一支该按的键时照常按, 不再因为"记账行还剩 2 支而画面只剩 1 支"沉默到补发;
          这是 97/900 行超时 (用户报的"卡在一半不输入") 的主因;
        - **记账行已按完又长出新箭 -> 立即采纳** (qte_grow_adopt_done): 弹出动画的
          真增长发生在行尾, 那时没有待按的键可供"消耗佐证", 干等只会超时;
        - **尾部自愈修剪** (qte_trim_ms, 默认关): 画面长期只显示记账行的前半截尾段时
          把幻影尾巴剪掉, 让这一行能正常收口;
        - **候选增长行 TTL 提前到所有早退分支之前**: 原来只在 grow 分支查, 画面不变
          走 wait-consume 早退 -> 候选永不过期 (真 bug, 可能几十秒后被巧合佐证提交);
        - **自适应确认窗** (qte_press_stable_dirty_ms / qte_row_stable_dirty_ms /
          qte_conflict_ms, 默认关): 只在"画面刚出现过解释不了的读法"时才用长窗,
          干净时仍走短窗 -> 不加常态延迟也能顶住 120~300ms 的持续读花。
        其余语义自 v1.1 起不变: 画面==记录尾段->按下一支; 漏帧->画面对齐;
        4 帧空->复位; 孤箭忽略不清状态。
        v1.6.6: 红行禁闭期 (qte_red_hold_ms) 内本函数整体早退 —— 失败动画的白残影/
        半帧不能被当成新行首键, 补发/看门狗也不许向死行补键。"""
        if time.time() < getattr(self, "_qte_red_until", 0.0):
            if seq:
                self._qte_trace(kind, [d for _x, d in seq], "red-hold")
            return
        if not seq:
            self._qte_empty += 1
            if self._qte_empty >= 4:
                self._qte_clear()
            elif self._qte_pressed or self._qte_row:
                self._qte_trace(kind, [], "empty%d" % self._qte_empty)
            return
        self._qte_empty = 0
        dirs = [d for _x, d in seq]
        # v1.6.11 消耗跃迁: 上一帧尾段 [h, ...] 这一帧正好变成 [...] (缩掉的恰是刚按的
        # 那支 h) -> "游戏吃掉了键" 这一事实本身已经是逐位匹配的证据, 不必再花
        # qte_press_stable_ms 去确认同一个候选。错读要**恰好**等于期望尾段才能骗过
        # 这道门, 概率远低于稳定窗想挡的那类瞬态读花; 真被骗也只是早按一支,
        # 红行否决 + 补发兜底照旧。qte_consume_fast=false 可整块关掉。
        _now0 = time.time()
        _prev = getattr(self, "_qte_prev_dirs", None)
        _prev_t = getattr(self, "_qte_prev_dirs_t", 0.0)
        self._qte_prev_dirs, self._qte_prev_dirs_t = list(dirs), _now0
        fast = bool(self.cfg.get("qte_consume_fast", True)
                    and _prev and dirs and _now0 - _prev_t <= 0.4
                    and self._qte_pressed and _prev[0] == self._qte_pressed[-1]
                    and dirs == _prev[1:])
        static = str(self.cfg.get("qte_model", "consume")).lower().startswith("static")
        # v1.6.4: 候选增长行的 TTL 作废必须在**任何早退分支之前**做。原来只在 grow
        # 分支里查, 而"画面不变"会在上面走 wait-consume 提前 return -> 候选永不过期,
        # 几十秒后还可能被一条巧合的尾段佐证提交 = 记账行凭空多出一支幻影箭。
        if self._qte_row_pend is not None:
            _gttl = float(self.cfg.get("qte_grow_ttl_ms", 600)) / 1000.0
            if _gttl > 0 and time.time() - self._qte_row_pend_ts > _gttl:
                self._qte_row_pend, self._qte_row_pend_ts = None, 0.0
                self._qte_trace(kind, dirs, "grow-expire")
        # v1.6.4 卡死破局: 旧的补发(qte_retry_ms)/看门狗(qte_stall_ms) 全部挂在下面
        # "画面完全不变" 的分支里, 而读花/爆炸闪光会让画面**一直变** -> 那两个机制
        # 永远进不去, 一行可以无声无息挂死 (用户报的"极低概率卡在一半不输入了")。
        # 这里改成按"多久没发出任何键"计时, 与画面是否变化无关: 到点就按画面把记账
        # 回纠并按最左那支; 画面对不上记账行就整行复位重来。每行最多 qte_stuck_max 次,
        # 暂停帧/静态样张不会被无限重敲。
        now0 = time.time()
        _stk = float(self.cfg.get("qte_stuck_ms", 1200)) / 1000.0
        _R0, _P0 = self._qte_row, self._qte_pressed
        if (_stk > 0 and _P0 and len(_P0) < len(_R0) and self._qte_act_ts
                and now0 - self._qte_act_ts >= _stk
                and self._qte_stucks < int(self.cfg.get("qte_stuck_max", 2))
                and self._qte_margin
                >= float(self.cfg.get("qte_press_margin", 0.10))):
            self._qte_stucks += 1
            self._qte_retries, self._qte_act_ts = 0, now0
            off = -1
            for o in range(len(_R0), -1, -1):
                if dirs and dirs == _R0[o:o + len(dirs)]:
                    off = o
                    break
            if off >= 0:
                self._qte_pressed = _R0[:off]      # 按画面把记账回纠 (画面才是真相)
                self._qte_last, self._qte_acted = [], False
                self._qte_trace(kind, dirs, "stuck-align off=%d" % off)
                self._qte_press_gated(dirs[0], off + 1, len(_R0), kind, dirs,
                                      via="stuck")
            else:
                # 画面与记账行完全对不上: 存下"还欠的那一行"再整行复位,
                # 后面若只剩一支孤箭且正是欠的那支, 由 _qte_ghost_lone 救回。
                self._qte_ghost, self._qte_ghost_ts = list(_R0), now0
                self._qte_ghost_off = len(_P0)
                self._qte_pressed, self._qte_row, self._qte_hist = [], [], []
                self._qte_last, self._qte_acted = [], False
                self._qte_alt, self._qte_alt_ts = None, 0.0
                self._qte_trace(kind, dirs, "stuck-reset")
            return
        if dirs == self._qte_last:
            if self._qte_acted and not static:
                # 画面未变 + 已按过 -> 等游戏消耗。按键可能丢失, 超过
                # qte_retry_ms 还没变化**且连续两轮读到同一尾段**才补发一次。
                rto = float(self.cfg.get("qte_retry_ms", 1200)) / 1000.0
                R0, P0 = self._qte_row, self._qte_pressed
                rmax = int(self.cfg.get("qte_retry_max", 3))
                sto = float(self.cfg.get("qte_stall_ms", 2500)) / 1000.0
                smax = int(self.cfg.get("qte_stall_max", 2))
                now = time.time()
                if (rto > 0 and len(P0) < len(R0) and self._qte_act_ts
                        and self._qte_retries < rmax
                        and now - self._qte_act_ts >= rto):
                    ck = tuple(dirs)
                    if self._qte_retry_cand != ck:
                        self._qte_retry_cand, self._qte_retry_cand_ts = ck, now
                        self._qte_trace(kind, dirs, "retry-arm")
                        return
                    if now - self._qte_retry_cand_ts < 0.02:
                        self._qte_trace(kind, dirs, "retry-confirm")
                        return
                    self._qte_retry_cand = None
                    self._qte_retries += 1
                    # 补发**画面最左那支**: 画面才是真相, 键丢失时游戏仍停在
                    # 原位, 用记账下标 R0[len(P0)] 会多跳一支 -> 补发错键。
                    if self._qte_margin >= float(self.cfg.get("qte_press_margin", 0.10)):
                        self._qte_trace(kind, dirs, "resend")
                        self._qte_resend(dirs[0], len(R0) - len(dirs) + 1, len(R0))
                elif (smax > 0 and sto > 0 and self._qte_retries >= rmax
                        and self._qte_act_ts
                        and now - self._qte_act_ts >= sto
                        and self._qte_stalls < smax):
                    # 卡死看门狗: 补发额度用尽画面仍不变 (连续丢键/权限拦键的
                    # 长突发)。冷却 qte_stall_ms 后强制重决策一次: 先按画面把
                    # 记账回纠 (真消耗数 = len(R0)-len(dirs)), 再打失配
                    # _qte_last 让下一轮走变化分支按画面最左支。每行至多
                    # qte_stall_max 次, 暂停帧/静态样张不会被无限重敲。
                    off_t = len(R0) - len(dirs)
                    if 0 <= off_t <= len(R0) and dirs == R0[off_t:]:
                        self._qte_pressed = R0[:off_t]
                    self._qte_stalls += 1
                    self._qte_last, self._qte_acted = [], False
                    self._qte_retries, self._qte_act_ts = 0, 0.0
                    self._qte_trace(kind, dirs, "stall-redecide")
                else:
                    self._qte_trace(kind, dirs, "wait-consume")
                return
            self._qte_trace(kind, dirs, "same-noact")
        else:
            self._qte_last, self._qte_acted = dirs, False
            self._qte_retries = 0                     # 画面变了 = 游戏有反应, 补发额度复位
            self._qte_retry_cand, self._qte_retry_cand_ts = None, 0.0
        P, R = self._qte_pressed, self._qte_row
        if P:
            if static and dirs == P:
                return                      # 静止型: 整行已按完, 等消失
            # 消耗型绝不能在上面返回: 重复行 (右,上,右,上 这类) 按到一半时
            # "剩下的白箭" 恰好等于 "已按前缀" (dirs==P), 旧代码在这里永久
            # 沉默 = 用户报的 "极低概率卡在一半不输入"。消耗型应继续走下面
            # 的尾段对齐, 按画面最左那支。
            if static and len(dirs) > len(P) and dirs[:len(P)] == P:
                self._qte_row = dirs        # 静止型: 行向右增长 -> 按新出现那支
                self._qte_press_gated(dirs[len(P)], len(P) + 1, len(dirs), kind, dirs,
                                      via="static-grow")
                return
            # v1.6.3 行增长改成"待确认 + 消耗佐证"(原 grow 单帧就采纳, 是蒙特卡洛
            # 逐键归因里 press:advance 错键的主因, 也在真实日志里造出 7/8 支的怪行):
            #   真增长 (弹出动画没走完; artillery.log 里 (2,3,3) 这类行) 与单帧
            #   "多检一支" 读花, 形状**完全一样** (dirs == 记账行 + 一支), 当场无法
            #   分辨。撑长记账行后工具会去按那支根本不存在的幻影箭 = 错键 + 整行重来,
            #   而且这一行永远按不完 (完成率被拖下去)。
            #   -> 先把增长读法记成候选 _qte_row_pend, **绝不动记账行**; 只有当后续
            #   某帧画面真的等于 候选[off:] (off >= 已按数, 即游戏确实吃掉了键、
            #   露出了更长的尾段) 才提交。幻影箭下一帧就没了, 永远拿不到佐证,
            #   超过 qte_grow_ttl_ms(默认 600) 自动作废。真增长只多付一个轮询周期
            #   (~8ms), 而候选带 TTL 反而比旧逻辑更抓得住 (旧逻辑要求恰好在那
            #   1~2 帧里读到完整长行, 错过就得等 1.2s 补发)。
            now = time.time()
            pend = self._qte_row_pend      # TTL 作废已在函数开头统一做过 (v1.6.4)
            if pend is not None:
                hit = -1
                for off in range(len(P), len(pend) + 1):
                    if dirs == pend[off:]:
                        hit = off
                        break
                if hit >= 0:
                    self._qte_row, self._qte_row_pend = list(pend), None
                    # 打失配 _qte_last/acted: 下一轮走"画面变了"分支, 按新记账行推进
                    self._qte_last, self._qte_acted = [], False
                    self._qte_trace(kind, dirs, "grow-commit off=%d" % hit)
                    return
            if len(dirs) > len(R) and dirs[:len(R)] == R:
                rmaxg = int(self.cfg.get("qte_row_max", 6))
                if rmaxg > 0 and len(dirs) > rmaxg:
                    self._qte_conf_ts = now
                    self._qte_trace(kind, dirs, "grow-cap>%d" % rmaxg)
                    return
                # v1.6.4 记账行**已按完**却又长出新的箭 -> 这就是弹出动画 (真增长)。
                # 此时手里没有"待按的键"可以拿消耗来佐证, 继续等候选只会把这一行拖到
                # 1.2s 补发甚至超时 -> 立即采纳并按新出现那支。只在刚按完的
                # qte_grow_done_ms(默认 800) 内认作同一行的尾巴, 超过就落回 newrow
                # 通道, 免得把下一行误接到旧行屁股上。
                _gdone = float(self.cfg.get("qte_grow_done_ms", 800)) / 1000.0
                if (self.cfg.get("qte_grow_adopt_done", True) and len(P) >= len(R)
                        and self._qte_act_ts and now - self._qte_act_ts <= _gdone):
                    self._qte_row, self._qte_row_pend = list(dirs), None
                    self._qte_trim_cand, self._qte_trim_ts = None, 0.0
                    self._qte_trace(kind, dirs, "grow-done")
                    self._qte_press_gated(dirs[len(P)], len(P) + 1, len(dirs), kind,
                                          dirs, via="grow-done")
                    return
                if not self.cfg.get("qte_grow_confirm", True):
                    self._qte_row = dirs        # A/B 用: v1.6.2 旧行为, 单帧即采纳
                    self._qte_trace(kind, dirs, "grow-immediate")
                    return
                if pend != dirs:
                    self._qte_row_pend, self._qte_row_pend_ts = list(dirs), now
                    self._qte_trace(kind, dirs, "grow-arm")
                else:
                    self._qte_trace(kind, dirs, "grow-wait")
                return
            # v1.6.1: 只认 off=len(P) (行首已被吃掉)。旧代码兼容 off-1
            # (最左是刚按那支的淡出残影) 会在"丢键/游戏未消耗"时把残影当
            # 已消耗 -> 提前按下一支 = 发错键整行重来; 残影期改由下面的
            # not-consumed 护栏沉默 ~200ms, 等残影淡出再按, 只慢不乱。
            for off in (len(P),):                   # 正常推进 (行首被吃掉)
                if 0 <= off <= len(R) and dirs == R[off:]:
                    i = len(P) - off
                    if 0 <= i < len(dirs):
                        self._qte_press_gated(dirs[i], len(P) + 1, len(R), kind, dirs,
                                              via="advance", stable_ok=fast)
                    return
            for off in range(len(R), len(P), -1):   # 漏帧: 画面跳前进 -> 以画面对齐
                if dirs == R[off:]:
                    self._qte_pressed = R[:off]
                    self._qte_press_gated(dirs[0], off + 1, len(R), kind, dirs,
                                          via="skipalign")
                    return
            # v1.6.1 读花护栏 (复位之前): 期望下一支是 R[len(P)], 画面首向与它
            # 一致且长度吻合 -> 只是尾段后面几支读花, 沉默等稳定读; 复位重按
            # 首键会向游戏多发一支错键 = 整行重来。
            nxt = R[len(P):]
            if nxt and dirs[0] == nxt[0] and len(dirs) == len(nxt):
                self._qte_conf_ts = now               # v1.6.4: 尾部读花 -> 脏窗
                self._qte_trace(kind, dirs, "flicker-tail")
                return
            # 刚按的那支还在画面最左 (游戏未消耗/丢键) -> 沉默, 交给补发与
            # 看门狗; 旧逻辑在这里复位 -> 下一轮又按同一支 = 重复错键。
            if dirs[0] == P[-1] and len(dirs) == len(R) - len(P) + 1:
                self._qte_trace(kind, dirs, "not-consumed")
                return
            # v1.6.2 切片护栏 (取代旧 overpress-wait / fade 两分支, 覆盖面更全):
            # 画面尾段只要是记账行 R 的**连续切片** (dirs == R[off:off+len(dirs)]),
            # 就仍属同一行 —— 尾部读少几支 / 刚按那支还没被游戏吃掉 / 淡出残影,
            # 一律不按或照常推进, **绝不复位**。旧代码这些形状会掉到 newrow-reset:
            # 复位后下一帧又读回完整行 -> 当成新行再按一次首键, 游戏判错整行重来
            # (artillery.log 里 "右 (1/3)" 连按三次的元凶)。
            #   off == len(P): 最左那支正是下一支该按的 -> 照常按 (稳定窗把关);
            #   off <  len(P): 游戏还没吃掉已按的键 -> 沉默, 交给补发/看门狗。
            # (qte_slice_guard=false 可整块关掉回到 v1.6 行为, 仅供 A/B 与排障。)
            if self.cfg.get("qte_slice_guard", True):
                soff = -1
                for off in range(min(len(R), len(P)), -1, -1):
                    if dirs == R[off:off + len(dirs)]:
                        soff = off
                        break
                # v1.6.4 放行"最后一支": soff == len(P) 已经意味着画面最左那支**正是**
                # 下一支该按的键 (dirs[0] == R[len(P)]), 尾部读少几支都不影响这一支的
                # 正确性 —— "真尾段"与"读花"两种解释下按的都是同一个键。旧条件额外要求
                # len(dirs)>1 或记账只剩 1 支, 于是"记账行被幻影撑长 + 画面只剩最后一支"
                # 时永远沉默, 干等 1.2s 补发 (蒙特卡洛 900 行里 97 行超时的主因); 更糟的
                # 是沉默期里任何一帧读花只要撞上幻影那支, 就会被 advance 当成正常推进
                # 按出去 = 错键 + 整行重来。
                if soff == len(P) and (self.cfg.get("qte_slice_press_last", True)
                                       or len(dirs) > 1 or len(R) - len(P) == 1):
                    self._qte_trim_tail(R, soff, len(dirs), kind, dirs, now)
                    self._qte_press_gated(R[len(P)], len(P) + 1, len(R), kind, dirs,
                                          via="slice", stable_ok=fast)
                    return
                if soff >= 0:
                    self._qte_conf_ts = now
                    self._qte_trace(kind, dirs, "slice-noise off=%d" % soff)
                    return
            if len(dirs) == 1:
                if self._qte_ghost_lone(dirs[0], kind, dirs, now):
                    return
                self._qte_conf_ts = now               # v1.6.4: 孤箭多半是读花残片
                self._qte_trace(kind, dirs, "lone-ignore")
                return          # 孤箭: 不是期望尾段也不是残行 -> 忽略, 不清状态
            # v1.6.2 复位闸门: 行还没按完 (len(P) < len(R)) 时, 与记账行不吻合的
            # 读法必须**连续稳定** qte_row_switch_ms(默认 400) 才认作无关新行并复位。
            # 瞬态读花 (画面撕裂/爆炸亮块/动画半帧, 1~3 帧 = 8~50ms) 不再触发复位。
            # 已按完的行 (len(P) >= len(R)) 立刻复位 -> 连续两行之间零额外延迟。
            if len(P) < len(R):
                ak, tnow = tuple(dirs), time.time()
                sw = float(self.cfg.get("qte_row_switch_ms", 400)) / 1000.0
                if self._qte_alt != ak:
                    self._qte_alt, self._qte_alt_ts = ak, tnow
                    self._qte_conf_ts = tnow          # v1.6.4: 读法与记账行打架
                    self._qte_trace(kind, dirs, "alt-first")
                    return
                if sw > 0 and tnow - self._qte_alt_ts < sw:
                    self._qte_conf_ts = tnow
                    self._qte_trace(kind, dirs, "alt-wait")
                    return
            if len(P) < len(R):
                # v1.6.4: 半途复位 = 可能还欠游戏几支键。把这一行记成"残行",
                # 若接下来画面只剩一支孤箭而它正是欠的那一支, 还能救回来
                # (否则 "单支箭不开新行" 会把它永远忽略掉 = 这一行挂死)。
                self._qte_ghost, self._qte_ghost_ts = list(R), time.time()
                self._qte_ghost_off = len(P)
            self._qte_alt, self._qte_alt_ts = None, 0.0
            self._qte_row_pend, self._qte_row_pend_ts = None, 0.0
            self._qte_trace(kind, dirs, "newrow-reset")
            self._qte_pressed, self._qte_row, self._qte_hist = [], [], []
            self._qte_stalls = 0                      # 无关新行: 卡死额度按行计, 复位
            self._qte_stucks = 0
            self._qte_cand, self._qte_cand_ts = None, 0.0
            self._qte_trim_cand, self._qte_trim_ts = None, 0.0
            self._qte_conf_ts = time.time()           # v1.6.4: 复位后首键走脏窗
            P = []                                  # 无关的新行
        if len(dirs) < 2:
            if dirs and self._qte_ghost_lone(dirs[0], kind, dirs, time.time()):
                return
            self._qte_trace(kind, dirs, "short-ignore")
            return              # 单支箭不开新行 (防游戏 UI 孤立亮块误触发)
        # v1.6.3 行长上限: 实测真行 2~4 支 (artillery.log 4700 行里 4 支占 96%),
        # 读到 7/8 支必是两行粘连或大面积读花 -> 直接不开行 (按错=整行重来, 代价
        # 远大于放行一条怪行; 真有更长行的模式把 qte_row_max 调大即可)。
        _rcap = int(self.cfg.get("qte_row_max", 6))
        if _rcap > 0 and len(dirs) > _rcap:
            self._qte_conf_ts = time.time()   # v1.6.4: 怪行 = 读花, 后续按键走脏窗
            self._qte_trace(kind, dirs, "row-cap>%d" % _rcap)
            return
        key = tuple(dirs)
        now = time.time()
        # v1.6.12 行首开行防抖: 行是从左往右逐支弹出的, 头几帧尾段一直在长; 而首键
        # 只依赖**行首**那一支。旧防抖按"整行序列不变"计时, 每弹一支就重新计时,
        # 首键要等最后一支弹完 + 110ms + 60ms。现按"行首方向不变"计时 (每帧仍要求
        # >=2 支, 单支走 short-ignore 不进这里), 尾支弹出不再重启倒计时; 会改行首的
        # 读花照旧重启。实测 p90 首键 248ms 里有一大块就是弹出动画重启掉的。
        _hd = dirs[0]
        if (_hd != getattr(self, "_qte_open_head", None)
                or now - getattr(self, "_qte_open_head_ts", 0.0) > 1.0):
            self._qte_open_head, self._qte_open_head_ts = _hd, now
        if not self._qte_hist or self._qte_hist[-1] != key:
            self._qte_hist, self._qte_new_ts = [key], now
            self._qte_trace(kind, dirs, "newrow-firstframe")
            return                                  # 新行首帧
        if len(self._qte_hist) < 2:
            self._qte_hist.append(key)
        # v1.6 行稳定防抖: 同一序列连续稳定读到 qte_row_stable_ms 才允许首键。
        # 瞬态误读 (生成动画/画面撕裂/场景亮块凑行) 活不过 70ms, 真行稳定 1~2s;
        # 行还在往右弹出时序列一直在变, 防抖自然重新计时。
        # v1.6.3: 70 -> 110ms。逐键归因里另一类错键是**首键**(press:newrow)被
        # "稳定但读错"的持续读花穿透 (弹出动画半帧/爆炸亮块能稳定存在 120~300ms,
        # 旧 70+60=130ms 确认窗盖不住)。首键每行只多付 40ms, 之后每键节奏不变
        # (仍由 qte_min_gap_ms=70 支配), 换来的是最贵的那一支键更稳。
        _rs = float(self.cfg.get("qte_row_stable_ms", 110)) / 1000.0
        _rsd = float(self.cfg.get("qte_row_stable_dirty_ms", 0)) / 1000.0
        if (_rsd > _rs and now - self._qte_conf_ts
                < float(self.cfg.get("qte_conflict_ms", 250)) / 1000.0):
            _rs = _rsd                        # v1.6.4 脏窗: 刚打过架的行多等一会儿
        # v1.6.12 单一开行门: 行首年龄 >= 行稳(_rs) + 候选稳(_st) 就直接开行并按首键
        # (stable_ok=True, 不再串行再等一个候选窗)。总观察时长与 v1.6.3 以来逐位相同
        # (110+60=170ms, 脏窗时更长), 但去掉了两段串行之间的轮询空等, 且行首时钟
        # 不被尾支弹出重启 -> 弹出动画那段时间不再计入首键延迟。
        _st = float(self.cfg.get("qte_press_stable_ms", 60)) / 1000.0
        _st_d = float(self.cfg.get("qte_press_stable_dirty_ms", 0)) / 1000.0
        if (_st_d > _st and now - self._qte_conf_ts
                < float(self.cfg.get("qte_conflict_ms", 250)) / 1000.0):
            _st = _st_d                        # 脏窗: 刚打过架的画面首键多确认一会儿
        _gate = _rs + _st
        _clk = (self._qte_open_head_ts if self.cfg.get("qte_head_open", True)
                else self._qte_new_ts)
        if len(self._qte_hist) < 2 or now - _clk < _gate:
            self._qte_trace(kind, dirs, "newrow-debounce")
            return
        self._qte_row = dirs
        self._qte_open_head, self._qte_open_head_ts = None, 0.0   # 时钟已消费, 下一条行重新计
        self._qte_press_gated(dirs[0], 1, len(dirs), kind, dirs,
                              via="newrow", stable_ok=True)

    def _qte_ghost_lone(self, d, kind, dirs, now):
        """v1.6.4 残行救回: 画面只剩一支孤箭时, 若它正好是"我们还欠游戏的那一支"

        就按掉它。只认 off == len(ghost)-1 (欠的正好是最后一支): 画面上只剩一支
        说明游戏已经吃到最后一支了, 我们欠的就是它; 若还欠好几支, 画面上这一支
        并不是该按的那支, 按了必错。ghost 只在 qte_ghost_ms 内有效, 用过即清。"""
        g, gt, off = self._qte_ghost, self._qte_ghost_ts, self._qte_ghost_off
        if not g:
            return False
        ttl = float(self.cfg.get("qte_ghost_ms", 1500)) / 1000.0
        if ttl > 0 and now - gt > ttl:
            self._qte_ghost, self._qte_ghost_ts, self._qte_ghost_off = None, 0.0, 0
            return False
        if off != len(g) - 1 or d != g[off]:
            return False
        self._qte_ghost, self._qte_ghost_ts, self._qte_ghost_off = None, 0.0, 0
        self._qte_row, self._qte_pressed = list(g), list(g[:off])
        self._qte_last, self._qte_acted = [], False
        self._qte_trace(kind, dirs, "ghost-rescue")
        self._qte_press_gated(d, off + 1, len(g), kind, dirs, via="ghost")
        return True

    def _qte_trim_tail(self, R, soff, ndirs, kind, dirs, now):
        """v1.6.4 记账行尾部自愈: 画面连续 qte_trim_ms 只稳定显示 R 的前半截尾段

        (soff + ndirs < len(R)), 说明 R 尾巴上那几支是幻影 (被 grow 采纳进来的读花),
        按画面把 R 修剪掉。不修剪的话这一行永远"差几支按不完", 只能干等 1.2s 补发/
        2.5s 看门狗。qte_trim_ms=0 关闭。"""
        tms = float(self.cfg.get("qte_trim_ms", 0)) / 1000.0
        end = soff + ndirs
        if tms <= 0 or soff != len(self._qte_pressed) or end >= len(R):
            self._qte_trim_cand, self._qte_trim_ts = None, 0.0
            return
        if self._qte_trim_cand != end:
            self._qte_trim_cand, self._qte_trim_ts = end, now
            return
        if now - self._qte_trim_ts < tms:
            return
        self._qte_row = R[:end]
        self._qte_row_pend, self._qte_row_pend_ts = None, 0.0
        self._qte_trim_cand, self._qte_trim_ts = None, 0.0
        self._qte_trace(kind, dirs, "row-trim>%d" % end)

    def _qte_press_gated(self, d, idx, total, kind="", dirs=(), via="", stable_ok=False):
        """按键前三道门 (v1.6.1): 回前台 settle / 置信度 margin / 候选稳定窗。

        - settle: 刚从 QQ/浏览器切回游戏的前 qte_fg_settle_ms 不按 —— 切窗瞬间
          游戏可能在播恢复动画, 箭行读不稳, 且发送线程的键也可能落在切换缝里;
        - margin: 本帧选中行最小 margin 低于 qte_press_margin 宁可等一帧
          (按错 = 整行重来, 代价远大于等 8ms);
        - 候选稳定窗: 同一个 (方向, 下标, 总数) 连续读到 qte_press_stable_ms
          才真按 —— 单帧读花 (动画半帧/撕裂) 活不过 60ms, 真行稳定 1~2s。
          消耗推进后候选自然换值重新计时, 每键只多付一个稳定窗。"""
        now = time.time()
        if now < getattr(self, "_qte_red_until", 0.0):
            self._qte_trace(kind, dirs, "gate-redhold")
            return False
        if now - self._qte_fg_back < float(self.cfg.get("qte_fg_settle_ms", 350)) / 1000.0:
            self._qte_trace(kind, dirs, "gate-fgsettle")
            return False
        if self._qte_margin < float(self.cfg.get("qte_press_margin", 0.10)):
            self._qte_trace(kind, dirs, "gate-margin")
            return False
        stable = float(self.cfg.get("qte_press_stable_ms", 60)) / 1000.0
        # v1.6.4 自适应确认窗: 画面刚出现过"解释不了的读法"(读花/切片噪声/复位)时
        # 改用更长的 qte_press_stable_dirty_ms —— 持续读花 (爆炸闪光/动画半帧) 能稳定
        # 存在 120~300ms, 固定短窗盖不住; 画面干净时仍走短窗, 常态零额外延迟。
        # 0 = 关闭 (退回 v1.6.3 固定窗)。
        _dirty = float(self.cfg.get("qte_press_stable_dirty_ms", 0)) / 1000.0
        if _dirty > stable and (now - self._qte_conf_ts
                                < float(self.cfg.get("qte_conflict_ms", 250)) / 1000.0):
            stable = _dirty
        if stable > 0 and not stable_ok:
            cand = (d, idx, total)
            if self._qte_cand != cand:
                self._qte_cand, self._qte_cand_ts = cand, now
                self._qte_trace(kind, dirs, "gate-cand-new")
                return False
            if now - self._qte_cand_ts < stable:
                self._qte_trace(kind, dirs, "gate-cand-wait")
                return False
            self._qte_cand, self._qte_cand_ts = None, 0.0
        if stable_ok:
            self._qte_trace(kind, dirs, "gate-fastpath")
        self._qte_press(d, idx, total)
        self._qte_trace(kind, dirs, "press:%s" % (via or "?"))
        return True

    def _qte_press(self, d, idx, total):
        self._qte_pressed = self._qte_pressed + [d]
        self._qte_red_logged = False        # v1.6.6:  newRow 成功发键 -> 允许再提示下一条死行
        self._qte_hist = []
        self._qte_acted = True                # 本帧已按: 画面不变就不再连发
        self._qte_retries = 0
        self._qte_alt, self._qte_alt_ts = None, 0.0   # v1.6.2 推进后重新观察
        self._qte_fast = time.time() + 0.45   # 按完短暂快速重轮询, 尽早看到消耗
        # v1.6 最小键间隔: 闭环靠画面推进, 低延迟屏上消耗反馈可能来得很早,
        # 连发太密会让游戏输入状态机串键 -> 整行重来。pre 在发送线程里补睡,
        # 不占轮询线程。
        now = time.time()
        pre = max(0.0, float(self.cfg.get("qte_min_gap_ms", 70)) / 1000.0
                  - (now - self._qte_last_key_ts))
        self._qte_last_key_ts = now + pre
        threading.Thread(target=self._qte_send_one,
                         args=(d, idx, total), kwargs={"pre": pre}, daemon=True).start()

    def _qte_resend(self, d, idx, total):
        """补发当前该按的那支, **不推进记账**。

        按键可能丢失 (发送瞬间切窗/被别的程序吞掉), 此时画面永远不变, 只靠
        "沉默等消耗" 会把整行白等到超时。补发一次即可让游戏继续; 因为不推进
        _qte_pressed, 画面随后追上时状态机仍按正常路径对齐, 不会多发也不会漏发。
        v1.6.1: 补发也遵守最小键间隔, 避免与上一键挤在同一游戏输入帧里。"""
        self._qte_acted = True
        self._qte_fast = time.time() + 0.45
        now = time.time()
        pre = max(0.0, float(self.cfg.get("qte_min_gap_ms", 70)) / 1000.0
                  - (now - self._qte_last_key_ts))
        self._qte_last_key_ts = now + pre
        threading.Thread(target=self._qte_send_one,
                         args=(d, idx, total, True), kwargs={"pre": pre}, daemon=True).start()
        # 次数上限 qte_retry_max (默认 3): 真实丢键补发 1 次就够; 上限是防止
        # 画面长期不变时 (QTE 失败后残影 / 暂停帧 / 关闸联调静态样张) 无限补发狂敲方向键。

    def _qte_send_one(self, d, idx, total, retry=False, pre=0.0):
        """闭环单键: 先补齐最小键间隔 pre, 再按住 qte_hold_ms 松开。
        _qte_act_ts 在实际 keydown 前打点, 补发计时从"键真的发出去"起算。
        v1.6.1: keydown 前**再查一次前台闸** —— pre 睡眠 (至多 100ms) 里用户
        切去 QQ/浏览器时, 旧代码会把这支键发进前台应用 (游戏收不到 = 丢键,
        02:12 日志里"连按三次同一支"的其中一条来源)。丢了的键由补发兜底。"""
        if pre > 0:
            time.sleep(pre)
        if time.time() < getattr(self, "_qte_red_until", 0.0):
            # v1.6.6: pre 睡眠 (至多 100ms) 里认出红行 -> 这支在途键已是死行的键, 丢弃。
            self._qte_trace("send", [d], "drop-redhold")
            return
        try:
            import keyboard
        except Exception as ex:
            self.q.put(("log", "QTE 发送失败: keyboard 不可用 (%s)" % ex))
            return
        hold = float(self.cfg.get("qte_hold_ms", 40)) / 1000.0
        self._qte_act_ts = time.time()
        if self.cfg.get("qte_gate", True) and not self._game_foreground():
            self.q.put(("log", "QTE 丢弃 -> %s (%d/%d): 发送瞬间前台不是游戏"
                        % (self.QTE_CN.get(d, d), idx, total)))
            self._qte_trace("send", [d], "drop-fg")
            return
        self.q.put(("log", "QTE%s -> %s (%d/%d)" % (
            " 补发" if retry else "", self.QTE_CN.get(d, d), idx, total)))
        keyboard.press(d)
        time.sleep(hold)
        keyboard.release(d)


    def _qte_send(self, seq):
        """按从左到右顺序发送方向键; 间隔/按住时长走 config (qte_gap_ms/qte_hold_ms)。"""
        try:
            import keyboard
        except Exception as ex:
            self.q.put(("log", "QTE 发送失败: keyboard 不可用 (%s)" % ex))
            return
        gap = float(self.cfg.get("qte_gap_ms", 90)) / 1000.0
        hold = float(self.cfg.get("qte_hold_ms", 40)) / 1000.0
        self.q.put(("log", "QTE -> %s" % " ".join(self.QTE_CN.get(d, d) for _x, d in seq)))
        for i, (_x, d) in enumerate(seq):
            if i:
                time.sleep(gap)
            keyboard.press(d)
            time.sleep(hold)
            keyboard.release(d)

    def toggle_qte(self):
        """F9: QTE 自动输入开关 (后台识别方向箭行并自动按方向键)。"""
        self.cfg["qte_on"] = not self.cfg.get("qte_on", False)
        self.save_cfg()
        self._sync_toggles()
        self.log("QTE 自动输入: %s (开=后台识别屏幕中央方向箭行, 从左到右自动按键)"
                 % ("开" if self.cfg.get("qte_on") else "关"))

    def qte_once(self):
        """F10: 立即截屏读一次方向箭行并发送 (不受 F9 开关影响, 不走 guard)。"""
        def work():
            try:
                seq = core.find_qte_arrows()
            except Exception as ex:
                self.q.put(("log", "QTE 读一次失败: %s: %s" % (type(ex).__name__, ex)))
                return
            if not seq:
                self.q.put(("log", "QTE 读一次: 屏幕中央没找到方向箭行"))
                return
            self._qte_send(seq)
        threading.Thread(target=work, daemon=True).start()

    def toggle_live(self):
        """F8: 地图悬停实时跟踪目标开关 (后台轮询读到即持续更新目标坐标)。"""
        self.cfg["live_target"] = not self.cfg.get("live_target", True)
        self.save_cfg()
        self._sync_toggles()
        self.log("实时跟踪目标: %s (开=地图里鼠标悬停即持续更新目标坐标/HUD)"
                 % ("开" if self.cfg.get("live_target", True) else "关"))

    def _live_target(self, t):
        """实时跟踪回调 (主线程): 更新目标并刷新读数/HUD。"""
        if not self.cfg.get("live_target", True):
            return
        self.target = t
        self.refresh()

    def _hud_update(self, big, sub, ok=True, state="ok", xy1=None, xy2=None):
        """ok=False 时整条读数转红并追加 ⚠超远/⚠过近 标记 (超出武器射程窗口)。
        xy1/xy2 = HUD 上的 目标/炮位 坐标行。"""
        if self.hud:
            bad = (ok is False)
            tag = {"far": "  ⚠超远", "near": "  ⚠过近"}.get(state, "") if bad else ""
            self.hud_big.configure(text=big, fg=RED if bad else GOLD)
            self.hud_sub.configure(text=sub + tag, fg=RED if bad else FG)
            if getattr(self, "hud_xy1", None) is not None:
                self.hud_xy1.configure(text=xy1 if xy1 else "目标 --",
                                       fg=FG if xy1 else DIM)
                self.hud_xy2.configure(text=xy2 if xy2 else "炮位 --",
                                       fg=FG if xy2 else DIM)

    @staticmethod
    def _clamp_hud_pos(pos, vx, vy, vw, vh, w=320, h=180):
        """把保存的 HUD 位置钳回当前虚拟屏。换显示器/改分辨率/副屏断开后,
        config 里的 hud_pos 可能整块落在屏幕外 -> HUD 状态是"开"却看不见也拖不回来。
        至少保留 80px 在屏内, 便于用 F7 拖回。"""
        try:
            x, y = int(pos[0]), int(pos[1])
        except Exception:
            x, y = vx + max(0, vw - 340), vy + 90
        x = min(max(x, vx - w + 80), vx + max(0, vw - 80))
        y = min(max(y, vy), vy + max(0, vh - 40))
        return [x, y]

    def toggle_hud(self):
        """置顶半透明 HUD: overrideredirect + topmost, 无边框/窗口化游戏之上可见。"""
        if self.hud:
            self.cfg["hud_pos"] = [self.hud.winfo_x(), self.hud.winfo_y()]
            self.hud.destroy()
            self.hud = None
            self.hud_tip = None
            self.hud_xy1 = self.hud_xy2 = None
            self._hud_hw = 0
        else:
            h = tk.Toplevel(self.root)
            h.overrideredirect(True)
            h.attributes("-topmost", True)
            h.attributes("-toolwindow", True)   # 不进任务栏 / Alt-Tab
            h.attributes("-alpha", 0.0)   # 量尺寸期间先隐形, 避免在左上角闪一下
            h.configure(bg="#0b0d10")
            h.geometry("10x10+0+0")
            big = tk.Label(h, text="-- m", bg="#0b0d10", fg=GOLD,
                           font=("Consolas", 26, "bold"), anchor="w", padx=10)
            big.pack(fill="x", pady=(6, 0))
            sub = tk.Label(h, text="方位 --  仰角 --", bg="#0b0d10", fg=FG,
                           font=("Microsoft YaHei", 10), anchor="w", padx=10)
            sub.pack(fill="x")
            xy1 = tk.Label(h, text="目标 --", bg="#0b0d10", fg=DIM,
                           font=("Consolas", 10), anchor="w", padx=10)
            xy1.pack(fill="x")
            xy2 = tk.Label(h, text="炮位 --", bg="#0b0d10", fg=DIM,
                           font=("Consolas", 10), anchor="w", padx=10)
            xy2.pack(fill="x")
            tip = tk.Label(h, text="WARDOGS HUD · 按住F7拖动 · 双击关闭", bg="#0b0d10", fg=DIM,
                           font=("Microsoft YaHei", 8), anchor="w", padx=10)
            tip.pack(fill="x", pady=(0, 4))
            # 尺寸自适应: 按"最坏情况文案"量一次内容宽高再定窗口大小。
            # 固定 300x150 在 125% 缩放下内容实测要 317x173: 底行提示被切掉一半、
            # 横排也出窗; 字体像素尺寸随系统缩放/分辨率变化, 任何固定值都会再切。
            worst = {big: "88888 m",
                     sub: "方位 888.8°  仰角 8888 mil  ⚠超远",
                     xy1: "目标 x888.88 y888.88",
                     xy2: "炮位 x888.88 y888.88",
                     tip: "SPH-2 射程 88888~88888m · 按住F7拖动 · 双击关闭"}
            keep = {w: w["text"] for w in worst}
            for w, t in worst.items():
                w.configure(text=t)
            h.update_idletasks()
            hw_ = max(h.winfo_reqwidth(), 240) + 2
            hh_ = h.winfo_reqheight() + 2
            for w, t in keep.items():
                w.configure(text=t)
            self._hud_size = (hw_, hh_)
            vx, vy, vw, vh = virtual_screen()
            pos = self._clamp_hud_pos(self.cfg.get("hud_pos"), vx, vy, vw, vh,
                                      w=hw_, h=hh_)
            h.geometry("%dx%d+%d+%d" % (hw_, hh_, int(pos[0]), int(pos[1])))
            h.attributes("-alpha", 0.72)
            h.bind("<Double-Button-1>", lambda e: self.toggle_hud())
            self.hud, self.hud_big, self.hud_sub = h, big, sub
            self.hud_xy1, self.hud_xy2 = xy1, xy2
            self.hud_tip = tip
            self._weapon_range_sync()   # HUD 提示行显示当前武器射程窗口
            self.root.update_idletasks()
            self._hud_hw = 0
            self._hud_hwnd()
            self._hud_apply_style()
            self._hud_holding = False
            self._drag_active = False
            self._hud_watch()
            self._hud_tick()
            self.refresh()
        self.cfg["hud_on"] = bool(self.hud)
        self.save_cfg()
        self._sync_toggles()
        self.log("HUD: %s" % ("开 (置顶半透明, 无边框游戏可见)" if self.hud else "关"))

    def _hud_hwnd(self):
        """Tk Toplevel 的真实顶层 HWND (winfo_id 的父窗); 建窗时缓存, 供钩子线程安全使用。"""
        hw = getattr(self, "_hud_hw", 0)
        if hw:
            return hw
        try:
            u = ctypes.windll.user32
            hw = u.GetParent(self.hud.winfo_id())
            hw = hw or self.hud.winfo_id()
            self._hud_hw = hw
            return hw
        except Exception:
            return 0

    def _hud_apply_style(self):
        """鼠标穿透: 加 WS_EX_TRANSPARENT(0x20); 需 WS_EX_LAYERED, -alpha 已启用。"""
        if not self.hud:
            return
        u = ctypes.windll.user32
        hw = self._hud_hwnd()
        if not hw:
            return
        ex = u.GetWindowLongW(hw, -20)
        if getattr(self, "_hud_passthrough", True):
            # v1.6.7: WS_EX_NOACTIVATE(0x08000000) —— HUD 是置顶窗, 绝不能
            # 抢走游戏的前台/激活; 抢了前台游戏就收不到方向键, 表现为"按了没反应"。
            ex |= 0x20 | 0x80000 | 0x08000000
        else:
            ex &= ~0x20
        u.SetWindowLongW(hw, -20, ex)
        u.SetWindowPos(hw, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x10 | 0x20)  # 刷新框架

    def _set_passthrough(self, on):
        self._hud_passthrough = bool(on)
        self._hud_apply_style()

    def _hud_hold(self, down):
        """按住 F7 (钩子线程): 只置标志 + 改 Win32 穿透样式 (线程安全);
        所有 Tk 操作交给主线程的 _hud_watch 循环, 避免跨线程调 Tk。"""
        if not self.hud:
            return
        self._hud_holding = bool(down)
        if down:
            self._set_passthrough(False)
        else:
            self._set_passthrough(bool(self.cfg.get("hud_passthrough", True)))

    def _hud_watch(self):
        """主线程 40ms 循环: 按住 F7 时让 HUD 跟随光标 (不依赖窗口命中测试);
        松开恢复穿透/透明度并保存位置。"""
        if not self.hud:
            return
        holding = bool(getattr(self, "_hud_holding", False))
        active = bool(getattr(self, "_drag_active", False))
        if holding and not active:
            self._drag_active = True
            try:
                self.hud.attributes("-alpha", 0.95)
            except Exception:
                pass
            pt = wt.POINT()
            u = ctypes.windll.user32
            if not u.GetPhysicalCursorPos(ctypes.byref(pt)):
                u.GetCursorPos(ctypes.byref(pt))
            self._drag_off = (pt.x - self.hud.winfo_x(), pt.y - self.hud.winfo_y())
        elif holding and active:
            pt = wt.POINT()
            u = ctypes.windll.user32
            if not u.GetPhysicalCursorPos(ctypes.byref(pt)):
                u.GetCursorPos(ctypes.byref(pt))
            ox, oy = getattr(self, "_drag_off", (0, 0))
            self.hud.geometry("+%d+%d" % (pt.x - ox, pt.y - oy))
        elif not holding and active:
            self._drag_active = False
            try:
                self.hud.attributes("-alpha", 0.72)
            except Exception:
                pass
            self.cfg["hud_pos"] = [self.hud.winfo_x(), self.hud.winfo_y()]
            core.save_config(self.cfg)
        self.root.after(40, self._hud_watch)

    def _hud_tick(self):
        """每 0.7s 强制 HWND_TOPMOST: 无边框游戏自身也 topmost, 不刷新会被压到游戏下面。"""
        if not self.hud:
            return
        try:
            hw = self._hud_hwnd()
            if hw:
                ctypes.windll.user32.SetWindowPos(hw, -1, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
        except Exception:
            pass
        self.root.after(700, self._hud_tick)

    # ------------------------------------------------------------ 消息队列
    def drain(self):
        """主线程消费后台消息 (QTE 等后台线程的日志)。"""
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self.log(item[1].rstrip("\n"))   # 走 log(): 后台线程的消息也能落盘回看
        except queue.Empty:
            pass
        self.root.after(300, self.drain)

    # ------------------------------------------------------------ 其它
    def save_cfg(self):
        self.cfg["weapon"] = self.weapon_var.get()
        core.save_config(self.cfg)
        self.cfg = core.load_config()
        self.hint.configure(text=self._hint_text())
        self.log("设置已保存 -> %s" % core.CONFIG_PATH)

    def on_close(self):
        """右上角 X / 关窗: **一次性结束本进程与 bootloader 父进程**。

        PyInstaller onefile 的形态是"父 bootloader + 子 app"两个进程: 子进程一退,
        父 bootloader 等待句柄返回后就会自己结束。所以关键是**子进程必须一定退得掉**,
        而旧写法只做 `keyboard.unhook_all()` + `root.destroy()` 之后等解释器自然收尾,
        有两类残留会拖住它:
          * keyboard 库的低层钩子线程在 unhook_all 之后可能还在;
          * 其它后台线程/子进程收尾期间又抛异常, 异常被吞掉后进程就挂在那儿。
        结果是"窗口关了, 进程还占着单实例互斥锁", 新版根本起不来。
        所以最后用 `os._exit(0)` 兜底: 不跑 atexit、不 join 线程、不等 GC,
        本进程立刻结束; 再显式收一次 bootloader 父进程 (带严格安全闸, 见下)。"""
        self._poll_on = False
        try:
            self._qte_red_until = 1e18          # 禁掉任何在途/排队的发键
        except Exception:
            pass
        for fn in (lambda: core.shutdown_ocr(),):
            try:
                fn()
            except Exception:
                pass
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        _terminate_parent_bootloader()
        os._exit(0)                             # 兜底: 保证父子进程都结束


    def run(self):
        self.root.mainloop()

def _terminate_parent_bootloader():
    """只在"父进程确实是本 exe 的 PyInstaller bootloader"时结束它。

    PyInstaller onefile 的父进程只做一件事: 建子进程 -> 等句柄 -> 拿退出码 -> 自己退出。
    子进程正常退出时父进程**本来就会跟着消失**, 这里显式收一次是为了杜绝任何残留
    (用户报过"点 X 关了窗口, 进程还挂在任务管理器", 于是单实例锁一直占着、新版起不来)。

    **安全闸 (必须过)**: 父进程的映像路径必须 == `sys.executable` 才动手。
    从 PowerShell / 终端双击前的 shell 里启动时, 父进程是 powershell.exe / explorer.exe,
    与本 exe 路径不相等 -> 直接跳过, **绝不会误杀 shell 或资源管理器**。
    非冻结 (源码 python gui_app.py) 时一律跳过。"""
    try:
        if not getattr(sys, "frozen", False):
            return
        ppid = os.getppid()
        if not ppid or ppid == os.getpid():
            return
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, ppid)      # QUERY_LIMITED_INFORMATION
        if not h:
            return
        try:
            buf = ctypes.create_unicode_buffer(520)
            n = wt.DWORD(520)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return
            parent_exe = buf.value
        finally:
            k32.CloseHandle(h)
        if os.path.normcase(parent_exe) != os.path.normcase(sys.executable):
            return                                  # 不是本 exe 的 bootloader -> 不碰
        h2 = k32.OpenProcess(0x0001, False, ppid)   # PROCESS_TERMINATE
        if h2:
            try:
                k32.TerminateProcess(h2, 0)
            finally:
                k32.CloseHandle(h2)
    except Exception:
        pass                                        # 收父进程失败也不能挡退出


# ---------------------------------------------------------- v1.6 单实例互斥锁
_SINGLE_MUTEX = None
_K32_LE = ctypes.WinDLL("kernel32", use_last_error=True)
MUTEX_NAME = "Local\\WARDOGS_Artillery_SingleInstance"


def acquire_single_instance(name=MUTEX_NAME):
    """拿到单实例锁返回 True; 已有实例在跑返回 False。

    为什么必须有这道锁 (v1.6 头号教训): 两个实例会**同时往游戏里发方向键**, 游戏侧收到
    交叉的按键流 -> QTE 整行判错重来, 用户看到的就是「错误率突然很高」, 而识别精度其实
    是好的 (本机实测: Downloads 里一份旧副本从 17:51 一直开到次日 00:2x, 与新实例并行
    发键, 日志时间戳可交叉印证)。旧副本没有可见窗口也照样在后台发键, 开过就忘 ->
    光靠文档提醒拦不住, 直接在启动阶段拦第二个实例。

    建锁失败/任何异常一律放行: 这道锁只为防串键, 绝不能因为权限或会话问题让工具起不来。
    句柄存进模块全局并持有到进程退出 (进程终止时内核自动释放)。
    onefile 冻结后只有子进程跑 __main__, 所以一次启动只建一把锁。"""
    global _SINGLE_MUTEX
    try:
        k32 = _K32_LE
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        h = k32.CreateMutexW(None, False, name)
        if not h:
            return True
        if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
            k32.CloseHandle(ctypes.c_void_p(h))
            return False
        _SINGLE_MUTEX = h
        return True
    except Exception:
        return True


def _single_instance_dialog():
    """第二个实例的提示框 —— 用 MessageBoxW 而不是 Tk: 此时不该再建任何窗口/线程/热键钩子。"""
    ctypes.windll.user32.MessageBoxW(
        None,
        "WARDOGS 炮兵助手已经在运行了, 这一个不会启动。\r\n\r\n"
        "同时开两个实例会一起往游戏里发方向键, QTE 会整行判错重来\r\n"
        "(这是「错误率突然变高」最常见的成因, 与识别精度无关)。\r\n\r\n"
        "请先关掉已有实例再启动:\r\n"
        "  任务管理器 -> 详细信息 -> 按名称找 WARDOGS_Artillery.exe -> 结束任务\r\n\r\n"
        "下载目录里的旧副本 (WARDOGS_Artillery (2).exe) 没有可见窗口也照样在发键,\r\n"
        "按映像名找最保险。",
        "WARDOGS 炮兵助手 %s - 已有实例在运行" % APP_VER,
        0x30)                                    # MB_ICONWARNING


if __name__ == "__main__":
    if not acquire_single_instance():
        _single_instance_dialog()
        sys.exit(0)
    app = App()
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        def _demo():
            app.target = (71.28, 102.39)
            app.gun = (71.43, 101.79)
            app.refresh()
            if not app.hud:
                app.toggle_hud()
        app.root.after(800, _demo)
    app.run()

