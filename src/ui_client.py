# -*- coding: utf-8 -*-
"""控制端界面：IP/端口/密码连接、远程画面显示、鼠标键盘转发、剪贴板与文件传输。"""
import io
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

import common
from desk_client import DeskClient
from native import clip_set_text

# tkinter keysym -> Windows 虚拟键码
KEYSYM_VK = {
    'Return': 0x0D, 'KP_Enter': 0x0D, 'space': 0x20, 'BackSpace': 0x08,
    'Tab': 0x09, 'Escape': 0x1B, 'Delete': 0x2E, 'Insert': 0x2D,
    'Home': 0x24, 'End': 0x23, 'Page_Up': 0x21, 'Page_Down': 0x22,
    'Up': 0x26, 'Down': 0x28, 'Left': 0x25, 'Right': 0x27,
    'Caps_Lock': 0x14, 'Shift_L': 0xA0, 'Shift_R': 0xA1,
    'Control_L': 0xA2, 'Control_R': 0xA3, 'Alt_L': 0xA4, 'Alt_R': 0xA5,
    'Win_L': 0x5B, 'Win_R': 0x5C, 'Menu': 0x5D,
    'Num_Lock': 0x90, 'Scroll_Lock': 0x91, 'Pause': 0x13, 'Print': 0x2C,
    '-': 0xBD, '=': 0xBB, '[': 0xDB, ']': 0xDD, ';': 0xBA, "'": 0xDE,
    '`': 0xC0, '\\': 0xDC, ',': 0xBC, '.': 0xBE, '/': 0xBF,
    'KP_0': 0x60, 'KP_1': 0x61, 'KP_2': 0x62, 'KP_3': 0x63, 'KP_4': 0x64,
    'KP_5': 0x65, 'KP_6': 0x66, 'KP_7': 0x67, 'KP_8': 0x68, 'KP_9': 0x69,
    'KP_Add': 0x6B, 'KP_Subtract': 0x6D, 'KP_Multiply': 0x6A,
    'KP_Divide': 0x6F, 'KP_Decimal': 0x6E,
}
for _i in range(1, 25):
    KEYSYM_VK['F%d' % _i] = 0x70 + _i - 1


def keysym_to_vk(keysym):
    if keysym in KEYSYM_VK:
        return KEYSYM_VK[keysym]
    if len(keysym) == 1:
        ch = keysym
        if 'a' <= ch <= 'z':
            return ord(ch.upper())
        if '0' <= ch <= '9':
            return ord(ch)
        return ord(ch.upper())
    return None


