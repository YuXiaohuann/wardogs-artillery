# -*- coding: utf-8 -*-
"""
WARDOGS 迫击炮/火炮 坐标读取与射距计算工具
============================================
坐标读取 (鼠标取点, 单通道):
  全屏地图模式下鼠标旁会显示 "xNN.NN / yNN.NN" 白色悬停标签, 以鼠标为锚点裁一个小 ROI
  识别标签文字得到坐标 (与游戏显示完全一致)。
  F1 读目标 / F2 读炮位 均为鼠标取点: 鼠标移到对应坐标标签旁按键即得。
  (历史版本的 聊天框OCR炮位通道 与 地图轴刻度几何标定通道 已按用户要求移除, 2026-09-14。)
射距计算: 1 地图单位 = 100 m; 距离 = hypot(dx,dy)*100; 方位角 = atan2(dx,dy) (0=北, 顺时针);
  仰角 mil 查 range_tables.json (社区实测射表, 瞄具为自定义刻度, 不用纯物理弹道公式)。

用法:
  python artillery_tool.py selftest [img_map.png]   # 离线自检 (地图截图读悬停标签)
依赖: opencv-python, numpy, mss, tkinter(内置); 悬停标签识别走 ocr_win.ps1 (Windows 内置 OCR)
"""
import base64
import ctypes
import ctypes.wintypes
import cv2
import numpy as np
import itertools
import json
import os
import re
import sys
import math
import time
import subprocess
import tempfile
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PS_OCR = os.path.join(SCRIPT_DIR, "ocr_win.ps1")
PS_OCR_WORKER = os.path.join(SCRIPT_DIR, "ocr_worker.ps1")
CREATE_NO_WINDOW = 0x08000000
TABLES_PATH = os.path.join(SCRIPT_DIR, "range_tables.json")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULT_CONFIG = {
    # 鼠标锚定的悬停标签搜索框 [dx, dy, w, h] (相对鼠标像素偏移)
    "hover_box": [0, -125, 120, 115],
    "ocr_scale": 4,
    "ocr_thresh": 110,
    "weapon": "SPH-2",
    # 地图悬停实时跟踪目标: 后台轮询读到即更新目标坐标 (F8 开关)
    "live_target": True,
    # 分辨率自适应: 鼠标近旁大区域内按"两行白字"形状自动定位悬停标签,
    # 不再依赖 hover_box 的固定像素偏移 (偏移随分辨率/缩放/界面版本变化)
    "hover_auto": True,
    # v1.1 主界面"运行日志"面板是否展开 (收起时只显示最近事件一行)
    "ui_log_open": False,
    # QTE 方向箭自动输入 (F9 自动 / F10 单次): 识别屏幕上的 QTE 箭头行并依次发送方向键
    "qte_on": False,
    "qte_gap_ms": 90,     # 两个方向键之间的间隔 (只有 F10 开环单次用)
    # 单个方向键按住时长。30ms 是实测下限 (再短游戏会漏收); v1.6 提到 40ms 换
    # 漏键余量 —— 按错/漏按的代价是整行重来, 而 40ms 仍远小于 qte_min_gap_ms,
    # 对速度零影响 (按键节奏由最小键间隔和画面消耗反馈决定, 不由 hold 决定)。
    "qte_hold_ms": 40,
    "qte_poll_ms": 50,    # F9 自动模式空闲轮询间隔 (越小越跟手, CPU 占用略升)
    "qte_fast_ms": 2,     # 按完一键后的快速重轮询间隔 (闭环靠"画面变化"推进)
    "qte_sweep_ms": 250,  # 锁定箭行小窗后, 每隔多久仍全横带扫一次 (兜底换位/漏检)
    "qte_sweep_relax_ms": 900,  # 已锁小窗且本行已按过键时, 兜底全扫放宽到此周期
    "qte_stall_max": 2,   # 看门狗: 同一行最多重决策几次 (防暂停帧/静态样张被无限重敲)
    "qte_stall_ms": 2500, # 看门狗冷却: 补发额度用尽且画面仍不变多久后强制重决策
    # 丢键兜底: 画面持续不变超过多久就补发最左那支一次。v1.6.4 与 qte_stuck_ms 对齐
    # 到 1200: 画面不变时两者同时到点, stuck 破局在函数开头抢先 (动作等价, 但会顺带
    # 按画面把记账回纠, 比单纯补发更准); 画面一直在变时补发/看门狗进不去, 只有 stuck
    # 能救 (用户报的"极低概率卡在一半不输入了")。
    "qte_retry_ms": 1200,
    "qte_retry_max": 3,   # 补发次数上限 (画面变化/箭行复位时自动恢复额度)
    "qte_model": "consume",  # consume=消耗型(WARDOGS 实测) / static=静止型
    "qte_gate": True,     # 仅前台是游戏进程时才识别 QTE (防桌面/浏览器误按)
    "qte_game_exe": "WardogsClient-Win64-Shipping.exe",
    # ---------------- v1.4 CPU 开销控制 ----------------
    # OpenCV 并行线程数。默认 12(=用满全部逻辑核)时, 一次全横带识别烧 66 ms CPU
    # 只换来 18.7 ms 墙钟; 限到 4 线程: 全横带 CPU 66->44 ms, 窄条带 33->14 ms,
    # 而墙钟几乎不变 (窄条带 20.4->21.0 ms)。更关键的是**不再每秒 20 次唤醒全部
    # 核心**, 与游戏的 worker 线程抢核/污染缓存的情况大幅减轻。0 = 用 OpenCV 默认。
    "cv2_threads": 4,
    # QTE 空闲轮询的 CPU 占空比上限 (单核百分比, 0 = 关闭调速器)。
    # 用实测 EMA 每轮开销反推最小 sleep, 保证 duty <= 预算: 换更高分辨率/更花画面
    # 时自动降频, 不会把一整个核心吃满。只约束**空闲态**; 按键后的快速窗与
    # 正在跟踪箭行时不受限 (延迟关键期, 且每次只持续 1~2 秒)。
    "qte_cpu_budget": 25,
    # 未锁定箭行时, 全横带兜底扫描的周期 (ms)。学到持久窄条带后, 常规轮询只扫
    # 条带 (0.7MP), 全横带 (2.2MP, 贵 3 倍) 只作为"箭行跑到条带外"的兜底。
    "qte_idle_sweep_ms": 1000,
    # 持久窄条带上下各留的余量 (占主屏高度比例)。QTE 行是固定 UI 位置, 0.06 足够。
    "qte_zone_pad": 0.06,
    # 悬停 OCR 轮询: 光标 2.5s 内没动过就把周期从 0.6s 放慢到此值 (读数不会变,
    # 纯省 CPU); 光标一动立刻回到 0.6s。
    "hover_idle_s": 1.5,
    # ---------------- v1.5 空闲 CPU 开销控制 ----------------
    # 前台不是游戏进程时**暂停**悬停轮询。实测一轮"鼠标旁没有标签"的空读要吃
    # 86 ms CPU (本进程 61 ms + PowerShell OCR worker 25 ms), 挂机/切出去看别的
    # 时候这份开销全是白烧的。暂停期间 F1/F2 仍走实时读取, 功能不受影响。
    "hover_gate": True,
    # 光标静止时, 若"鼠标近旁这块画面"与上一轮逐位相同就整轮跳过 OCR:
    # 画面没变 -> 悬停标签读数不可能变。跳过时把缓存时间戳顺延, F1/F2 兜底照旧。
    "hover_static_skip": True,
    # ---------------- v1.6 错误率控制 ----------------
    # 新行首键防抖: 同一方向序列需连续稳定读到这么久才允许按第一键。
    # 瞬态误读 (生成动画/画面撕裂/场景亮块凑行) 活不过 70ms, 而真行会稳定存在 1~2s。
    # v1.6.3: 70 -> 110ms。弹出动画半帧/爆炸亮块能"稳定地读错" 120~300ms, 旧的
    # 70+60=130ms 首键确认窗盖不住; 首键每行只多付 40ms, 之后每键节奏不变。
    "qte_row_stable_ms": 110,
    # 按键置信度下限: 本帧选中行里最差的"最佳-次佳模板 IoU 差"低于此值就宁可等一帧。
    # 真箭 margin 实测 0.2~0.6, 干扰物 <=0.1, 0.10 是留了余量的分界。
    "qte_press_margin": 0.10,
    # 两次实际 keydown 之间的最小间隔: 闭环靠画面推进, 低延迟屏/低显示滞后时
    # 消耗反馈可能来得很早, 连发太密会让游戏输入状态机串键 -> 整行重来。
    "qte_min_gap_ms": 100,
    # 空闲时红箭探针的周期 (只在灰度通道抓空时跑; 探针自带 stride-4 存在性闸,
    # 画面无红 ~0.2ms/次 = 4Hz 约 0.1% 单核; 有红才付 ~5ms/0.7MP 的全分辨率连通域)。
    "qte_red_probe_ms": 250,
    # 红箭行活跃期的轮询下限: 红箭只有颜色通道看得见, 消耗反馈也走它,
    # 8ms 既保证跟手又把这段的 CPU 占空比压在 ~13% 单核内。
    "qte_red_poll_ms": 8,
    # ---------------- v1.6.6 红行否决 ----------------
    # 红色箭行 = **上一行输入失败的反馈** (用户实测确认), 不是可按的提示。认出红行后
    # 这么久内禁止一切发键 (开新行/推进/补发/看门狗/发送线程在途键全停), 躲开失败
    # 动画的白残影与半帧; 250ms 对下一行首键几乎无感 (首键本来还要等 110ms 行稳定
    # + 60ms 候选窗)。0 = 只停认出红行的那一帧。
    "qte_red_hold_ms": 250,
    # ---------------- v1.6.1 / v1.6.2 错误率收口 ----------------
    # 每键候选稳定窗: 同一个 (方向, 下标, 总数) 需连续读到这么久才真按下去。
    # 单帧读花 (动画半帧/画面撕裂) 活不过 60ms, 真箭行稳定存在 1~2s。
    "qte_press_stable_ms": 60,
    # 从别的应用切回游戏后的静默期: 切窗瞬间游戏可能在播恢复动画, 箭行读不稳,
    # 键也可能落在切换缝里 -> 这段时间一律不按。
    "qte_fg_settle_ms": 350,
    # 行没按完时, "与记账行不吻合的读法" 要连续稳定这么久才认作无关新行并复位。
    # 400ms 足以让爆炸/闪光造成的持续读花过去, 又不会让连续两行之间明显变慢
    # (上一行按完 -> 立刻复位, 不走这道闸)。
    "qte_row_switch_ms": 400,
    # 决策黑匣子: 每轮 QTE 决策写一行到 <exe 目录>/qte_trace.log (4MB 轮转)。
    "qte_trace": True,
    # 切片护栏开关 (true = v1.6.2 行为)。false 只用于 A/B 对比/排障: 关掉后
    # "尾部读少几支/残影" 这类读法会重新掉进复位分支, 错误率明显上升。
    "qte_slice_guard": True,
    # ---------------- v1.6.3 幻影增长行修复 ----------------
    # 单行方向键数量上限。实测真行 2~4 支 (artillery.log 4700 行里 4 支占 96%),
    # 读到 7/8 支必是两行粘连或大面积读花 -> 直接不开行、也不去撑长记账行。
    # 若某模式确有更长的行, 把这个值调大即可 (0 = 不设上限)。
    "qte_row_max": 6,
    # "行还在往右弹出" 的候选记账行有效期。候选必须被后续画面**消耗佐证**
    # (某帧真的等于 候选[off:], off>=已按数) 才提交; 拿不到佐证就到期作废。
    # 多检一支的读花下一帧就没了, 永远佐证不上 -> 不会再按到幻影箭。
    "qte_grow_ttl_ms": 600,
    # 增长确认开关 (true = v1.6.3 行为)。false 只用于 A/B 对比/排障: 关掉后回到
    # v1.6.2 的"单帧即撑长记账行", 幻影箭错键会明显回来。
    "qte_grow_confirm": True,
    # ---------------- v1.6.4 错键/超时双收口 ----------------
    # 画面尾段是记账行的切片、且起点正好是"下一支该按的键"时照常按下。
    # 关掉(=v1.6.3)会在"记账行被幻影撑长 + 画面只剩最后一支"时一直沉默到
    # qte_retry_ms 补发甚至超时(用户报的"卡在一半不输入"); 而两种解释下
    # 要按的本来就是同一个键, 放行不增加错键风险。
    "qte_slice_press_last": True,
    # 记账行**已按完**却又长出新箭 = 弹出动画的真增长(此时没有待按的键可供
    # "消耗佐证", 继续等候选只会超时) -> 立即采纳并按新出现那支。
    "qte_grow_adopt_done": True,
    # 上一条只在"距上次实际发键 <= 该毫秒数"内认作同一行的尾巴; 超过就当新行,
    # 免得把下一行误接到旧行屁股上。
    "qte_grow_done_ms": 800,
    # 记账行尾部自愈: 画面连续这么久只显示记账行的前半截尾段, 就把幻影尾巴剪掉
    # (否则这一行永远差几支按不完, 只能干等补发/看门狗)。0 = 关闭。
    "qte_trim_ms": 0,
    # 自适应确认窗: 只在"画面刚出现过解释不了的读法"(读花/切片噪声/复位)后
    # qte_conflict_ms 内, 把按键确认窗 / 首键稳定窗临时拉长到下面两个值;
    # 画面干净时仍走短窗 -> 常态零额外延迟。0 = 关闭(退回 v1.6.3 固定窗)。
    "qte_press_stable_dirty_ms": 0,
    "qte_row_stable_dirty_ms": 0,
    "qte_conflict_ms": 250,
    # 卡死破局: 一行按到一半, 连续这么久**没发出任何键**就强制收口 —— 按画面把记账
    # 回纠并按最左那支; 画面与记账行完全对不上就整行复位重来。旧的补发/看门狗只在
    # "画面完全不变" 时才生效, 画面被读花持续扰动时一行会无声无息挂死
    # (用户报的"极低概率卡在一半不输入了")。0 = 关闭。
    "qte_stuck_ms": 1200,
    # 每行最多破局几次 (暂停帧/静态样张不会被无限重敲)。
    "qte_stuck_max": 2,
    # 残行救回: 半途复位后, 画面只剩一支孤箭而它正好是"还欠游戏的那一支"时按掉它
    # 的有效期。否则 "单支箭不开新行" 会把最后一支永远忽略掉 = 这一行挂死。0 = 关闭。
    "qte_ghost_ms": 1500,
    # v1.6.5 抗画面变化的自适应亮度通道。主通道用绝对亮度窗 (180~248) 分割箭填充,
    # 画面整体变亮/变暗/泛光/爆炸闪光/夜战压暗时会**整行抓空** (真实样张实测:
    # bright ±40 全丢、gain 0.7 全丢、暗幕 >=0.2 只剩 1/4 档、bloom 三档全丢、
    # 模糊 sigma>=1.0 只剩 1/3 档); 而色相/饱和度/色温/JPEG/噪声/缩放六族 100% 通过
    # —— 说明瓶颈在"分割", 不在"判方向"。自适应通道改用相对分割 (顶帽/Otsu/CLAHE)
    # + 相对描边环检, 只在主通道抓空时才跑, 常态零开销; 全套形状门更严 (IoU 0.66 /
    # margin 0.12), 最坏情况仍是"抓空等下一轮", 不会变成按错键。
    "qte_adapt": True,
    # 空闲期探测限流 (1 秒内见过箭行/按过键时每轮都跑, 不受此限)。越大越省 CPU,
    # 代价是"画面突变把主通道打瞎"时首键最多多等这么久。
    "qte_adapt_probe_ms": 400,
    # 分割方法优先级, 逗号分隔; 留空 = 内置 "th25,th61,otsu,clahe"。
    # th<K>=顶帽核 K 像素 (对整体亮度偏移完全不变), otsu=全局自适应阈, clahe=局部均衡。
    "qte_adapt_methods": "",
}

