# -*- coding: utf-8 -*-
"""被控端界面：显示本机 IP、随机数字密码、监听控制、文件发送、日志。"""
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import common
import native
from desk_server import DeskServer, generate_password, get_local_ips


class ServerWindow(object):
    def __init__(self, root, password=None, port=common.DEFAULT_PORT):
        self.root = root
        self.root.title('被控端 - LanRemoteDesk 局域网远程桌面')
        self.root.geometry('640x560')
        self.root.minsize(560, 480)

        self.password = password or generate_password()
        self._password = self.password
        self.server = None

        self._build_ui()
        self.port_var.set(str(port))
        self._refresh_ips()
        self._refresh_admin()
        self._start()

        root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = {'padx': 10, 'pady': 4}

        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, **pad)

        ttk.Label(top, text='本机 IP 地址：', font=('Microsoft YaHei UI', 10)).pack(side=tk.LEFT)
        self.ip_var = tk.StringVar(value='（正在获取…）')
        ttk.Label(top, textvariable=self.ip_var, foreground='#0b5394',
                  font=('Consolas', 11, 'bold')).pack(side=tk.LEFT)
        ttk.Button(top, text='刷新', width=6, command=self._refresh_ips).pack(side=tk.RIGHT)

        # 密码区
        pwd = ttk.LabelFrame(self.root, text=' 连接密码（随机生成，请告知控制端） ')
        pwd.pack(fill=tk.X, **pad)
        row = ttk.Frame(pwd)
        row.pack(fill=tk.X, padx=10, pady=8)
        self.pwd_var = tk.StringVar(value=self._password_text())
        ttk.Label(row, textvariable=self.pwd_var, font=('Consolas', 26, 'bold'),
                  foreground='#b00020').pack(side=tk.LEFT)
        ttk.Button(row, text='重新生成', command=self._regenerate).pack(side=tk.RIGHT)

        # ---- 监听区 ----
        lst = ttk.LabelFrame(self.root, text='监听设置')
        lst.pack(fill=tk.X, **pad)
        r1 = ttk.Frame(lst)
        r1.pack(fill=tk.X, padx=10, pady=4)
        ttk.Label(r1, text='端口：').pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(common.DEFAULT_PORT))
        ttk.Entry(r1, textvariable=self.port_var, width=8).pack(side=tk.LEFT)
        self.btn_start = ttk.Button(r1, text='开始监听', command=self._start)
        self.btn_start.pack(side=tk.LEFT, padx=(16, 0))
        self.btn_stop = ttk.Button(r1, text='停止监听', command=self._stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value='未监听')
        ttk.Label(lst, textvariable=self.status_var, foreground='#00695c',
                  font=('Microsoft YaHei UI', 9, 'bold')).pack(side=tk.LEFT, padx=10)

        r2 = ttk.Frame(lst)
        r2.pack(fill=tk.X, padx=10, pady=4)
        self.all_scr_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text='抓取全部显示器', variable=self.all_scr_var,
                        command=self._apply_opts).pack(side=tk.LEFT)
        ttk.Label(r2, text='画面质量：').pack(side=tk.LEFT, padx=(16, 0))
        self.quality_var = tk.IntVar(value=80)
        ttk.Scale(r2, from_=50, to=95, variable=self.quality_var, length=140,
                  command=lambda v: self._apply_opts()).pack(side=tk.LEFT)
        self.quality_lbl = ttk.Label(r2, text='80')
        self.quality_lbl.pack(side=tk.LEFT)
        ttk.Label(r2, text='帧率：').pack(side=tk.LEFT, padx=(16, 0))
        self.fps_var = tk.IntVar(value=12)
        ttk.Scale(r2, from_=3, to=30, variable=self.fps_var, length=140,
                  command=lambda v: self._apply_opts()).pack(side=tk.LEFT)
        self.fps_lbl = ttk.Label(r2, text='12')
        self.fps_lbl.pack(side=tk.LEFT)

        # 操作区
        op = ttk.Frame(self.root)
        op.pack(fill=tk.X, **pad)
        ttk.Button(op, text='发送文件给控制端…', command=self._send_file).pack(side=tk.LEFT)
        self.progress_var = tk.StringVar(value='')
        ttk.Label(op, textvariable=self.progress_var, foreground='#555555').pack(side=tk.LEFT, padx=8)

        # 权限区
        perm = ttk.LabelFrame(self.root, text='运行权限')
        perm.pack(fill=tk.X, **pad)
        r3 = ttk.Frame(perm)
        r3.pack(fill=tk.X, padx=10, pady=4)
        ttk.Button(r3, text='切换到管理员模式…', command=self._elevate).pack(side=tk.LEFT)
        self.admin_var = tk.StringVar(value='')
        ttk.Label(r3, textvariable=self.admin_var, foreground='#7f6000').pack(side=tk.LEFT, padx=10)

        # 日志
        logf = ttk.LabelFrame(self.root, text='运行日志')
        logf.pack(fill=tk.BOTH, expand=True, **pad)
        self.log = tk.Text(logf, height=10, state=tk.DISABLED, wrap=tk.WORD,
                           font=('Consolas', 9))
        self.log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    # ---------------- 动作 ----------------
    def _refresh_admin(self):
        if native.is_user_elevated():
            self.admin_var.set('当前已是管理员模式（可操控远端管理员程序）')
        else:
            self.admin_var.set('当前为普通权限；点击按钮经 UAC 确认后重启提权')

    def _elevate(self):
        if native.is_user_elevated():
            messagebox.showinfo('管理员模式', '当前已处于管理员模式')
            return
        ok = messagebox.askyesno(
            '管理员模式',
            '将以管理员权限重新启动被控端。\n'
            '系统会弹出 UAC 确认窗口，请点击"是/允许"。\n\n'
            '端口与密码保持不变，控制端重新连接一次即可。\n\n是否继续？')
        if not ok:
            return
        # 密码经命令行传入新实例（6 位临时数字密码，权衡后选择保持不变的简单方案）
        args = ['-s', '-p', str(self.port_var.get().strip() or common.DEFAULT_PORT),
                '-P', self._password]
        if native.relaunch_elevated(args):
            self._log('已发出管理员提权请求，本实例即将退出…')
            self.root.after(600, self._on_close)
        else:
            messagebox.showwarning('提示', '未获得管理员权限（可能取消了 UAC 弹窗），继续以普通权限运行。')
    def _password_text(self):
        return self.server.password if self.server else self._password

    def _refresh_ips(self):
        ips = get_local_ips()
        if ips:
            self.ip_var.set('  '.join(ips))
        else:
            self.ip_var.set('未检测到局域网 IP（请检查网络）')

    def _regenerate(self):
        pwd = generate_password()
        if self.server:
            self.server.set_password(pwd)
        self._password = pwd
        self.pwd_var.set(pwd)
        self._log('已重新生成密码')

    def _apply_opts(self):
        if not self.server:
            return
        self.server.all_screens = self.all_scr_var.get()
        self.server.jpeg_quality = self.quality_var.get()
        self.server.target_fps = self.fps_var.get()
        self.quality_lbl.config(text=str(self.quality_var.get()))
        self.fps_lbl.config(text=str(self.fps_var.get()))

    def _start(self):
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror('参数错误', '端口必须是数字')
            return
        if self.server and self.server.is_running():
            return
        self.pwd_var.set(self._password)
        self.server = DeskServer(
            password=self._password,
            port=port,
            save_dir=common.received_dir(),
            on_log=lambda t: self._ui(self._log, t),
            on_client_change=lambda on, addr: self._ui(self._on_client_change, on, addr),
            on_file_progress=lambda n, d, s, t: self._ui(self._on_progress, n, d, s, t),
            on_file_done=lambda n, ok, m: self._ui(self._on_file_done, n, ok, m),
            all_screens=self.all_scr_var.get(),
            jpeg_quality=self.quality_var.get(),
            target_fps=self.fps_var.get(),
        )
        if self.server.start():
            self.status_var.set('监听中（端口 %d）' % port)
            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
            self.port_var.set(str(port))
        else:
            self.server = None

    def _stop(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.status_var.set('未监听')
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def _send_file(self):
        if not (self.server and self.server._client_sock):
            messagebox.showinfo('提示', '请先等待控制端连接')
            return
        path = filedialog.askopenfilename(title='选择要发送给控制端的文件')
        if path:
            self.server.send_file(path)

    # ---------------- 回调（切回 UI 线程） ----------------
    def _ui(self, fn, *args):
        try:
            self.root.after(0, lambda: fn(*args))
        except Exception:
            pass

    def _log(self, text):
        self.log.config(state=tk.NORMAL)
        self.log.insert(tk.END, '[%s] %s\n' % (time_str(), text))
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _on_client_change(self, connected, addr):
        if connected:
            self.status_var.set('已连接：%s' % addr)
        else:
            self.status_var.set('监听中（端口 %s）' % self.port_var.get())

    def _on_progress(self, name, direction, sent, total):
        pct = (sent * 100 // total) if total else 0
        d = '发送' if direction == 'send' else '接收'
        self.progress_var.set('%s %s %s：%d%%（%s / %s）' % (d, name, direction_icon(direction), pct,
                                                              fmt_size(sent), fmt_size(total)))

    def _on_file_done(self, name, ok, msg):
        self.progress_var.set('文件 %s：%s' % ('完成' if ok else '失败', msg))
        if not ok:
            self._log('文件传输失败：%s' % msg)

    def _on_close(self):
        try:
            if self.server:
                self.server.stop()
        except Exception:
            pass
        self.root.destroy()


def direction_icon(name):
    return '→' if name == 'send' else '←'


def time_str():
    import datetime
    return datetime.datetime.now().strftime('%H:%M:%S')


def fmt_size(n):
    n = float(n)
    if n >= 1024 * 1024:
        return '%.1f MB' % (n / 1024 / 1024)
    if n >= 1024:
        return '%.1f KB' % (n / 1024)
    return '%d B' % int(n)