class ClientWindow(object):
    def __init__(self, root):
        self.root = root
        root.title('控制端 - LanRemoteDesk 局域网远程桌面')
        root.geometry('1080x760')
        root.minsize(720, 480)

        self.client = None
        self.frame_info = None
        self.last_image = None    # 最近一帧 PIL Image
        self.photo = None         # PhotoImage 引用
        self._mouse_last = 0
        self._disp_w = None       # 画面实际显示尺寸（等比缩放，可能小于画布）
        self._disp_h = None

        self._build_ui()
        root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, padx=8, pady=6)

        ttk.Label(top, text='被控端 IP：').pack(side=tk.LEFT)
        self.ip_var = tk.StringVar(value='127.0.0.1')
        ttk.Entry(top, textvariable=self.ip_var, width=15).pack(side=tk.LEFT)

        ttk.Label(top, text='端口：').pack(side=tk.LEFT, padx=(8, 0))
        self.port_var = tk.StringVar(value=str(common.DEFAULT_PORT))
        ttk.Entry(top, textvariable=self.port_var, width=6).pack(side=tk.LEFT)

        ttk.Label(top, text='密码：').pack(side=tk.LEFT, padx=(8, 0))
        self.pwd_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.pwd_var, width=10, show='*').pack(side=tk.LEFT)

        self.btn_conn = ttk.Button(top, text='连接', command=self._connect)
        self.btn_conn.pack(side=tk.LEFT, padx=(12, 0))
        self.btn_dis = ttk.Button(top, text='断开', command=self._disconnect, state=tk.DISABLED)
        self.btn_dis.pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value='未连接')
        ttk.Label(top, textvariable=self.status_var, foreground='#00695c',
                  font=('Microsoft YaHei UI', 9, 'bold')).pack(side=tk.LEFT, padx=10)

        # 画面区
        self.canvas = tk.Canvas(self.root, bg='#202020', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        self.canvas.bind('<Configure>', lambda e: self._redraw())
        self.canvas.bind('<Motion>', self._on_mouse_move)
        self.canvas.bind('<ButtonPress-1>', lambda e: self._on_mouse_btn(e, 1, 'down'))
        self.canvas.bind('<ButtonRelease-1>', lambda e: self._on_mouse_btn(e, 1, 'up'))
        self.canvas.bind('<ButtonPress-3>', lambda e: self._on_mouse_btn(e, 2, 'down'))
        self.canvas.bind('<ButtonRelease-3>', lambda e: self._on_mouse_btn(e, 2, 'up'))
        self.canvas.bind('<ButtonPress-2>', lambda e: self._on_mouse_btn(e, 3, 'down'))
        self.canvas.bind('<ButtonRelease-2>', lambda e: self._on_mouse_btn(e, 3, 'up'))
        self.canvas.bind('<MouseWheel>', self._on_mouse_wheel)
        self.canvas.bind('<KeyPress>', self._on_key_press)
        self.canvas.bind('<KeyRelease>', self._on_key_release)
        # 注意：不要再用 <Button-1> 绑定其他逻辑——它是 <ButtonPress-1> 的别名，
        # 后绑定会覆盖上面的按下转发，导致左键点击/拖动失效（focus 已并入 _on_mouse_btn）

        # 工具条
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Button(bar, text='发送文件到被控端…', command=self._send_file).pack(side=tk.LEFT)
        ttk.Button(bar, text='保存当前画面', command=self._save_snapshot).pack(side=tk.LEFT, padx=6)

        self.clip_var = tk.StringVar(value='剪贴板：-')
        ttk.Label(bar, textvariable=self.clip_var, foreground='#555555').pack(side=tk.RIGHT)

        self.progress_var = tk.StringVar(value='')
        ttk.Label(bar, textvariable=self.progress_var, foreground='#0b5394').pack(side=tk.RIGHT, padx=8)

        # 日志
        self.log = tk.Text(self.root, height=7, state=tk.DISABLED, wrap=tk.WORD,
                           font=('Consolas', 9))
        self.log.pack(fill=tk.BOTH, padx=8, pady=(0, 6))

    # ---------------- 连接 ----------------
    def _connect(self):
        if self.client and self.client.is_connected():
            return
        ip = self.ip_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror('参数错误', '端口必须是数字')
            return
        pwd = self.pwd_var.get()
        if not ip or not pwd:
            messagebox.showwarning('提示', '请填写被控端 IP 和密码')
            return
        self.status_var.set('正在连接…')
        self.client = DeskClient(
            ip, port, pwd,
            save_dir=common.received_dir(),
            on_connected=lambda: self._ui(self._on_connected),
            on_disconnected=lambda reason: self._ui(self._on_disconnected, reason),
            on_frame=lambda info, jpeg: self._ui(self._on_frame, info, jpeg),
            on_log=lambda t: self._ui(self._log, t),
            on_clip=lambda t: self._ui(self._on_clip, t),
            on_file_progress=lambda n, d, s, t: self._ui(self._on_progress, n, d, s, t),
            on_file_done=lambda n, ok, m: self._ui(self._on_file_done, n, ok, m),
        )
        # 后台线程连接，避免卡界面
        import threading
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            ok = self.client.connect()
            if not ok:
                self._ui(self._log, '连接失败')
        except Exception as e:
            self._ui(self._log, '连接失败：%s' % e)
            self._ui(self._set_status, '连接失败')

    def _disconnect(self):
        if self.client:
            self.client.disconnect()
        self._set_status('未连接')
        self.btn_conn.config(state=tk.NORMAL)
        self.btn_dis.config(state=tk.DISABLED)
        self.canvas.delete('all')
        self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                text='已断开', fill='#666666', font=('Microsoft YaHei UI', 20))
        self.last_image = None

    def _on_connected(self):
        self._set_status('已连接')
        self.btn_conn.config(state=tk.DISABLED)
        self.btn_dis.config(state=tk.NORMAL)
        self.canvas.focus_set()
        self._log('连接成功，正在接收画面…')

    def _on_disconnected(self, reason):
        self._set_status('已断开（%s）' % reason)
        self.btn_conn.config(state=tk.NORMAL)
        self.btn_dis.config(state=tk.DISABLED)

    # ---------------- 画面 ----------------
    def _on_frame(self, info, jpeg):
        self.history_info = info
        try:
            img = Image.open(io.BytesIO(jpeg))
            img.load()
            self.last_image = img
            self._redraw()
        except Exception as e:
            self._log('画面解码失败：%s' % e)

    def _redraw(self):
        if not self.last_image:
            return
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        img = self.last_image.copy()
        img.thumbnail((cw, ch), Image.LANCZOS)
        # 记录实际显示尺寸（等比缩放后可能小于画布，居中留黑边）
        self._disp_w = img.width
        self._disp_h = img.height
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete('all')
        self.canvas.create_image(cw // 2, ch // 2, image=self.photo, anchor=tk.CENTER)

    # ---------------- 鼠标 ----------------
    @staticmethod
    def _map_local_to_virtual(x, y, cw, ch, disp_w, disp_h, vw, vh, ox, oy):
        """画布坐标 -> 远端虚拟屏幕坐标。
        画面为等比缩放后居中显示（可能有黑边），因此必须按实际显示矩形换算，
        不能用整个画布换算，否则点击会偏移。对黑边区做钳制。"""
        sw = disp_w or cw
        sh = disp_h or ch
        img_left = (cw - sw) / 2.0
        img_top = (ch - sh) / 2.0
        local_x = min(1.0, max(0.0, (x - img_left) / sw))
        local_y = min(1.0, max(0.0, (y - img_top) / sh))
        return ox + local_x * vw, oy + local_y * vh

    def _remap(self, x, y):
        """canvas 坐标 -> 远端虚拟屏幕坐标。"""
        info = getattr(self, 'history_info', None) or {}
        return ClientWindow._map_local_to_virtual(
            x, y,
            max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()),
            getattr(self, '_disp_w', None), getattr(self, '_disp_h', None),
            info.get('virt_w', 1), info.get('virt_h', 1),
            info.get('offset_x', 0), info.get('offset_y', 0))

    def _on_mouse_move(self, e):
        if not (self.client and self.client.is_connected()):
            return
        import time
        now = time.time()
        if now - self._mouse_last < 0.03:
            return
        self._mouse_last = now
        rx, ry = self._remap(e.x, e.y)
        self.client.send_mouse(rx, ry, 'move', 0)

    def _on_mouse_btn(self, e, btn, action):
        if not (self.client and self.client.is_connected()):
            return
        if action == 'down':
            # 按下时顺手把键盘焦点给画面，替代被移除的 <Button-1> 绑定
            self.canvas.focus_set()
        rx, ry = self._remap(e.x, e.y)
        self.client.send_mouse(rx, ry, action, btn)

    def _on_mouse_wheel(self, e):
        if not (self.client and self.client.is_connected()):
            return
        delta = 120 if e.delta > 0 else -120
        self.client.send_mouse(0, 0, 'wheel', 0, delta)

    # ---------------- 键盘 ----------------
    def _on_key_press(self, e):
        if not (self.client and self.client.is_connected()):
            return
        vk = keysym_to_vk(e.keysym)
        if vk:
            self.client.send_key(vk, True)

    def _on_key_release(self, e):
        if not (self.client and self.client.is_connected()):
            return
        vk = keysym_to_vk(e.keysym)
        if vk:
            self.client.send_key(vk, False)

    # ---------------- 剪贴板 ----------------
    def _on_clip(self, text):
        self.clip_var.set('剪贴板：%s' % text[:60])
        try:
            clip_set_text(text)
        except Exception:
            pass

    # ---------------- 文件 ----------------
    def _send_file(self):
        if not (self.client and self.client.is_connected()):
            messagebox.showinfo('提示', '请先连接被控端')
            return
        path = filedialog.askopenfilename(title='选择要发送到被控端的文件')
        if path:
            self.client.send_file(path)

    def _save_snapshot(self):
        if not self.last_image:
            messagebox.showinfo('提示', '还没有画面可保存')
            return
        from tkinter import filedialog as fd
        path = fd.asksaveasfilename(title='保存画面', defaultextension='.png',
                                    initialfile='snapshot.png')
        if path:
            self.last_image.save(path)
            self._log('画面已保存：%s' % path)

    # ---------------- 回调 ----------------
    def _ui(self, fn, *args):
        try:
            self.root.after(0, lambda: fn(*args))
        except Exception:
            pass

    def _log(self, text):
        import datetime
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, '[%s] %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), text))
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _set_status(self, text):
        self.status_var.set(text)

    def _on_progress(self, name, direction, sent, total):
        pct = (sent * 100 // total) if total else 0
        self.progress_var.set('文件%s %s：%d%%' % ('发送' if direction == 'send' else '接收',
                                                    name, pct))

    def _on_file_done(self, name, ok, msg):
        self.progress_var.set('文件 %s：%s' % ('完成' if ok else '失败', msg))
        if not ok:
            self._log('文件传输失败：%s' % msg)

    def _on_close(self):
        try:
            if self.client:
                self.client.disconnect()
        except Exception:
            pass
        self.root.destroy()