# 已移除功能 (聊天OCR/几何标定/连续监控/语音播报) 的遗留配置键, 加载时自动清理
LEGACY_KEYS = ("chat_roi", "map_roi", "geo_calib", "auto_monitor", "voice")

# ---------------------------------------------------------------- OCR 基础
_TMP_SEQ = itertools.count()

def _ocr_bin(g):
    """二值图 -> Windows 内置 OCR (WinRT) 文本; 常驻 worker, 不可用时回退隐藏单次调用。
    临时文件名每次唯一: 后台悬停轮询线程与主线程(F1/F2/离线自检)会并发进来,
    共用 wd_ocr_<pid>.png 时一方的 imwrite 会覆盖另一方正要送 OCR 的图 ->
    读到错坐标或半张写坏的 PNG (真竞态, 偶发且难复现)。"""
    tmp = os.path.join(tempfile.gettempdir(), "wd_ocr_%d_%d_%d.png"
                       % (os.getpid(), threading.get_ident(), next(_TMP_SEQ)))
    try:
        cv2.imwrite(tmp, cv2.cvtColor(g, cv2.COLOR_GRAY2BGR))
        text = None
        try:
            text = OCR_WORKER.ocr_file(tmp)
        except Exception:
            text = None
        if text is None:  # 常驻 worker 不可用时回退单次调用 (隐藏窗口, 不弹控制台)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS_OCR, "-Path", tmp],
                capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
            text = (r.stdout or b"").decode("utf-8", "replace")
        return (text or "").strip()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _white_mask(up, k=1.0, floor=170, blur=0.0, close=0):
    """近白+低饱和像素 = 悬停标签白字。明亮地形上全局阈值会把背景一起判白,
    故改用颜色掩膜; 再按字符尺度过滤连通块, 去掉地图亮色区块(高饱和绿/棕)与噪点。
    k = ocr_scale/4.0: 连通块阈值必须随放大倍率缩放, 否则用户一改 ocr_scale
    字符就被整批过滤掉(掩膜全黑 -> OCR 恒空), 而这看起来像"识别不了坐标"。

    floor/blur/close = 低分辨率"鲁棒重采"参数 (见 ocr_bgr(robust=True)):
    1080p/720p 下标签字符只有 8~14px 高, 抗锯齿把笔画冲淡到 mn<170, 标准掩膜只剩半截笔画,
    OCR 读出 'y102 39 xn 28' 这种残字 -> 表现为"换了分辨率就读不到坐标"。
    降 floor 把淡笔画收回来, 高斯模糊把断笔连上, 形态学闭运算补掉笔画内小洞。"""
    b, g, r = cv2.split(up)
    mn = np.minimum(np.minimum(r, g), b).astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b).astype(np.int16)
    mask = (((mn > int(floor)) & ((mx - mn) < 60)).astype(np.uint8)) * 255
    if blur and blur > 0:
        sig = float(blur)
        ksz = int(2 * round(2.0 * sig) + 1)          # 核必须为奇数
        mask = cv2.GaussianBlur(mask, (ksz, ksz), sig)
        mask = ((mask > 127).astype(np.uint8)) * 255
    if close and close >= 2:
        kk = int(close) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((kk, kk), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    k = max(0.25, float(k))
    hmin, hmax = max(1, int(round(8 * k))), max(2, int(round(150 * k)))
    wmin, wmax = max(1, int(round(4 * k))), max(2, int(round(220 * k)))
    amin = max(2, int(round(10 * k * k)))          # 面积随 k^2; 含小数点(4x 约 10px 高)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if hmin <= h <= hmax and wmin <= w <= wmax and area >= amin:
            out[lab == i] = 255
    return out


def _med_char_h(img):
    """1x 裁片内字符连通块高度中位数 (取不到时按 12px 估)。
    robust 放大倍率锚到"放大后字高约 48px", 于是 720p 的 8px 小字和 4K 的 40px 大字
    送进 WinRT OCR 时清晰度一致 —— 这是"换分辨率也能读"的关键, 固定倍率做不到。"""
    try:
        b, g, r = cv2.split(img)
        mn = np.minimum(np.minimum(r, g), b).astype(np.int16)
        mx = np.maximum(np.maximum(r, g), b).astype(np.int16)
        m = (((mn > 140) & ((mx - mn) < 60)).astype(np.uint8)) * 255
        n, _l, st, _c = cv2.connectedComponentsWithStats(m, 8)
        hs = [int(st[i][3]) for i in range(1, n) if 4 <= st[i][3] <= 60 and 2 <= st[i][2] <= 60]
        if hs:
            hs.sort()
            return float(hs[len(hs) // 2])
    except Exception:
        pass
    return 12.0


# 低分辨率"鲁棒重采"配方 —— 两族, 按 1x 字高自动选序。
# up 族 (先放大彩色, 再掩膜): (目标字高 px, floor, blur 系数 x k, close, 插值 LANCZOS?, unsharp?)
#   依据 work/exp2 参数扫描 (sample_map.png 缩到 0.45x~1.5x): 字高 10~40px 时,
#   floor 降到 120 把抗锯齿淡笔画收回来, 高斯模糊把断笔连上, 字高锚到 52~80px
#   后 1080p~4K 送进 WinRT OCR 的清晰度一致。
ROBUST_RECIPES = (
    (52.0, 120, 0.6, 3, True,  True),
    (52.0, 140, 0.9, 3, False, False),
    (80.0, 120, 0.6, 3, True,  False),
)
# m1 族 (先在 1x 原图掩膜, 再放大二值掩膜): (目标字高 px, floor, 插值 LANCZOS?, 3x3 膨胀次数)
#   work/exp5 扫描结论: 1x 字高只有 8px (1080p 线性缩放的最坏情形) 时 up 族全军覆没 ——
#   插值把抗锯齿灰边一起放大, 再用 floor 一刀切只剩半截笔画, OCR 只能读残字;
#   而"原图先按颜色取笔画 -> 放大掩膜 -> 二值化"保住了笔画拓扑, 'Y102.39 x 71.28' 完整读回。
#   下面 3 个组合在块级/行级都不产出错值 (行级 tgt64+floor140+cubic 会把 71.28 读成 77.28, 已剔除)。
ROBUST_RECIPES_M1 = (
    (48.0, 120, False, 0),
    (48.0, 120, True,  0),
    (64.0, 120, True,  0),
)
# 1x 字高中位数低于此阈值才先走 m1 族: 字高 >=10px 时 up 族已稳定命中, 不必多付 OCR 开销。
M1_GATE = 9.5


def _mask_1x(img, floor):
    """1x 原图上的白字颜色掩膜 (不做连通块过滤) —— m1 族第一步。
    放大前取掩膜, 笔画边缘还是原图那一圈抗锯齿像素, 放大后二值化能连成完整字形;
    反过来先放大再阈值, 灰边被插值摊开, 阈值切下去笔画就断了。"""
    b, g, r = cv2.split(img)
    mn = np.minimum(np.minimum(r, g), b).astype(np.int16)
    mx = np.maximum(np.maximum(r, g), b).astype(np.int16)
    return (((mn > int(floor)) & ((mx - mn) < 60)).astype(np.uint8)) * 255


def ocr_robust(img, recipes=ROBUST_RECIPES, recipes_m1=ROBUST_RECIPES_M1, budget=None):
    """按配方逐个重采, 拿到严格配对(xNN.NN/yNN.NN)即停, 返回 OCR 原文。
    只在常规通道失败时调用 —— floor 降到 120 会把亮地形噪点一起收进来,
    常规分辨率下用它反而更容易误读, 所以不能当默认通道。
    配方顺序按 1x 字高自适应: 小字 (<M1_GATE) 先试 m1 族, 再退回 up 族。"""
    if img is None or img.size == 0:
        return ""
    hmed = _med_char_h(img)
    plan = [(u"m1", r) for r in recipes_m1] if hmed < M1_GATE else []
    plan += [(u"up", r) for r in recipes]
    best = ""
    best_ax = -1
    for kind, rec in plan:
        tgt = float(rec[0])
        sc = float(min(14.0, max(2.0, tgt / max(4.0, hmed))))
        if kind == u"m1":
            _tgt, floor, lanc, dil = rec
            m = _mask_1x(img, floor)
            m = cv2.resize(m, None, fx=sc, fy=sc,
                           interpolation=cv2.INTER_LANCZOS4 if lanc else cv2.INTER_CUBIC)
            m = ((m > 127).astype(np.uint8)) * 255
            if dil:
                m = cv2.dilate(m, np.ones((3, 3), np.uint8))
        else:
            _tgt, floor, bm, cl, lanc, us = rec
            up = cv2.resize(img, None, fx=sc, fy=sc,
                            interpolation=cv2.INTER_LANCZOS4 if lanc else cv2.INTER_CUBIC)
            if us:                              # 反锐化掩模: 把被抗锯齿冲淡的笔画边缘拉回高对比
                up = cv2.addWeighted(up, 1.6, cv2.GaussianBlur(up, (0, 0), 2.0), -0.6, 0)
            k = sc / 4.0
            m = _white_mask(up, k, floor=floor, blur=bm * k, close=cl)
        if int((m > 0).sum()) < 60:             # 掩膜几乎全黑: 这一配方在该裁片上没料, 省一次 OCR
            continue
        if budget is not None and not budget.take():
            break
        t = _ocr_bin(m)
        pairs = extract_pairs_strict(t)
        if group_xy(pairs):                 # 双轴齐才算命中。只拿到一个轴就早退的话,
            return t                        # 后面本可读全的配方永远轮不到 (f=0.5 实测踩过)
        ax = len(set(a for a, _v in pairs))
        if ax > best_ax:                    # 都不全: 留下轴数最多的那次, 供跨候选凑 x+y
            best_ax, best = ax, t
    return best


class _Budget:
    """单次 read_hover_target 的 OCR 调用配额。低分辨率下鲁棒重采一个框就要 3~6 次 OCR,
    不限量时一次读取能吃到 28 次调用 (实测 f=0.6, 约 1.0s) —— 实时跟踪轮询会被拖死。
    配额用尽后直接进宽松兜底级, 用已收集到的文本取值。"""

    def __init__(self, n):
        self.n = int(n)

    def take(self):
        if self.n <= 0:
            return False
        self.n -= 1
        return True


MAX_OCR_CALLS = 14


def _global_thresh(up, thresh):
    g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    if thresh:
        g = (g > thresh).astype(np.uint8) * 255
    return g


def ocr_bgr(img, scale=4, thresh=110, robust=False, budget=None):
    """裁片 -> 放大 -> 白字颜色掩膜 -> Windows OCR, 返回原始文本。
    掩膜读不出配对且掩膜几乎为空时, 回退旧的全局阈值通道 (暗底场景)。
    robust=True: 低分辨率小字重采 (LANCZOS 放到 ~48px 字高 + floor=140 + 模糊/闭运算),
    只在常规通道失败时才用 —— 它会把亮地形噪点一起收进来, 常规情况反而更容易误读。"""
    if img is None or img.size == 0:
        return ""
    if robust:
        return ocr_robust(img, budget=budget)
    if budget is not None and not budget.take():
        return ""
    up = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    mask = _white_mask(up, scale / 4.0)
    text = _ocr_bin(mask)
    if extract_pairs(text):
        return text
    if int((mask > 0).sum()) < 200 and (budget is None or budget.take()):
        t2 = _ocr_bin(_global_thresh(up, thresh))
        if extract_pairs(t2) or not text:
            return t2
    return text


class _OcrWorker:
    """常驻隐藏 PowerShell OCR 进程 (ocr_worker.ps1)。
    每次 OCR 不再新起 powershell 控制台窗口, 也省去 ~1 s/次 的启动开销;
    协议: stdin 写 PNG 路径一行, stdout 读回 base64(UTF-8 文本) 一行, ERR 前缀表示失败。
    父进程退出后管道关闭, worker 自行退出, 不会残留。"""

    def __init__(self):
        self.p = None
        self.lock = threading.Lock()

    def _start(self):
        self.p = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", PS_OCR_WORKER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        ready = self.p.stdout.readline().decode("utf-8", "replace").strip()
        if ready != "READY":
            self._kill()
            raise RuntimeError("ocr worker not ready: %r" % ready)

    def _kill(self):
        try:
            if self.p is not None and self.p.poll() is None:
                self.p.kill()
        except Exception:
            pass
        self.p = None

    def ocr_file(self, path):
        with self.lock:
            last = None
            for _ in (0, 1):  # 最多重启一次
                try:
                    if self.p is None or self.p.poll() is not None:
                        self._start()
                    self.p.stdin.write((path + "\n").encode("ascii"))
                    self.p.stdin.flush()
                    line = self.p.stdout.readline()
                    if not line:
                        raise RuntimeError("ocr worker closed pipe")
                    line = line.decode("utf-8", "replace").strip()
                    if line.startswith("ERR "):
                        raise RuntimeError(base64.b64decode(line[4:]).decode("utf-8", "replace"))
                    return base64.b64decode(line).decode("utf-8", "replace")
                except Exception as ex:
                    last = ex
                    self._kill()
            raise RuntimeError("ocr worker failed: %s" % last)

    def shutdown(self):
        with self.lock:
            try:
                if self.p is not None and self.p.poll() is None:
                    self.p.stdin.write(b"__QUIT__\n")
                    self.p.stdin.flush()
                    self.p.stdin.close()
                    try:
                        self.p.wait(timeout=3)
                    except Exception:
                        self.p.kill()
            except Exception:
                pass
            self.p = None


OCR_WORKER = _OcrWorker()


def shutdown_ocr():
    """退出时调用, 优雅结束常驻 OCR 进程。"""
    OCR_WORKER.shutdown()

OCR_FIX = (("B", "8"), ("D", "0"), ("S", "5"), ("Z", "2"), ("O", "0"), ("l", "1"),
           ("I", "1"), ("|", "1"), ("'", "."), (",", "."), ('"', "."), ("•", ""),
           ("\\", ""), ("/", ""), (" ", ""))

def normalize_ocr(text):
    for a, b in OCR_FIX:
        text = text.replace(a, b)
    return text

# 交替必须"长模式在前": 旧写法 \d{1,3}(?:\.\d{1,2})? 在 OCR 丢掉小数点时
# 会把 "x9613" 先吃成 "961" -> 9.61 (真值 96.13), "x10239" -> 1.02 (真值 102.39),
# 距离直接算错几百米。现在先试带点, 再试 4~5 位, 最后才是 1~3 位。
PAIR_RE = re.compile(r"([xXyY])(\d{1,3}\.\d{1,2}|\d{4,5}|\d{1,3})")

def extract_pairs(text):
    """'x71.43, y101.79' -> [('x',71.43), ('y',101.79)]; 无小数点时末两位视为小数"""
    out = []
    for m in PAIR_RE.finditer(normalize_ocr(text)):
        num = m.group(2)
        if "." not in num:
            num = num[:-2] + "." + num[-2:]
        out.append((m.group(1).lower(), float(num)))
    return out

def group_xy(pairs):
    """把 x/y 序列按出现顺序配成 (x, y) 坐标列表 (x/y 先后顺序均可)"""
    groups, cur = [], {}
    for axis, val in pairs:
        if axis in cur:
            if len(cur) == 2:
                groups.append((cur["x"], cur["y"]))
            cur = {}
        cur[axis] = val
        if len(cur) == 2:
            groups.append((cur["x"], cur["y"]))
            cur = {}
    return groups

# ---------------------------------------------------------------- 屏幕/鼠标
_MSS_TLS = threading.local()

def _mss_shared():
    """本线程复用的 mss 实例。每帧新建一个实测要多花 ~7ms
    (5120x1440 主屏上 3891x792 的 QTE 横带: 35ms -> 28ms); QTE 快速轮询
    一秒几十帧, 这笔开销直接压在按键间隔上。mss 实例非线程安全 -> 按线程各存一份。"""
    s = getattr(_MSS_TLS, "sct", None)
    if s is None:
        import mss
        s = mss.mss()
        _MSS_TLS.sct = s
    return s

def grab(region, gray=False):
    """截屏 region=(x,y,w,h) -> BGR 图; gray=True 时直接返回单通道灰度图。

    gray=True 给 QTE 轮询用: 它只吃灰度, 省掉 BGRA->BGR 那份同尺寸中间图
    (3MP 图上是一次 ~10MB 的分配+拷贝)。"""
    import mss
    x, y, w, h = region
    box = {"left": int(x), "top": int(y), "width": int(w), "height": int(h)}
    code = cv2.COLOR_BGRA2GRAY if gray else cv2.COLOR_BGRA2BGR
    try:
        shot = _mss_shared().grab(box)
    except Exception:
        _MSS_TLS.sct = None          # 分辨率切换/睡眠后 DC 失效 -> 丢掉, 下次重建
        with mss.mss() as sct:
            shot = sct.grab(box)
    return cv2.cvtColor(np.asarray(shot), code)

def mouse_pos():
    import ctypes
    pt = ctypes.wintypes.POINT()
    # GetPhysicalCursorPos: 不受进程 DPI 感知状态影响, 恒为物理像素,
    # 与 mss 截图像素空间一致 (Win8.1+); 老系统回退 GetCursorPos。
    if not ctypes.windll.user32.GetPhysicalCursorPos(ctypes.byref(pt)):
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def virtual_screen():
    """虚拟屏(多显示器)原点与尺寸; 进程 DPI 感知后为物理像素, 与 mss 截图一致。"""
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(76), u.GetSystemMetrics(77),
            u.GetSystemMetrics(78), u.GetSystemMetrics(79))

def enable_dpi_awareness():
    """把本进程设为 每显示器DPI感知(2) —— 和 mss.mss() 内部做的事一样,
    但必须由主线程在"创建任何窗口 / 第一次截屏"之前调用。

    不这么做会怎样(本机已实测): 进程启动时是 DPI 不感知, GetSystemMetrics 报
    2880x1152; 后台悬停轮询线程第一次 grab 时 mss 把进程翻成感知, 同一时刻
    桌面变成 3600x1440(125% 缩放)。也就是说坐标系会在运行中被另一个线程整体
    改掉 1.25 倍: GetCursorPos/GetWindowRect/Tk 的 winfo_x 语义当场变,
    翻转前存下来的 hud_pos 再读回来就错位, 光标是否落在 HUD 上的判断也会错。"""
    try:
        sh = ctypes.windll.shcore
        sh.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        sh.SetProcessDpiAwareness.restype = ctypes.c_long
        if sh.SetProcessDpiAwareness(2) == 0:      # S_OK
            return 2
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return 1
    except Exception:
        pass
    return 0


_TIMER_PERIOD_MS = 0


def enable_timer_resolution(ms=1):
    """把全进程定时器分辨率提到 ms 级 (winmm.timeBeginPeriod), 退出自动还原。

    Windows 默认 ~15.6ms 一格: time.sleep(0.002) 会睡成 15.6ms, 按住 30ms 的
    键也会被量化到 31.2/46.8ms。本机因浏览器常驻早已是 1ms 格(实测无差别),
    但换一台"干净"机器, QTE 快速窗轮询与按键按住时长会整体劣化 10ms 级。
    提权是进程级的, atexit 里 timeEndPeriod 还原, 不改变系统平时功耗策略。"""
    global _TIMER_PERIOD_MS
    if _TIMER_PERIOD_MS:
        return _TIMER_PERIOD_MS
    try:
        import atexit
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod.restype = ctypes.c_uint
        if winmm.timeBeginPeriod(ms) == 0:          # TIMERR_NOERROR
            _TIMER_PERIOD_MS = ms
            atexit.register(winmm.timeEndPeriod, ms)
    except Exception:
        pass
    return _TIMER_PERIOD_MS

# ---------------------------------------------------------------- 鼠标取点: 鼠标旁悬停标签
def _crop_origin(screen_img, mx, my, box):
    """与 _crop 相同的钳制逻辑, 但额外返回裁片原点 (x0, y0),
    供分辨率自适应搜索把裁片内坐标换算回屏幕坐标。"""
    dx, dy, w, h = box
    if screen_img is None:
        vx, vy, vw, vh = virtual_screen()
        x0, y0 = mx + dx, my + dy
        x1, y1 = min(x0 + w, vx + vw), min(y0 + h, vy + vh)
        x0, y0 = max(x0, vx), max(y0, vy)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return x0, y0, np.zeros((0, 0, 3), np.uint8)   # ocr_bgr 对空图返回 ""
        return x0, y0, grab([x0, y0, x1 - x0, y1 - y0])
    H, W = screen_img.shape[:2]
    x0, y0 = max(0, mx + dx), max(0, my + dy)
    x1, y1 = min(W, mx + dx + w), min(H, my + dy + h)
    return x0, y0, screen_img[y0:y1, x0:x1]

def _crop(screen_img, mx, my, box):
    """按锚点裁 ROI。screen_img=None 走实时截屏: 先把 region 钳进虚拟屏,
    光标贴屏幕边缘时 hover_box(向上 125px)会整块出屏, mss 对 0/负宽高直接抛异常,
    部分出屏则补黑边; 钳制后既不会抛, 也不会把黑边喂给 OCR。"""
    return _crop_origin(screen_img, mx, my, box)[2]

# 配置框没命中时的放大搜索框 (仅 hover_auto=False 的旧通道使用)
FALLBACK_BOXES = ((-90, -170, 280, 240), (-160, -60, 320, 260))

# 分辨率自适应搜索区: 鼠标近旁一大块 (物理像素), 覆盖 720p~4K 常见标签偏移
AUTO_SEARCH_BOX = (-340, -380, 680, 660)

# 检测掩膜的两级亮度门槛: 170 = 白字标准阈值; 130 = 极小字兜底。
# 1x 字高掉到 6~7px (720p 级) 时抗锯齿把笔画冲到 mn<170, 一级门槛下标签整块消失,
# 检测不出候选 -> 表现为"换到低分辨率就读不到坐标"。二级门槛只在一级零候选时才跑
# (多一次连通块分析, 约 5 ms), 常态不付这份开销。
DETECT_FLOORS = (170, 130)


def _auto_label_regions(screen_img, mx, my, max_cand=3, max_lines=4, floors=DETECT_FLOORS):
    """在鼠标近旁按"字符形状连通块 -> 行 -> 文本块"找悬停标签, 返回 (blocks, lines)。
    blocks: 1~4 行并成的文本块 (一次 OCR 拿全 x+y), 按与鼠标距离排序;
    lines : 单行候选 —— 低分辨率下整块 OCR 容易把 '71' 读成 'n', 单行识别率明显更高。
    rel_box 相对 (mx, my); scale 由块/行内字符中位高度反推 (放大后字高约 40px), 与分辨率无关。
    距离门槛: 悬停标签只出现在光标近旁, 远处的亮地形/其他 UI 白字即使形状像文本
    也不能当候选 —— 否则鼠标停在别处时会把别处的数字读成目标坐标。
    门槛 = min(屏幕比例上限, 候选字高 x 9), 于是 720p 小字和 4K 大字用同一条规则。
    floors: 逐级放宽白字判定, 前一级零候选才进下一级 (见 DETECT_FLOORS)。"""
    ox, oy, img = _crop_origin(screen_img, mx, my, AUTO_SEARCH_BOX)
    if img is None or img.size == 0:
        return [], []
    H = float(screen_img.shape[0]) if screen_img is not None else float(virtual_screen()[3])
    dmax = max(150.0, 0.16 * H)
    bc, lc = [], []
    for floor in floors:
        bc, lc = _scan_label(img, ox, oy, mx, my, floor, dmax, max_cand, max_lines)
        if bc:                     # 拿到文本块就停: 常态走一级门槛, 不付二级开销
            return bc, lc
    return bc, lc                  # 各级都没有块 -> 退回最后一级找到的单行候选


def _scan_label(img, ox, oy, mx, my, floor, dmax, max_cand=3, max_lines=4):
    """单级亮度门槛下的扫描: 字符连通块 -> 行 -> 文本块, 返回按距离排序的 (blocks, lines)。"""
    b, g, r = cv2.split(img)
    mn = np.minimum(np.minimum(r, g), b).astype(np.int16)
    mxx = np.maximum(np.maximum(r, g), b).astype(np.int16)
    mask = (((mn > int(floor)) & (mxx - mn < 60)).astype(np.uint8)) * 255
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        # 1x 下宽松字符尺度: 720p 字高约 5px, 4K 约 40px, 全部落在 [4, 90]
        if 4 <= h <= 90 and 2 <= w <= 70 and area >= 8 and w <= 6 * h:
            comps.append([int(x), int(y), int(w), int(h)])
    if len(comps) < 4:
        return [], []
    comps.sort(key=lambda c: c[1] + c[3] / 2.0)
    lines = []
    for c in comps:                      # y 中心相近 -> 同一行
        cy = c[1] + c[3] / 2.0
        for L in lines:
            if abs(cy - L["cy"]) <= 0.7 * max(L["h"], c[3]):
                L["cs"].append(c)
                L["cy"] = sum(q[1] + q[3] / 2.0 for q in L["cs"]) / len(L["cs"])
                L["h"] = max(L["h"], c[3])
                break
        else:
            lines.append({"cs": [c], "cy": cy, "h": c[3]})
    for L in lines:
        L["x0"] = min(q[0] for q in L["cs"]); L["x1"] = max(q[0] + q[2] for q in L["cs"])
        L["y0"] = min(q[1] for q in L["cs"]); L["y1"] = max(q[1] + q[3] for q in L["cs"])
    # 剔掉"亮地形糊成的假行": 真文本行宽度有上限 (标签一行 ~6 字符), 字高也有上限;
    # 不剔的话假行 h 很大, 下面并块的 6 倍字高窗口会被它撑到把整屏并成一块 -> 候选全灭。
    lines = [L for L in lines if (L["x1"] - L["x0"]) <= 320 and L["h"] <= 48]
    lines.sort(key=lambda L: L["cy"])
    blocks = []
    for L in lines:                      # 垂直相邻 + 水平重叠 -> 同一文本块 (标签=2 行)
        for B in blocks:
            last = B[-1]
            vgap = L["y0"] - last["y1"]
            ovl = min(L["x1"], last["x1"]) - max(L["x0"], last["x0"])
            # 标签两行可能隔得远 (y 行贴水平十字线, x 行贴垂直十字线, 实机样张隔 ~4 倍字高),
            # 放宽到 6 倍字高 + 水平有重叠即并块; 误并的无关白字没有 x/y 前缀, 不影响配对。
            if vgap <= 6.0 * min(L["h"], last["h"]) and ovl > 0:
                B.append(L)
                break
        else:
            blocks.append([L])

    def rel_box(x0, y0, x1, y1, hmed):
        cx, cy = ox + (x0 + x1) / 2.0, oy + (y0 + y1) / 2.0
        d = math.hypot(cx - mx, cy - my)
        pad = max(3, int(round(0.7 * hmed)))
        # 距离门槛按候选自身字高收紧: 悬停标签紧贴光标, 实测中心距 = 4.6x~6.5x 字高
        # (整块 4.9x, 单行最远 6.5x), 而"鼠标不在标签上"时最近的干扰候选 >= 7.6x 字高。
        # 取 7x 两边都留 ~8% 余量。字高会随 UI 缩放同比变化, 所以这条规则 720p~4K 通用;
        # 固定像素门槛做不到 —— 同一个 150px 在 4K 上只有几个字宽, 在 1080p 上却能放进半屏亮地形。
        # 55px 下限防 hmed 被噪点低估时把真标签误杀。
        dmax_c = min(dmax, max(55.0, 7.0 * hmed))
        return d, (ox + x0 - pad - mx, oy + y0 - pad - my, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad), \
               float(min(8.0, max(2.0, 40.0 / max(4, hmed)))), dmax_c

    bc, lc = [], []
    for B in blocks:
        if not (1 <= len(B) <= 4):
            continue
        x0 = min(l["x0"] for l in B); x1 = max(l["x1"] for l in B)
        y0 = min(l["y0"] for l in B); y1 = max(l["y1"] for l in B)
        hs = [q[3] for l in B for q in l["cs"]]
        if len(hs) < 4 or not (10 <= y1 - y0 <= 220) or not (24 <= x1 - x0 <= 300):
            continue
        hs.sort()
        d, box, sc, dmc = rel_box(x0, y0, x1, y1, hs[len(hs) // 2])
        if d <= dmc:
            bc.append((d, box, sc))
    for L in lines:                      # 单行候选: 像坐标行 (>=3 字符, 宽度/字高在合理区间)
        w = L["x1"] - L["x0"]
        if len(L["cs"]) < 3 or not (20 <= w <= 300) or not (5 <= L["h"] <= 60):
            continue
        d, box, sc, dmc = rel_box(L["x0"], L["y0"], L["x1"], L["y1"], L["h"])
        if d <= dmc:
            lc.append((d, box, sc))
    bc.sort(key=lambda t: t[0]); lc.sort(key=lambda t: t[0])
    return [(box, sc) for _d, box, sc in bc[:max_cand]], [(box, sc) for _d, box, sc in lc[:max_lines]]


def _auto_label_boxes(screen_img, mx, my, max_cand=3):
    """兼容包装: 只取块级候选 (自检/调试用)。"""
    return _auto_label_regions(screen_img, mx, my, max_cand)[0]

# 游戏标签恒为 xNN.NN/yNN.NN, 所以优先吃满 2 位小数; 1 位小数只在"后面不接数字"时才收
# (低分辨率下 'x71.28' 末位糊掉会读成 'x71.2', 这种读法误差 0.08 单位, 可接受但绝不外推)。
# 注意 normalize_ocr 会抹掉空格: 'y102.39 171.28' 连读成 'y102.39171.28', 若无 2 位小数优先
# 这条交替, 正则会退化成只匹配 1 位再被"后无数字"毙掉, 把本来正确的 y 一起丢掉。
PAIR_RE_STRICT = re.compile(r"([xXyY])(\d{1,3}\.\d{2}|\d{1,3}\.\d(?!\d))")

def extract_pairs_strict(text):
    """只认"带 2 位小数"的坐标对。放大搜索/回退框可能把标签切到一半,
    宽松正则会把半截数字静默读成错坐标; 严格模式只接受完整 xNN.NN/yNN.NN。"""
    return [(m.group(1).lower(), float(m.group(2)))
            for m in PAIR_RE_STRICT.finditer(normalize_ocr(text))]

# 丢小数点兜底用的归一化: 保留空格与 / \ (它们是"读残"的证据, 抹掉就会把
# 'x/ 1 28' 洗成 'x128' 再被猜成 x=1.28 —— 真值 71.28, 炮弹偏出几百米)
OCR_FIX_LOOSE = tuple(p for p in OCR_FIX if p[0] not in (" ", "/", "\\"))

def _norm_loose(text):
    for a, b in OCR_FIX_LOOSE:
        text = text.replace(a, b)
    return text

# 字母后必须紧跟数字(最多隔一个空格), 总位数 4~5: 'y102 39'->102.39, 'x7128'->71.28
PAIR_RE_LOOSE = re.compile(r"([xXyY])(\d{1,3}\.\d{1,2}|\d{4,5}|\d{1,3} \d{2})(?!\d)")

def extract_pairs_loose(text):
    """OCR 丢小数点时的兜底 (末两位当小数)。比旧 extract_pairs 严: 不接受 1~3 位
    连续数字 —— 低分辨率残读 'x/ 1 28' 会被旧规则猜成 1.28; 也不接受字母与数字之间
    隔着 / n ! 之类杂字符的读法。宁可这一轮读不到, 不给错坐标。"""
    out = []
    for m in PAIR_RE_LOOSE.finditer(_norm_loose(text)):
        num = m.group(2).replace(" ", "")
        if "." not in num:
            num = num[:-2] + "." + num[-2:]
        out.append((m.group(1).lower(), float(num)))
    return out

def hover_signature(cfg, anchor=None):
    """鼠标近旁"悬停读取会用到"的那块画面, 原样返回 (rect, img)。

    用途: 轮询线程比较前后两轮这块像素是否逐位相同 —— 相同就说明这一轮读数
    必然与上轮一致, 可以整轮跳过 (一轮空读实测 86 ms CPU, 见 hover_static_skip)。
    区域 = hover_box 与 AUTO_SEARCH_BOX 的并集 (hover_auto=False 时再并入
    FALLBACK_BOXES), 正好是 read_hover_target 实时截屏可能碰到的全部范围;
    并集之外的像素它根本不读, 变不变都与读数无关。
    抓不到 (整块出屏/异常) 时返回 (None, None), 调用方据此走正常读取路径。"""
    if anchor is None:
        anchor = mouse_pos()
    mx, my = anchor
    try:
        hb = tuple(cfg.get("hover_box") or DEFAULT_CONFIG["hover_box"])
    except TypeError:
        hb = tuple(DEFAULT_CONFIG["hover_box"])
    boxes = [hb, AUTO_SEARCH_BOX]
    if not cfg.get("hover_auto", True):
        boxes.extend(FALLBACK_BOXES)
    vx, vy, vw, vh = virtual_screen()
    x0 = max(min(mx + b[0] for b in boxes), vx)
    y0 = max(min(my + b[1] for b in boxes), vy)
    x1 = min(max(mx + b[0] + b[2] for b in boxes), vx + vw)
    y1 = min(max(my + b[1] + b[3] for b in boxes), vy + vh)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None, None
    try:
        return (x0, y0, x1 - x0, y1 - y0), grab([x0, y0, x1 - x0, y1 - y0])
    except Exception:
        return None, None


def read_hover_target(cfg, screen_img=None, anchor=None, debug=None):
    """screen_img=None 时实时截屏; anchor=(mx,my) 默认当前鼠标。
    候选来源: 配置 hover_box (本机已标定, 最快) -> hover_auto 形状搜索的块/行
    (分辨率自适应, 不再依赖固定像素偏移) -> (hover_auto=False 时) FALLBACK_BOXES。
    取值分四级, 前一级拿到就直接返回:
      1 严格(带小数点) 常规 OCR          —— 本机/高分屏的正常路径
      2 严格 低分辨率鲁棒重采 + 单行重读  —— 1080p/720p 小字路径
      3 宽松(丢小数点末两位当小数)        —— OCR 读全了数字但丢了点
    错读防护: 宽松规则只认"字母紧跟 4~5 位数字", 形状候选带距离门槛。"""
    if anchor is None:
        anchor = mouse_pos()
    mx, my = anchor
    attempts = []          # (box, text, img, scale, auto?)
    budget = _Budget(MAX_OCR_CALLS)

    def try_box(box, scale=None, auto=False, robust=False):
        sc = float(scale or cfg["ocr_scale"])
        img = _crop(screen_img, mx, my, box)
        txt = ocr_bgr(img, sc, cfg["ocr_thresh"], robust=robust, budget=budget)
        attempts.append((list(box), txt, img, sc, auto))
        return txt

    def pick_round(pick, items):
        for _box, txt, _img, _sc, _a in items:
            groups = group_xy(pick(txt))
            if groups:
                return groups[-1]
        # 单块没凑齐双轴 (标签两行被拆成两个候选/配置框切掉一行): 跨候选凑 x+y。
        # 全屏地图同一时刻只有一个悬停标签, 跨候选配对不会张冠李戴。
        px = py = None
        for _box, txt, _img, _sc, _a in items:
            for axis, val in pick(txt):
                if axis == "x":
                    px = val
                else:
                    py = val
        if px is not None and py is not None:
            return (px, py)
        return None

    def step(box, scale=None, auto=False, robust=False):
        """跑一次 OCR 立刻试凑双轴 —— 凑齐就返回, 不再白跑后面几次 OCR。
        低分辨率下 x 和 y 常常分散在"块候选给 y、行候选给 x"两次识别里,
        一次性跑完全部候选再统一取值, 1080p 下单次要 0.5s, 实时跟踪会掉帧。"""
        if budget.n > 0:
            try_box(box, scale, auto=auto, robust=robust)
        return pick_round(extract_pairs_strict, attempts)   # 配额用尽也要再凑一次: 双轴可能刚好齐

    # ---- 第一级: 常规 OCR (配置框 -> 形状搜索块 -> 旧回退框) ----
    auto_blocks, auto_lines = [], []
    got = step(tuple(cfg["hover_box"]))
    if got:
        return got
    if cfg.get("hover_auto", True):
        auto_blocks, auto_lines = _auto_label_regions(screen_img, mx, my)
        for box, sc in auto_blocks:
            got = step(box, sc, auto=True)
            if got:
                return got
    else:
        for box in FALLBACK_BOXES:
            got = step(box)
            if got:
                return got

    # ---- 低分辨率第二级: 鲁棒重采 + 单行重读 ----
    # 触发门槛: 常规 OCR 读到过数字, 或形状搜索找到了像文本的块 (标签大概率在框内)。
    # 鼠标不在标签上时常规 OCR 基本返回空串且形状搜索无候选, 不付这份开销。
    has_ink = any(re.search(r"\d", normalize_ocr(t)) for _b, t, _i, _s, _a in attempts) \
              or bool(auto_blocks)
    if has_ink:
        # 便宜的先跑: 单行常规 OCR 一次调用, 常常直接给出 'x71 28' / 'y102 39' 这种
        # "数字全对、只是小数点变空格"的读法; 鲁棒重采一个框要 3~6 次调用, 排后面。
        # retry 把 auto 候选排在配置框之前: hover_box 是本机标定的固定像素框,
        # 换了分辨率往往切歪, 拿它做鲁棒重采是白花调用。
        retry = [a for a in attempts
                 if (re.search(r"\d", normalize_ocr(a[1])) or (a[4] and a[1].strip()))]
        retry.sort(key=lambda a: 0 if a[4] else 1)      # 稳定排序, 同级仍按距离
        if not retry:
            retry = [a for a in attempts if a[4]][:1]
        for box, sc in auto_lines[:2]:
            got = step(box, sc, auto=True)
            if got:
                return got
        # 旧通道 (hover_auto=False) 没有形状候选, 两个回退框都值得重采一次
        n_rob = 1 if auto_blocks else 2
        for box, _txt, _img, sc, auto in retry[:n_rob]:
            got = step(box, sc, auto=auto, robust=True)
            if got:
                return got
        # 单行也读残 -> 对最近两行再走一次鲁棒重采
        for box, sc in auto_lines[:2]:
            got = step(box, sc, auto=True, robust=True)
            if got:
                return got

    got = pick_round(extract_pairs_loose, attempts)
    if got:
        return got
    if debug is not None:
        for box, txt, img, _sc, _a in attempts:
            debug.append({"box": box, "text": txt, "img": img})
    return None

# ---------------------------------------------------------------- QTE 方向箭
# 游戏 QTE 提示是一横排方向箭 glyph: 暗描边 (<110) 包着亮填充 (180~248)。
# 分割对象选**亮填充连通域**而不是暗描边: 填充域本身就是干净的实心箭形状,
# 而描边域会被"当前选中箭"外面那圈高亮框的暗边并进去 (框暗边贴着箭描边,
# 8-连通就成一个组件), 把 bbox 和形状全污染。填充域则天然与框隔离。
# 方向判定: 填充域 resize 到 32x32 后与运行时生成的 4 个标准箭模板求 IoU,
# 取最大且要求领先次大一定 margin。不用模板文件、不用固定像素尺寸 ->
# 换分辨率/缩放同一套参数继续工作 (与悬停标签的分辨率自适应同一思路)。
# 注意不能用质心判向: 实心箭的三角头质量集中在基底, 质心反而偏向箭尾, 方向会整体判反。
QTE_FILL_GRAY = (180, 248)   # 箭填充亮度窗 (天空 200~205 同窗但无暗描边环, 靠环检排除)
QTE_BRIGHT_BG_DENSITY = 0.30  # v1.6.7: 亮窗像素占比超过它 -> "亮底帧" (天空/白幕/雪地)
QTE_EDGE_PAD = 3              # v1.6.7: 裁图边界的安全距离 (像素)
QTE_EDGE_CLIP_W = 1.0         # v1.6.7: 最左箭左边缘距裁图边界 <= 1 支箭宽 -> 疑似裁掉
QTE_SIZE = (10, 170)         # 填充域 bbox 边长范围 (物理像素, 覆盖 720p~8K)
QTE_FILL_RATIO = (0.35, 0.80)  # 填充率 (箭 ≈0.50~0.58; 细条/实心块被排除)
QTE_RING_MEAN = 130          # 描边环平均亮度上限
QTE_RING_DARK = 0.50         # 第 2 圈中 <110 像素占比下限
QTE_RING1_MEAN = 150         # 紧贴填充的第 1 圈 (=箭自身暗描边) 平均亮度上限
QTE_RING1_DARK = 0.35        # 第 1 圈中 <110 像素占比下限 (低分辨率抗锯齿后描边变淡)
QTE_BAND_EDGE = 130          # 合并带“暗”判定阈值 (实机“下一支”描边核心 111~113, 用 <110 会漏)
QTE_BAND_MEAN = 132          # 第三通道: 外圈合并带 (ring1+ring2) 平均灰度上限
QTE_BAND_DARK = 0.45         # 第三通道: 合并带内 <QTE_BAND_EDGE 像素占比下限
QTE_IOU = 0.62               # 与最佳模板的 IoU 下限 (真箭 0.83~0.94, 桌面干扰 <=0.46)
QTE_IOU_MARGIN = 0.08        # 最佳与次佳模板 IoU 差下限
# v1.6 红箭通道: 游戏存在**红填充**箭行 (填充 RGB~(214,4,4), 灰度~67, 与它自己的
# 暗描边同灰度 -> 灰度图上完全不可见, 只能靠颜色饱和度识别)。R 足够大且 R-G / R-B
# 都足够饱和才算红填充; 掩膜用单趟 cv2.inRange 出 (比三通道减法快 ~8x:
# 0.73MP 6.8ms -> 0.83ms), 因为这是空闲期也要跑的探针, 成本必须压住。
# 砖墙/集装箱这类红棕大块 (G/B 偏高) 大多直接被掩膜挡掉, 漏网的靠尺寸+
# 填充率+IoU+行聚类四道门兜住。
QTE_RED_MIN = 140            # 红填充 R 通道下限
QTE_RED_GB = 95              # 红填充 G/B 上限 (等价 R-G>=45; 白箭 R=G=B 被排除)
QTE_RED_GATE_PX = 4          # 存在性闸: 抽样图里的红像素下限 (≈1000 实体像素)
QTE_RED_GATE_STRIDE = 4      # 存在性闸抽样步长
# v1.6.6 红行语义反转: 红色箭行是**按错键之后的失败反馈**, 不是"接下来该按这些"。
# v1.6~v1.6.5 把红行当 prompt 直接进按键管线 (src="red"), 等于向已经判死的行补键,
# 是用户截图里整行变红的来源之一。现在红像素**永不做可按键候选**: 认出红箭行就
# 整帧否决 (所有通道一律不出候选), 由调用方据此抑制发键并把记账行记为死行。
QTE_RED_VETO_MIN = 2           # 红箭形状候选 >=2 支 -> 整帧否决 (负样本实测 >=2 误报 0)
QTE_RED_SAT = 45               # 填充色否决: 中心 R-max(G,B) 下限 (白箭 ~0, 红箭实测 >=100)

_QTE_TEMPLATES = None

def _qte_templates():
    """32x32 标准箭模板 (杆宽 0.56, 头占半边), 懒生成。"""
    global _QTE_TEMPLATES
    if _QTE_TEMPLATES is not None:
        return _QTE_TEMPLATES
    n, s = 32, 9
    def poly(pts):
        return np.array(pts, np.int32)
    t = {}
    m = np.zeros((n, n), np.uint8)
    cv2.rectangle(m, (n // 2 - s, 0), (n // 2 + s, n // 2), 1, -1)
    cv2.fillPoly(m, [poly([[0, n // 2], [n - 1, n // 2], [n // 2, n - 1]])], 1)
    t["down"] = m
    m = np.zeros((n, n), np.uint8)
    cv2.rectangle(m, (n // 2 - s, n // 2), (n // 2 + s, n - 1), 1, -1)
    cv2.fillPoly(m, [poly([[0, n // 2], [n - 1, n // 2], [n // 2, 0]])], 1)
    t["up"] = m
    m = np.zeros((n, n), np.uint8)
    cv2.rectangle(m, (n // 2, n // 2 - s), (n - 1, n // 2 + s), 1, -1)
    cv2.fillPoly(m, [poly([[n // 2, 0], [n // 2, n - 1], [0, n // 2]])], 1)
    t["left"] = m
    m = np.zeros((n, n), np.uint8)
    cv2.rectangle(m, (0, n // 2 - s), (n // 2, n // 2 + s), 1, -1)
    cv2.fillPoly(m, [poly([[n // 2, 0], [n // 2, n - 1], [n - 1, n // 2]])], 1)
    t["right"] = m
    _QTE_TEMPLATES = t
    return t

def _qte_match(sub, iou_min=None, margin_min=None):
    """填充域 mask -> (方向, 最佳IoU, margin); 不匹配返回 (None, 0, 0)。

    iou_min/margin_min 缺省时用主通道的 QTE_IOU / QTE_IOU_MARGIN。"""
    r = cv2.resize(sub, (32, 32), interpolation=cv2.INTER_AREA)
    g = (r > 0.5).astype(np.uint8)
    scores = {}
    for d, t in _qte_templates().items():
        inter = int((g & t).sum())
        union = int((g | t).sum())
        scores[d] = inter / float(union) if union else 0.0
    order = sorted(scores.items(), key=lambda kv: -kv[1])
    best, iou = order[0]
    margin = iou - order[1][1]
    if iou < (QTE_IOU if iou_min is None else iou_min) or \
            margin < (QTE_IOU_MARGIN if margin_min is None else margin_min):
        return None, iou, margin
    return best, iou, margin

def _red_mask(bgr):
    """红填充掩膜: R>=QTE_RED_MIN 且 G,B<=QTE_RED_GB (单趟 inRange, 出 0/255)。"""
    return cv2.inRange(bgr, (0, 0, QTE_RED_MIN), (QTE_RED_GB, QTE_RED_GB, 255))


def _red_present(bgr, stride=None, min_px=None):
    """廉价"画面里有没有饱和红"闸: stride 抽样后数一遍 (~0.2ms/0.7MP)。

    作用是把红通道那笔全分辨率连通域开销 (~5ms/0.7MP) 挡在"画面真的有红"
    之后 —— 空闲轮询绝大多数帧一点红都没有, 不该为它付钱。阈值故意取得很低
    (默认 4 个抽样点 ≈ 1000 实体像素 ≈ 半支 15px 小箭), 宁可多探一次,
    也不能把"行尾只剩一支小红箭"这种关键帧漏掉。"""
    if stride is None:
        stride = QTE_RED_GATE_STRIDE
    if min_px is None:
        min_px = QTE_RED_GATE_PX
    sub = bgr[::stride, ::stride]
    return int(np.count_nonzero(_red_mask(sub))) >= min_px

def _row_fill_red(bgr, row, sat=None):
    """选中行里任一支箭的中心 3x3 填充是饱和红? (v1.6.6 填充色否决, 第三道闸)

    形状闸 (QTE_RED_VETO_MIN) 抓的是"红掩膜里能凑成箭形"的行; 但爆炸闪光/白幕会把
    红填充整体抬进主通道亮度窗 (67+90 -> 157, +0.5 增益 -> 194), 灰度/自适应通道会
    把红箭当白箭读出来 —— 这时红掩膜因为 G/B 也被抬高而漏检, 只有"填充本身还是红的"
    这一条物理事实可用。中心 3x3 对四个方向都落在实心填充上, 单次 mean ~2us。
    白箭 R≈G≈B (差 ~0), 白衬底/白选框同理, 不会误否决。"""
    if sat is None:
        sat = QTE_RED_SAT
    h, w = bgr.shape[:2]
    for c in row:
        x, y = int(round(c[0])), int(round(c[1]))
        if not (1 <= x < w - 1 and 1 <= y < h - 1):
            continue
        m = bgr[y - 1:y + 2, x - 1:x + 2].reshape(-1, 3).mean(0)
        if m[2] - max(m[0], m[1]) >= sat:
            return True
    return False

# ---------------- v1.6.5 抗画面变化的自适应亮度通道 ----------------
# 主通道用**绝对**亮度窗 (QTE_FILL_GRAY = 180~248) 分割箭填充, 画面整体变亮/变暗/
# 泛光/爆炸闪光/夜战压暗时会整行抓空 —— 真实样张实测: bright ±40 全丢、gain 0.7 全丢、
# 半透明暗幕 >=0.2 只剩 1/4 档、bloom **三档全丢**、模糊 sigma>=1.0 只剩 1/3 档。
# 而**方向判定本身与亮度无关** (IoU 跑在二值掩膜上, 实测 hue/sat/色温/JPEG/噪声/缩放
# 六族 100% 通过), 所以只要把"哪些像素算填充"换成相对量, 后面整套门都能照用。
# 自适应通道 = 只在主通道(灰度+红)抓空时才付费的兜底, 四条相互独立的相对分割:
#   th<K>  顶帽 (gray - 均值滤波<K>) 再 Otsu: 对整体亮度偏移**数学上完全不变**
#          ((g+v)-(blur(g)+v) = g-blur(g)), 对泛光/暗幕/gamma 近似不变 —— 主力
#   otsu   全局 Otsu 亮侧: 阈值随直方图走, 治均匀变亮/变暗
#   clahe  局部对比度均衡后仍用原窗: 治大面积逆光/局部强光
# 四条都必须再过掩膜密度/连通域数/尺寸/填充率/**相对**描边环/更严 IoU/行规整度
# 全套门, 所以最坏情况是
# "仍然抓空"(=等下一轮), 不会变成"按错键" —— 沿用 v1.6 以来的 fail-safe 取向。
QTE_ADAPT_METHODS = ("th25", "th61", "otsu", "clahe")
QTE_IOU_ADAPT = 0.66             # 自适应通道形状门 (主通道 0.62): 仍略严于主通道。
QTE_IOU_MARGIN_ADAPT = 0.10      # 同上 (主通道 0.08)。v1.6.5 一度收到 0.74/0.14 去堵
#   亮底细十字误报, 实测发现密度闸已经能在掩膜层把它判废, 0.74 只是白挡掉泛光/模糊下
#   IoU 天然下降的真箭 (bloom 精确召回 50% -> 83%); 再放回 0.62/0.08 则单箭误报 +4,
#   0.66/0.10 是"召回最高且误报仍为 0"的拐点。
QTE_REL_RING1 = 18               # 相对环检: 紧贴填充的第 1 圈平均灰度至少比填充暗这么多
QTE_REL_RING1_DARK = 0.35        # 第 1 圈中"比填充暗 0.6x 上述差值"的像素占比下限
QTE_REL_BAND = 12                # 相对环检: 外圈合并带 (ring1+ring2) 的对应量
QTE_REL_BAND_DARK = 0.45
QTE_ADAPT_MAX_PX = 900000        # 超过此像素量先降采样再跑 (空闲全区 3MP -> ~0.5x)
# 三道"分割退化"闸 (v1.6.5 实测标定, 见 work\shaft_probe.py):
# 相对分割在"整幅都是纹理"的画面上会把大片背景当填充 —— 亮底细十字 38.8%、
# 白面板 62.3%、天空噪声 23.7%、栅栏 56.9%; 而真 QTE 箭行只有 2.9%~12.8%。
# 这种分割已经不是"找箭"而是"找亮块", 直接判废: 既堵掉唯一一类能凑成 >=2 箭
# 的误报 (亮底细十字 4x"上" IoU 0.661), 又省掉后面连通域那笔钱
# (天空噪声图 13917 个域 ≈ 28ms 白烧)。
QTE_ADAPT_MAX_DENSITY = 0.18     # 顶帽类掩膜面积占比上限 (真箭 <=0.128)
QTE_ADAPT_MAX_DENSITY_GLOB = 0.45  # otsu/clahe 的上限: 整幅亮天空时掩膜本来就大
QTE_ADAPT_MAX_COMP = 1200        # 连通域个数上限 (真箭 <=42; 地图纹理 1740)
# 多分割法之间怎么挑 (v1.6.5 实测标定, 见 work\adapt_xguard.py):
QTE_ADAPT_GOOD = 4               # 某法已给出这么多支箭就停止再试后面的法。本作 QTE
                                 # 行长绝大多数为 4; 上限设 4 与"四法全试"结果逐位相同,
                                 # 却省掉约一半分割开销。
QTE_ADAPT_XTOL = 0.6             # "最左 x 优先"容差 (单位: 箭宽), 见 _qte_adapt_cands

def _qte_adapt_mask(gray, method):
    """按方法名出一张相对分割掩膜; 未知方法/分割退化返回 None。"""
    if method.startswith("th"):
        try:
            k = int(method[2:])
        except ValueError:
            return None
        k |= 1                                  # 核必须为奇数
        if k < 3 or k > 401:
            return None
        th = cv2.subtract(gray, cv2.blur(gray, (k, k)))
        _t, m = cv2.threshold(th, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        return m if _qte_density_ok(m, QTE_ADAPT_MAX_DENSITY) else None
    if method == "otsu":
        _t, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        return m if _qte_density_ok(m, QTE_ADAPT_MAX_DENSITY_GLOB) else None
    if method == "clahe":
        cl = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        m = cv2.inRange(cl, QTE_FILL_GRAY[0], QTE_FILL_GRAY[1])
        return m if _qte_density_ok(m, QTE_ADAPT_MAX_DENSITY_GLOB) else None
    return None


def _qte_density_ok(mask, cap):
    """掩膜面积占比 <= cap ? 超了说明分割已退化成"整幅亮块/纹理", 判废。"""
    return bool(mask.size) and float(np.count_nonzero(mask)) <= cap * mask.size

def _qte_adapt_cands(gray, min_arrows, methods=None, size=None):
    """自适应通道: 依次试各分割法, 按「最左 x 优先 + 箭数最多」挑一个候选集;
    全部失败返回 (None, None)。

    为什么不是"首个够用就返回" (v1.6.5 实测, 180 次亮/糊族识别):
      * 首个够用会让 th25 的半行截胡 th61 的整行 —— 泛光/暗幕/模糊下 th61 常能补全,
        改成择优后精确召回 85.0% -> 92.2%;
      * 但一味取"箭数最多"会把首键正确率拖下去 (95.6% -> 95.0%)。闭环里真正致命的
        不是少读一支箭 (状态机的增长确认/消耗佐证能自愈), 而是**最左那支丢了** ——
        工具会把第二支当成待按的第一支发出去, 游戏判错, 整行重来。实测全部首键失手
        都是"结果 = 真值的后缀", 没有一例是方向判错;
      * 所以先用"最左箭 x 最小"锁定行头 (谁的行头更靠左, 谁更可能没丢箭), 只保留行头
        与它相差 <= QTE_ADAPT_XTOL*箭宽 的集合, 再在其中取箭数最多/中位置信最高。
    QTE_ADAPT_GOOD 用来控开销: 某法已给出这么多支就停止再试后面的法。"""
    pool = []
    for m in (methods or QTE_ADAPT_METHODS):
        mask = _qte_adapt_mask(gray, m)
        if mask is None:
            continue                      # 分割退化 (密度闸) -> 这一法不花钱也不冒险
        c = _qte_cands(gray, mask, rel=True, iou_min=QTE_IOU_ADAPT,
                       margin_min=QTE_IOU_MARGIN_ADAPT, size=size,
                       max_comp=QTE_ADAPT_MAX_COMP)
        if len(c) < min_arrows:
            continue
        c.sort(key=lambda z: z[0])
        pool.append((m, c))
        if len(c) >= QTE_ADAPT_GOOD:
            break
    if not pool:
        return None, None
    rx = min(p[1][0][0] for p in pool)
    ok = [p for p in pool
          if p[1][0][0] - rx <= QTE_ADAPT_XTOL * max(p[1][0][2], p[1][0][3])] or pool
    m, c = max(ok, key=lambda p: (len(p[1]),
                                  float(np.median([z[5] for z in p[1]]))))
    return c, m

def _qte_bright_bg(mask):
    """整幅是不是"亮底" (天空/白幕/雪地): 亮窗像素占比 >= QTE_BRIGHT_BG_DENSITY。

    直接吃调用方已经算好的 QTE_FILL_GRAY 掩膜 (0/255), 免得每帧再多跑一趟全图
    inRange + count_nonzero (3MP 上实测 ~2ms, 白天空本来就最需要省时间)。

    亮底帧上箭填充和背景几乎同亮度 (实测天空 204 / 箭填充 204~206), 箭是靠**暗描边**
    从背景里分出来的; 而主通道的描边环检用的是**绝对**暗门 (<110 / mean<130),
    实机天空描边却是 97~162 的中灰 -> 三道绝对环检全部失灵, 只剩自适应通道那条
    "比填充自身暗"的相对环检能过。本函数让主通道在亮底帧也打开 rel 环检,
    白天天空就不用等 400ms 的自适应限流才认出行。暗底帧保持原行为逐位不变。"""
    if mask is None or mask.size == 0:
        return False
    return (np.count_nonzero(mask) / float(mask.size)) >= QTE_BRIGHT_BG_DENSITY


def _qte_cands(gray, mask, rel=False, iou_min=None, margin_min=None, size=None,
               max_comp=None):
    """填充掩膜 -> 单箭候选 [[xc, yc, w, h, dir, margin], ...] (未聚类)。

    rel=True 时额外启用**相对**描边环检 (与填充自身亮度比, 不看绝对灰度),
    iou_min/margin_min 覆盖形状门, size=(下限,上限) 覆盖 bbox 边长门 (降采样时用)。
    默认参数下行为与 v1.6.4 逐位一致。"""

    n, lab, st, _cen = cv2.connectedComponentsWithStats(mask, 8)
    if max_comp is not None and n - 1 > max_comp:
        # 域数闸: 上千个碎块 = 纹理/噪点, 不是箭行; 早退省掉整个 Python 循环
        return []
    k3 = np.ones((3, 3), np.uint8)
    smin, smax = size if size else QTE_SIZE
    cands = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in st[i])
        if not (smin <= w <= smax and smin <= h <= smax):
            continue
        if not (0.70 <= w / float(h) <= 1.45):
            continue
        fillr = area / float(w * h)
        if not (QTE_FILL_RATIO[0] <= fillr <= QTE_FILL_RATIO[1]):
            continue
        sub = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        # 描边环检: 紧贴填充域的第 1 圈 (ring1) 就是箭自身的暗描边, 与背景无关 ——
        # 箭压在暗色物体/亮天空/选中框上都能过; 第 2 圈 (ring2) 采到的是描边外层+背景,
        # 只有背景偏暗时才可靠, 仅作补充通道。两圈都亮 (天空/亮面小碎块) 才排除。
        d1 = cv2.dilate(sub, k3, iterations=1)
        d2 = cv2.dilate(sub, k3, iterations=2)
        gsub = gray[y:y + h, x:x + w]
        ok1 = ok2 = ok3 = False
        ring1 = (d1 == 1) & (sub == 0)
        if ring1.sum() >= 8:
            rv = gsub[ring1]
            r1m, r1f = float(rv.mean()), float((rv < 110).mean())
            ok1 = r1m < QTE_RING1_MEAN and r1f >= QTE_RING1_DARK
        ring2 = (d2 == 1) & (d1 == 0)
        if ring2.sum() >= 8:
            rv = gsub[ring2]
            ok2 = (float(rv.mean()) < QTE_RING_MEAN
                   and float((rv < 110).mean()) >= QTE_RING_DARK)
        # 第三通道 (行尾“下一支”高亮箭): 白框亮衬底把描边抬到 111~113,
        # <110 占比只剩 0.22 -> ok1 失败; 但外两圈合并仍明显偏暗
        # (实测 mean 104.8 / <130 占比 0.75; 0.67x~2x 缩放下 mean<=124.7、
        # 占比>=0.50), 而干扰块 mean>=146 且占比<=0.23 -> 用合并带兜住。
        band = ring1 | ring2
        if band.sum() >= 16:
            bv = gsub[band]
            ok3 = (float(bv.mean()) < QTE_BAND_MEAN
                   and float((bv < QTE_BAND_EDGE).mean()) >= QTE_BAND_DARK)
        ok4 = ok5 = False
        # v1.6.7: 绝对环检已经过闸就不必再算相对量 —— 相对环检本来就是给"亮底帧
        # 绝对门全灭"兜底的, 让它每支候选都白跑一遍 mean/fraction 会把 2.85MP 全带
        # 的识别从 34ms 推到 57ms (实测)。改成**只对绝对门全灭的候选**付费:
        # 暗底帧逐位回到原开销, 亮底帧也只为那批真需要的箭算。
        if rel and not (ok1 or ok2 or ok3):
            # 相对环检: 描边环与**填充自身**的亮度差, 不看绝对灰度 -> 画面整体
            # 变亮(泛光/白幕/闪光)时绝对门 (<150/<130/<132) 会全灭, 这条不会。
            fm = float(gsub[sub == 1].mean())
            if ring1.sum() >= 8:
                rv4 = gsub[ring1]
                ok4 = (float(rv4.mean()) <= fm - QTE_REL_RING1
                       and float((rv4 <= fm - QTE_REL_RING1 * 0.6).mean())
                       >= QTE_REL_RING1_DARK)
            if band.sum() >= 16:
                bv5 = gsub[band]
                ok5 = (float(bv5.mean()) <= fm - QTE_REL_BAND
                       and float((bv5 <= fm - QTE_REL_BAND * 0.5).mean())
                       >= QTE_REL_BAND_DARK)
        if not (ok1 or ok2 or ok3 or ok4 or ok5):
            continue
        d, _iou, _margin = _qte_match(sub, iou_min, margin_min)
        if d is None:
            continue
        cands.append([x + w / 2.0, y + h / 2.0, w, h, d, _margin])
    return cands

def find_qte_arrows(screen_img=None, region=None, debug=False, min_arrows=2, gray_img=None,
                    color_img=None, red=True, stats=None,
                    adapt=False, adapt_methods=None, red_press=False):
    """识别屏幕上的 QTE 方向箭行, 返回 [(x中心, 方向), ...] 按从左到右排序;
    没找到返回 []。方向 ∈ down/left/right/up, 可直接喂 keyboard.send。
    screen_img=None 时自截 region (默认主屏中央横带)。
    gray_img: 调用方已截好并转灰度的图 —— QTE 快速轮询走这条, 一次转换都不多做。
    v1.6: color_img + red 打开红箭通道 (灰度通道抓空时才跑, 存在性闸+全分辨率);
    stats 字典回写 src (gray/red/adapt:<法>/None) 与选中行的 min_margin (按键置信度用)。
    v1.6.5: adapt=True 打开自适应亮度通道 —— 灰度+红都抓空时改用相对分割 (顶帽/Otsu/
    CLAHE) + 相对环检兜住"画面整体变亮/变暗/泛光"导致的整行漏检; adapt_methods 可
    覆盖方法优先级。这条只在抓空时付费, 调用方需自行限流 (见 gui_app._qte_scan)。
    v1.6.6: **红色箭行 = 输入失败的反馈, 不是可按的提示** (用户实测确认)。默认
    red_press=False 时红像素永不做可按键候选, 三道闸任一命中即整帧否决 (stats
    ["red_row"]=True, src=None, 返回空): ①红箭形状候选 >=QTE_RED_VETO_MIN; ②只有 1 支
    红箭且灰度通道本来也抓空 (堵自适应通道把孤红箭当亮块); ③选中行中心填充是饱和红
    (堵闪光/白幕把红填充抬进亮度窗)。否决帧调用方应抑制发键并把记账行记为死行。
    red_press=True 仅用于 A/B 复现 v1.6~v1.6.5 旧语义 (红行当 prompt), 默认关闭。"""
    if gray_img is None:
        if screen_img is None:
            if region is None:
                vx, vy, vw, vh = virtual_screen()
                region = (vx + 0.15 * vw, vy + 0.22 * vh, 0.70 * vw, 0.74 * vh)
            gray_img = grab(region, gray=True)
        else:
            gray_img = cv2.cvtColor(screen_img, cv2.COLOR_BGR2GRAY)
    gray = gray_img
    # inRange 单趟出 0/255 掩膜: 比 "(g>=a)&(g<=b)" 的 bool 中间图再 astype 快 ~5x
    # (3MP 上 4.4ms -> 0.8ms); 连通域只关心"非零", 两种写法结果完全等价。
    fill_mask = cv2.inRange(gray, QTE_FILL_GRAY[0], QTE_FILL_GRAY[1])
    bright_bg = _qte_bright_bg(fill_mask)   # v1.6.7: 亮底帧改用相对描边环检
    cands = _qte_cands(gray, fill_mask, rel=bright_bg)
    src = "gray"
    red_n, red_c = 0, None
    have_color = (red and color_img is not None
                  and color_img.shape[:2] == gray.shape[:2])
    if have_color and _red_present(color_img):
        # 红填充箭在灰度图上与它自己的暗描边同灰度, 颜色通道是唯一入口。
        # 先过存在性闸 (stride-4 抽样 ~0.2ms), 有红才跑**全分辨率**红色连通域
        # (~5ms/0.7MP)。全分辨率是为了让红通道和灰度通道共享 QTE_SIZE 的
        # >=10px 下限: 早先的半分辨率探针会把下限抬到 20px, 小窗/低分辨率下
        # 整行红箭直接抓空 (实测样张 0.6x/0.53x 缩放全丢)。
        # v1.6.6: 这里认出的红箭**只用于否决**, 不再进按键管线。
        red_c = _qte_cands(gray, _red_mask(color_img))
        red_n = len(red_c)
    # v1.6.7: 把原来的三合一否决拆成两段 —— 形状闸 (>=2 支红箭) 照旧最早退;
    # 孤红闸 (红 1 支 + 抓空) 挪到**自适应通道之后**再判。原来的写法在自适应
    # 之前就 return, 而亮底帧灰度通道本来就常常抓空, 画面上一点暖色凑出 1 支
    # "红箭形" 就把整帧判成失败反馈 -> 行被白白丢掉、循环里反复"按一键就红"。
    if stats is not None:
        stats["red_n"] = red_n
        stats["red_row"] = False
    if have_color and red_n >= QTE_RED_VETO_MIN and not red_press:
        # 红行 = 失败反馈: 本帧任何通道都不出可按键候选, 红像素永不做候选。
        if stats is not None:
            stats["red_row"], stats["src"], stats["min_margin"] = True, None, 1.0
        if debug:
            return ([], [{"x": c[0], "y": c[1], "w": c[2], "h": c[3], "dir": c[4],
                          "margin": c[5]} for c in (red_c or [])])
        return []
    if red_press and red_n >= min_arrows and len(cands) < min_arrows:
        cands, src = red_c, "red"      # 仅 A/B 旧语义
    if adapt and len(cands) < min_arrows:
        # 自适应亮度通道 (v1.6.5): 大区先降采样, 候选坐标再还原回原图尺度
        g2, sc = gray, 1.0
        if gray.size > QTE_ADAPT_MAX_PX:
            sc = (QTE_ADAPT_MAX_PX / float(gray.size)) ** 0.5
            g2 = cv2.resize(gray, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        size = None if sc >= 1.0 else (max(6, int(round(QTE_SIZE[0] * sc))),
                                       max(12, int(round(QTE_SIZE[1] * sc))))
        c3, mname = _qte_adapt_cands(g2, min_arrows, adapt_methods, size)
        if c3 is not None:
            if sc < 1.0:
                for c in c3:
                    c[0] /= sc
                    c[1] /= sc
                    c[2] = int(round(c[2] / sc))
                    c[3] = int(round(c[3] / sc))
            cands, src = c3, "adapt:" + mname
    if have_color and red_n >= 1 and len(cands) < min_arrows and not red_press:
        # v1.6.7 孤红闸: 红 1 支 + **所有通道**都抓空才算死行 (以前只看灰度通道,
        # 自适应还没跑就误杀)。亮底/暖色画面上这条不再抢在自适应通道前面否决。
        if stats is not None:
            stats["red_row"], stats["src"], stats["min_margin"] = True, None, 1.0
        if debug:
            return ([], [{"x": c[0], "y": c[1], "w": c[2], "h": c[3], "dir": c[4],
                          "margin": c[5]} for c in (red_c or [])])
        return []
    if len(cands) < min_arrows:
        if debug:
            return ([], [{"x": c[0], "y": c[1], "w": c[2], "h": c[3], "dir": c[4],
                            "margin": c[5]} for c in cands])
        return []
    cands.sort(key=lambda c: c[0])
    # 同排聚类: 中心 y 接近 + 高度接近 + 水平间距合理; 取 >=2 箭的最大簇
    best = []
    for i in range(len(cands)):
        grp = [cands[i]]
        for j in range(i + 1, len(cands)):
            c, p = cands[j], grp[-1]
            if abs(c[1] - p[1]) > 0.6 * min(c[3], p[3]):
                continue
            if not (0.75 <= c[3] / float(p[3]) <= 1.33):
                continue
            gap = (c[0] - c[2] / 2.0) - (p[0] + p[2] / 2.0)
            if not (-0.15 * p[2] <= gap <= 2.5 * p[2]):
                continue
            # 行规整度: 真 QTE 行间距近似等差、箭高近似相等; 桌面/游戏 UI 里
            # 偶然凑对的亮块间距随机, 用首间距+首箭高卡掉, 降误报。
            if len(grp) >= 2:
                g0 = (grp[1][0] - grp[1][2] / 2.0) - (grp[0][0] + grp[0][2] / 2.0)
                if g0 > 0 and not (0.6 * g0 <= gap <= 1.5 * g0):
                    continue
                # 箭高随朝向变 (横箭矮/竖箭高), 用长边 max(w,h) 比才稳定
                s0 = max(grp[0][2], grp[0][3])
                if not (0.85 * s0 <= max(c[2], c[3]) <= 1.18 * s0):
                    continue
            grp.append(c)
        if len(grp) >= min_arrows and len(grp) > len(best):
            best = grp
    if stats is not None:
        stats["src"] = src if best else None
        stats["min_margin"] = min((c[5] for c in best), default=1.0)
        # v1.6.7 左裁边护栏: 紧凑轮询窗把行的最左一支裁掉时, 识别出来的就是真值的
        # 后缀 -> 工具把第二支当首键发出去, 游戏判错整行变红。标出来让调用方
        # **不要用这一帧开新行**, 并立即退回全扫。
        # 判据是"最左箭离裁图边界够不够远", 不是"候选有没有贴边" ——
        # 被裁掉一半的箭过不了形状门, 连候选都不会出现, 光看候选贴边必然漏判。
        if best:
            _aw = float(np.median([max(c[2], c[3]) for c in best]))
            _lmin = min(c[0] - c[2] / 2.0 for c in best)
            stats["left_clip"] = _lmin <= QTE_EDGE_PAD + QTE_EDGE_CLIP_W * _aw
        else:
            stats["left_clip"] = False
    if best and have_color and not red_press and _row_fill_red(color_img, best):
        # 填充色否决: 闪光/白幕把红填充抬进亮度窗时, 灰度/自适应通道会把红箭当白箭
        # 读出来 (红掩膜因 G/B 同抬而漏检) —— 选中行中心还是饱和红就整帧否决。
        if stats is not None:
            stats["red_row"], stats["src"] = True, None
            stats["min_margin"] = 1.0
        if debug:
            return ([], [{"x": c[0], "y": c[1], "w": c[2], "h": c[3], "dir": c[4],
                          "margin": c[5]} for c in best])
        return []
    if debug:
        # 诊断用: (选中行, 全部通过单箭检验的候选) —— 候选含 x,y,w,h,dir
        return ([(c[0], c[4]) for c in best],
                [{"x": c[0], "y": c[1], "w": c[2], "h": c[3], "dir": c[4],
                  "margin": c[5]} for c in cands])
    return [(c[0], c[4]) for c in best]

def qte_track_region(base, boxes, left_pad_h=8, right_pad_h=14, vert_pad_h=3):
    """由本轮识别到的箭 bbox 推出下一轮的"紧凑轮询窗"(屏幕绝对坐标), 推不出返回 None。

    base : 本轮实际截屏区 (x, y, w, h) 绝对坐标
    boxes: find_qte_arrows(debug=True) 的候选列表 (坐标是 base 内的相对坐标)

    为什么要两级截屏区: 整条 QTE 横带在 5120x1440 主屏上是 3891x792 ≈ 3MP,
    抓一次 ~28ms + 灰度/掩膜/连通域 ~13ms ≈ 41ms; 而真正有箭的那一小块
    (~700x300 ≈ 0.2MP) 抓+识别只要 ~8ms。闭环的按键间隔地板 = "看到游戏吃掉
    那支箭"的轮询周期, 所以锁定箭行后只在它周围的小窗里抓图, 单键开销直接砍掉
    四分之三。右侧留 14 倍箭高的余量: 箭行是逐支往右弹出的, 不能把后面还没
    出现的箭裁出去 (裁掉就会重现"最后一支按不出来")。"""
    if not boxes:
        return None
    bx, by, bw, bh = (float(v) for v in base)
    hh = float(np.median([max(b["w"], b["h"]) for b in boxes]))
    if not (4.0 <= hh <= 400.0):
        return None
    x0 = min(b["x"] - b["w"] / 2.0 for b in boxes)
    x1 = max(b["x"] + b["w"] / 2.0 for b in boxes)
    y0 = min(b["y"] - b["h"] / 2.0 for b in boxes)
    y1 = max(b["y"] + b["h"] / 2.0 for b in boxes)
    lx, rx = x0 - left_pad_h * hh, x1 + right_pad_h * hh
    ty, by2 = y0 - vert_pad_h * hh, y1 + vert_pad_h * hh
    # 夹回 base 内: 紧凑窗是 base 的子集, 坐标系才和上一轮一致
    lx, rx = max(0.0, lx), min(bw, rx)
    ty, by2 = max(0.0, ty), min(bh, by2)
    if rx - lx < 8 * hh or by2 - ty < 4 * hh:
        return None                      # 贴着 base 边被裁残了 -> 继续用全区
    return (int(bx + lx), int(by + ty), int(rx - lx), int(by2 - ty))

# ---------------------------------------------------------------- 弹道/射表
_TABLES_CACHE = {"key": None, "data": None}
_TABLES_LOCK = threading.Lock()

def load_tables():
    """射表 JSON, 带 (mtime, size) 缓存。实时跟踪时每秒要解算 ~2 次,
    每次都读盘+解析 15KB JSON 纯属浪费; 标定写表后调 invalidate_tables_cache()
    立即失效。注意: 返回的是共享对象, 要改它必须先 copy.deepcopy()。"""
    try:
        st = os.stat(TABLES_PATH)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _TABLES_LOCK:
        if key is not None and key == _TABLES_CACHE["key"] and _TABLES_CACHE["data"] is not None:
            return _TABLES_CACHE["data"]
        with open(TABLES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _TABLES_CACHE["key"], _TABLES_CACHE["data"] = key, data
        return data

def invalidate_tables_cache():
    with _TABLES_LOCK:
        _TABLES_CACHE["key"] = None
        _TABLES_CACHE["data"] = None

def _merge_curve(w, key):
    """社区射表(基准, 升序 [[d, mil], ...]) 叠加用户实测样本(±1 m 内覆盖)"""
    pts = [[float(p[0]), float(p[1])] for p in ((w.get("firing_tables") or {}).get(key) or [])]
    for t in (w.get("table") or []):
        d, m = float(t["range_m"]), float(t["mil"])
        pts = [p for p in pts if abs(p[0] - d) > 1.0]
        pts.append([d, m])
    pts.sort(key=lambda p: p[0])
    return pts

def _interp_curve(pts, d, clamp=False):
    """分段线性插值; 点数不足返回 None。clamp=True 时域外钳制到端点, 否则域外返回 None"""
    if len(pts) < 2:
        return None
    if not (pts[0][0] <= d <= pts[-1][0]):
        if not clamp:
            return None
        d = min(max(d, pts[0][0]), pts[-1][0])
    return float(np.interp(d, [p[0] for p in pts], [p[1] for p in pts]))

def solve_shot(tables, weapon, dist_m):
    w = tables["weapons"][weapon]
    rmin, rmax = w["range_m"]
    cal = w.get("calibration", {}) or {}
    off = float(cal.get("mil_offset", 0.0) or 0.0)
    dscale = float(cal.get("distance_scale", 1.0) or 1.0)
    doff = float(cal.get("distance_offset_m", 0.0) or 0.0)
    d = dist_m * dscale + doff   # 标定后真实距离 (2026-09-15 用户实测: 地图距离+70m)
    RANGE_EPS = 0.5   # m; 684.0000001 之类的浮点尾差仍视为"恰好最远射程"
    ok = (rmin - RANGE_EPS) <= d <= (rmax + RANGE_EPS)
    res = {"weapon": weapon, "dist_m": float(dist_m), "dist_eff_m": float(d),
           "dist_offset_m": doff, "dist_scale": dscale,
           "in_range": ok, "rmin": float(rmin), "rmax": float(rmax)}
    # 越界方向说明: 先换算 L81 最远 684 m, 再判为"太远"; 否则判为"太近"
    if not ok and d > rmax:
        res["range_state"] = "far"
        res["range_note"] = "!! 超出最远射程 %.0fm (最远 %.0fm)" % (d - rmax, rmax)
    elif not ok and d < rmin:
        res["range_state"] = "near"
        res["range_note"] = "!! 低于最近射程 %.0fm (最近 %.0fm)" % (rmin - d, rmin)
    else:
        res["range_state"] = "ok"
        res["range_note"] = ("在射程内 (%.0f~%.0fm)" % (rmin, rmax)) + \
                            ("  [恰好最远射程]" if abs(d - rmax) <= RANGE_EPS else "")
    if doff or dscale != 1.0:
        tag = ("距离修正 %.0f→%.0fm " % (d - doff, d)) if dscale == 1.0 else \
              ("距离修正 %.0fx%g%+g→%.0fm " % ((d - doff) / dscale, dscale, doff, d))
        res["range_note"] = tag + res["range_note"]
    # 归一化射程占用比 0~1, 供 HUD/进度条使用
    res["range_pct"] = 0.0 if rmax <= rmin else max(0.0, min(1.0, (d - rmin) / float(rmax - rmin)))
    n_user = len(w.get("table") or [])
    ft = w.get("firing_tables") or {}
    if w.get("dual_trajectory"):
        low, high = _merge_curve(w, "low"), _merge_curve(w, "high")
        mil_low = _interp_curve(low, d)                  # 低弧够不到 = None
        mil_high = _interp_curve(high, d, clamp=True)    # 高弧域外钳制到端点
        if mil_low is None and mil_high is None:  # 无社区表 -> 端点线性回退
            fb = float(np.interp(d, [rmin, rmax], [w["mil_range"][1], w["mil_range"][0]]))
            mil_low = mil_high = fb
            res["mil_src"] = "端点插值"
        else:
            res["mil_src"] = ("社区射表 低%d/高%d点" % (len(low), len(high))) + \
                             ("+实测%d点" % n_user if n_user else "")
        if d < w.get("high_arc_only_below_m", 0) or mil_low is None:
            mil = mil_high if mil_high is not None else mil_low
        else:
            mil = mil_low
        res["mil_low"] = None if mil_low is None else mil_low + off
        res["mil_high"] = None if mil_high is None else mil_high + off
        if mil_low is not None and mil_high is not None:
            res["arc"] = "双弹道"
            res["mil_txt"] = "低%.0f/高%.0f mil" % (mil_low + off, mil_high + off)
        else:
            res["arc"] = "高弧-only"
            res["mil_txt"] = "%.0f mil 高弧" % (mil + off)
    else:
        cur = _merge_curve(w, "single")
        clamped = bool(cur) and not (cur[0][0] <= d <= cur[-1][0])
        mil = _interp_curve(cur, d, clamp=True)
        if mil is None:  # 无社区表 -> 端点线性回退
            mil = float(np.interp(d, [rmin, rmax], [w["mil_range"][1], w["mil_range"][0]]))
            res["mil_src"] = "端点插值"
            res["arc"] = "单弹道(端点插值)"
        else:
            res["mil_src"] = "社区射表%d点" % len(cur) + ("+实测%d点" % n_user if n_user else "") \
                             + ("(域外钳制)" if clamped else "")
            res["arc"] = "单弹道"
        res["mil_low"] = res["mil_high"] = None
        res["mil_txt"] = "%.0f mil" % (mil + off)
    res["mil"] = mil + off
    res["reload_s"] = w["reload_s"]
    res["splash_m"] = w.get("splash_m")
    return res

def azimuth_deg(gx, gy, tx, ty):
    """0°=北(+y), 顺时针; 地图 x=东"""
    return math.degrees(math.atan2(tx - gx, ty - gy)) % 360.0

# ---------------------------------------------------------------- 配置
# v1.6.4 起配置版本迁移 (v1.6.5 把 qte_adapt_probe_ms 也纳入)。
# 背景: config.json 是**持久化**的, 而 QTE 的时序/护栏参数在 v1.6.1~v1.6.4 里被
# 大幅重调 (撤掉 v1.6.0 那批只图快的改动, 加读花护栏 / 卡死破局 / 残行救回)。
# 旧 exe 退出时会把内存里的旧参数写回盘, 新 exe 一启动又被这份旧文件覆盖 ->
# 用户"升级了却没变好"。所以给配置打版本号 CFG_VER: 盘上版本落后时, 把
# MIGRATE_KEYS (全部 QTE 调参键) 拉回 DEFAULT_CONFIG 的新基线;
# **用户个性化键一律不动** (功能开关 / 热区 / HUD 位置 / 武器 / CPU 预算 …)。
CFG_VER = 166

MIGRATE_KEYS = (
    "qte_gap_ms", "qte_hold_ms", "qte_poll_ms", "qte_fast_ms",
    "qte_sweep_ms", "qte_sweep_relax_ms", "qte_stall_ms", "qte_stall_max",
    "qte_retry_ms", "qte_retry_max", "qte_row_stable_ms", "qte_press_margin",
    "qte_min_gap_ms", "qte_red_probe_ms", "qte_red_poll_ms",
    "qte_press_stable_ms", "qte_fg_settle_ms", "qte_row_switch_ms",
    "qte_slice_guard", "qte_row_max", "qte_grow_ttl_ms", "qte_grow_confirm",
    "qte_slice_press_last", "qte_grow_adopt_done", "qte_grow_done_ms",
    "qte_trim_ms", "qte_press_stable_dirty_ms", "qte_row_stable_dirty_ms",
    "qte_conflict_ms", "qte_stuck_ms", "qte_stuck_max", "qte_ghost_ms",
    "qte_idle_sweep_ms", "qte_zone_pad", "qte_adapt_probe_ms",
    "qte_red_hold_ms",
)

LAST_MIGRATION = []      # 最近一次迁移的改动 [(key, 旧值, 新值), ...]; 供日志/测试读
LAST_BACKUP = ""         # 迁移改值前给盘上旧配置留的备份路径 (没改值/没备份则空)


def migrate_config(cfg, data):
    """盘上配置(data)版本落后时, 把调参键拉回 DEFAULT_CONFIG 基线; 返回变更列表。

    只在 data 里**确实写过**且与新基线不同的键上动手 —— 没写过的键本来就已经是
    DEFAULT_CONFIG 的值。data 为空 (文件不存在 / JSON 损坏) 时不迁移, 免得把
    用户盘上的东西误判成"旧版"。"""
    changed = []
    try:
        fver = int(data.get("cfg_ver", 0))
    except Exception:
        fver = 0
    if data and fver < CFG_VER:
        for k in MIGRATE_KEYS:
            if k not in DEFAULT_CONFIG or k not in data:
                continue
            new = DEFAULT_CONFIG[k]
            if data[k] != new:
                changed.append((k, data[k], new))
                cfg[k] = new
    cfg["cfg_ver"] = CFG_VER
    return changed


def _backup_config(fver):
    """迁移**真要改值**之前, 给盘上旧配置留一份 .bak_v<旧版本>_<时间戳>。

    exe 发给别人用时这条最值钱: 万一对方手调过的参数被拉回新基线, 原件还在旁边可以
    对着抄回来。原样字节复制 (不解析、不重排键); 只读目录/文件被占用等异常一律吞掉,
    备份失败绝不阻断启动。只留这一次, 不删历史备份也不主动堆积。"""
    global LAST_BACKUP
    LAST_BACKUP = ""
    try:
        if not os.path.exists(CONFIG_PATH):
            return ""
        p = "%s.bak_v%s_%s" % (CONFIG_PATH, fver, time.strftime("%Y%m%d_%H%M%S"))
        with open(CONFIG_PATH, "rb") as fi:
            blob = fi.read()
        with open(p, "wb") as fo:
            fo.write(blob)
        LAST_BACKUP = p
        return p
    except Exception:
        return ""


def load_config():
    global LAST_MIGRATION
    cfg = dict(DEFAULT_CONFIG)
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}                      # 损坏的配置当"没有配置": 用内置基线
    if not isinstance(data, dict):
        data = {}
    cfg.update(data)
    for k in LEGACY_KEYS:
        cfg.pop(k, None)
    LAST_MIGRATION = migrate_config(cfg, data)
    # v1.6.6: 盘上版本号落后就落盘, **哪怕这次没有任何调参键需要改**。
    # 旧写法只在 LAST_MIGRATION 非空时写盘, 而相邻版本的基线值往往一个都没变
    # (164->166 就是如此), 于是 cfg_ver 永远停在旧值、每次启动重算, 本版新增的键
    # (qte_red_hold_ms) 要等到拖 HUD / 学窄条带之类偶然触发 save_config 才落盘 ——
    # 用户打开 config.json 根本看不到新键, 也就没法手调。
    # data 为空 (文件不存在 / JSON 损坏) 时仍然一律不写: 不凭空造文件, 也不把
    # 损坏文件覆盖成默认值 (M8/M9 断言)。
    try:
        stale = int(data.get("cfg_ver", 0)) != CFG_VER
    except Exception:
        stale = True
    if data and (LAST_MIGRATION or stale):
        try:
            if LAST_MIGRATION:
                _backup_config(data.get("cfg_ver", 0))
            save_config(cfg)               # 落盘: 免得每次启动都重算, 也让旧 exe
        except Exception:                  #      退出写回时至少带着新版本号
            pass
    return cfg

_CFG_LOCK = threading.Lock()

def save_config(cfg):
    """原子写配置。

    v1.4 起 QTE 轮询线程会把学到的持久窄条带写回 config, 与主线程 (HUD 拖拽 /
    保存设置) 并发。原来直接 open(...,"w") 覆盖写有两处隐患: 一方写到一半另一方
    再 open 会截断出半截 JSON (下次启动 load_config 直接抛异常 -> 配置全丢);
    json.dump 遍历 dict 时另一线程插入新键会抛 "dictionary changed size during
    iteration"。加锁 + 临时文件 + os.replace 原子替换, 两个问题一起解决。"""
    with _CFG_LOCK:
        snap = dict(cfg)                       # 快照: 遍历期间不受并发改动影响
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def configure_cv2_threads(cfg=None):
    """限制 OpenCV 的内部并行度 (见 DEFAULT_CONFIG["cv2_threads"] 的实测依据)。

    必须在第一次调用 cv2 重活之前生效; 之后改也可, 立即对后续调用起作用。
    返回实际设成的线程数 (0 表示沿用 OpenCV 默认)。"""
    try:
        n = int((cfg or load_config()).get("cv2_threads", 4))
    except Exception:
        n = 4
    if n > 0:
        try:
            cv2.setNumThreads(n)
        except Exception:
            pass
    try:
        return int(cv2.getNumThreads())
    except Exception:
        return n

# ---------------------------------------------------------------- selftest
def selftest(img_map):
    """离线自检: 地图截图上以固定锚点读悬停标签, 再用演示炮位走一遍解算链路。"""
    cfg = load_config()
    im1 = cv2.imread(img_map)
    if im1 is None:
        print("无法读取地图截图: %s" % img_map)
        return 2
    print("== 鼠标取点: 悬停标签 (地图截图, 固定锚点) ==")
    tgt = read_hover_target(cfg, screen_img=im1, anchor=(320, 314))
    print("   悬停标签目标坐标:", tgt)
    print("== 射距解算示例 (炮位=演示值, 目标=悬停读数或演示值) ==")
    g = (71.43, 101.79)
    t = tgt or (71.28, 102.39)
    d = math.hypot(t[0] - g[0], t[1] - g[1]) * 100.0
    az = azimuth_deg(g[0], g[1], t[0], t[1])
    shot = solve_shot(load_tables(), cfg["weapon"], d)
    print("   炮位 x%.2f y%.2f -> 目标 x%.2f y%.2f" % (g[0], g[1], t[0], t[1]))
    print("   距离 %.0f m | 方位 %.1f° | 仰角 %s | %s | 装填 %ss | %s"
          % (d, az, shot["mil_txt"], shot["arc"], shot["reload_s"], shot.get("mil_src", "")))
    print("   射程窗口 %.0f~%.0f m -> %s" % (shot["rmin"], shot["rmax"], shot["range_note"]))
    return 0

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "selftest"
    if mode == "selftest":
        a = sys.argv[2] if len(sys.argv) > 2 else os.path.join(SCRIPT_DIR, "samples", "sample_map.png")
        sys.exit(selftest(a))
    else:
        print(__doc__)



