# -*- coding: utf-8 -*-
"""
Win32 原生能力封装（ctypes 直调 user32.dll / kernel32.dll / shell32.dll，不依赖 pywin32）：
 - 虚拟屏幕坐标 / 光标
 - 鼠标注入（SetCursorPos + mouse_event）
 - 键盘注入（keybd_event）
 - 剪贴板 Unicode 文本读写（句柄函数需显式 restype，否则 64 位截断）
 - 管理员提权检测 / runas 自提权重启（UAC 弹窗由用户点击确认）
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_shell32 = ctypes.windll.shell32

# ---- 常量 ----
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002

_BTN_FLAGS = {
    1: (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    2: (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    3: (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


# ---- 显式函数签名（句柄必须按指针宽度声明，否则 64 位下被截断）----
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
_user32.GetCursorPos.restype = ctypes.c_int
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = ctypes.c_int
_user32.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p]
_user32.keybd_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_void_p]

_user32.OpenClipboard.argtypes = [ctypes.c_void_p]
_user32.OpenClipboard.restype = ctypes.c_int
_user32.CloseClipboard.restype = ctypes.c_int
_user32.EmptyClipboard.restype = ctypes.c_int
_user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
_user32.IsClipboardFormatAvailable.restype = ctypes.c_int
_user32.GetClipboardData.argtypes = [ctypes.c_uint]
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_user32.SetClipboardData.restype = ctypes.c_void_p

_kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.restype = ctypes.c_int
_kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
_kernel32.GlobalSize.restype = ctypes.c_size_t

_shell32.IsUserAnAdmin.restype = ctypes.c_int
_shell32.ShellExecuteW.restype = ctypes.c_void_p
_shell32.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                   ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]

SW_SHOWNORMAL = 1


def get_virtual_screen():
    """返回 (x, y, w, h) 虚拟屏幕（多显示器整体）。"""
    x = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


def get_primary_screen():
    return (0, 0,
            _user32.GetSystemMetrics(SM_CXSCREEN),
            _user32.GetSystemMetrics(SM_CYSCREEN))


def get_cursor_pos():
    p = wt.POINT()
    if _user32.GetCursorPos(ctypes.byref(p)):
        return p.x, p.y
    return 0, 0


def inject_mouse(x, y, action, button=1, delta=0):
    """action: move / down / up / wheel"""
    _user32.SetCursorPos(int(x), int(y))
    if action == 'move':
        _user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)
    elif action == 'wheel':
        _user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(delta), 0)
    elif action == 'down' or action == 'up':
        flags = _BTN_FLAGS.get(button, _BTN_FLAGS[1])
        _user32.mouse_event(flags[0] if action == 'down' else flags[1], 0, 0, 0, 0)


def inject_key(vk, down=False):
    _user32.keybd_event(int(vk), 0, 0 if down else KEYEVENTF_KEYUP, 0)


def clip_get_text():
    """读取剪贴板 Unicode 文本，失败返回 None。"""
    if not _user32.OpenClipboard(None):
        return None
    try:
        if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = _user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = _kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            size = int(_kernel32.GlobalSize(h))
            buf = ctypes.create_string_buffer(size)
            ctypes.memmove(buf, p, size)
            # 注意：必须用 raw 完整解码；buf.value 遇首个 NUL 即截断（UTF-16LE 每个字符后都有 0x00）
            return buf.raw.decode('utf-16-le', errors='replace').rstrip('\x00')
        finally:
            _kernel32.GlobalUnlock(h)
    finally:
        _user32.CloseClipboard()


def clip_set_text(text):
    """写入剪贴板 Unicode 文本；失败抛 OSError。"""
    data = text.encode('utf-16-le') + b'\x00\x00'
    if not _user32.OpenClipboard(None):
        raise OSError('打开剪贴板失败（可能被其他程序占用）')
    try:
        _user32.EmptyClipboard()
        h = _kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
        if not h:
            raise OSError('分配剪贴板内存失败')
        p = _kernel32.GlobalLock(h)
        if not p:
            raise OSError('锁定剪贴板内存失败')
        try:
            ctypes.memmove(p, data, len(data))
        finally:
            _kernel32.GlobalUnlock(h)
        if not _user32.SetClipboardData(CF_UNICODETEXT, h):
            raise OSError('SetClipboardData 失败')
    finally:
        _user32.CloseClipboard()


# ---------------- 管理员权限（UAC） ----------------

def is_user_elevated():
    """当前进程是否以管理员（提权）令牌运行。普通双击运行的 exe 返回 False。"""
    return bool(_shell32.IsUserAnAdmin())


def build_launch_args(script_args):
    """构造"以管理员重启"的 (exe, 参数) 组合。
    打包版直接使用 exe 自身；源码运行时使用 python + main.py。"""
    params = ' '.join(str(a) for a in script_args)
    if getattr(sys, 'frozen', False):
        return sys.executable, params
    import common
    script = os.path.join(common.app_base_dir(), 'main.py')
    return sys.executable, '"%s" %s' % (script, params)


def relaunch_elevated(script_args):
    """以管理员权限重新启动本程序，触发系统 UAC 弹窗（由用户点击"是/允许"）。
    返回 True：启动请求已发出（UAC 确认后新实例将运行）；
    返回 False：用户取消了 UAC 或启动失败（当前实例应继续运行）。"""
    exe, params = build_launch_args(script_args)
    ret = _shell32.ShellExecuteW(None, 'runas', exe, params, None, SW_SHOWNORMAL)
    return int(ret) > 32