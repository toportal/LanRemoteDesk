# -*- coding: utf-8 -*-
"""启动主菜单：选择作为「被控端」还是「控制端」。"""
import tkinter as tk
from tkinter import ttk

from ui_client import ClientWindow
from ui_server import ServerWindow

VERSION = '1.0.0'


class MainMenu(object):
    def __init__(self, root):
        self.root = root
        root.title('LanRemoteDesk - 局域网远程桌面')
        root.geometry('460x300')
        root.resizable(False, False)

        tk.Label(root, text='LanRemoteDesk', font=('Microsoft YaHei UI', 20, 'bold'),
                 fg='#0b5394').pack(pady=(26, 4))
        tk.Label(root, text='局域网远程桌面工具（v%s）\n密码认证 + AES-GCM 加密' % VERSION,
                 font=('Microsoft YaHei UI', 9), fg='#666666').pack()

        btns = ttk.Frame(root)
        btns.pack(pady=22)
        ttk.Button(btns, text='被控端（让别人连接本机）', width=24,
                   command=self._open_server).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text='控制端（连接别人电脑）', width=24,
                   command=self._open_client).pack(side=tk.LEFT, padx=8)

        tk.Label(root, text='提示：可在一台电脑上开两个窗口自测（被控端 IP 填 127.0.0.1）',
                 font=('Microsoft YaHei UI', 8), fg='#888888').pack(pady=(14, 0))

    def _open_server(self):
        self.root.withdraw()
        win = tk.Toplevel(self.root)
        ServerWindow(win)
        win.protocol('WM_DELETE_WINDOW', lambda: (win.destroy(), self.root.deiconify()))

    def _open_client(self):
        self.root.withdraw()
        win = tk.Toplevel(self.root)
        ClientWindow(win)
        win.protocol('WM_DELETE_WINDOW', lambda: (win.destroy(), self.root.deiconify()))


def main():
    root = tk.Tk()
    MainMenu(root)
    root.mainloop()


if __name__ == '__main__':
    main()