# -*- coding: utf-8 -*-
"""LanRemoteDesk 入口。
用法：
  无参数         启动主菜单
  -s [密码]      直接进入被控端模式（可省略密码，省略则随机生成并显示）
  -c             直接进入控制端模式
  -p 端口        指定被控端端口（默认 5900）
  -t             控制端连接被控端（IP:端口:密码，用于自动化/命令行场景）
"""
import sys
import tkinter as tk


def main():
    args = sys.argv[1:]
    argv = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-s', '--server'):
            argv['server'] = True
        elif a in ('-c', '--client'):
            argv['client'] = True
        elif a in ('-p', '--port') and i + 1 < len(args):
            argv['port'] = args[i + 1]
            i += 1
        elif a in ('-P', '--password') and i + 1 < len(args):
            argv['password'] = args[i + 1]
            i += 1
        elif a.startswith('-'):
            pass
        i += 1

    if argv.get('server'):
        from ui_server import ServerWindow
        root = tk.Tk()
        ServerWindow(root, password=argv.get('password'),
                     port=int(argv.get('port') or 5900))
        root.mainloop()
    elif argv.get('client'):
        from ui_client import ClientWindow
        root = tk.Tk()
        ClientWindow(root)
        root.mainloop()
    else:
        from ui_main import MainMenu
        root = tk.Tk()
        MainMenu(root)
        root.mainloop()


if __name__ == '__main__':
    main()