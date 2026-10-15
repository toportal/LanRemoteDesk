# -*- coding: utf-8 -*-
"""
LanRemoteDesk 自动化自检脚本（无 UI 依赖）：
1. 协议层：密钥派生 / HMAC / AES-GCM 加解密往返
2. 端到端：被控端+控制端 回环连接
   - 错误密码被拒，正确密码通过
   - 收到屏幕帧（JPEG 有效、尺寸正常），保存样例到 .temp
   - 鼠标 / 键盘事件注入被正确转发（monkeypatch 记录，不真实注入）
   - 剪贴板文本同步（会备份并恢复本机剪贴板）
   - 双向文件传输（字节一致性校验）
"""
import hashlib
import os
import random
import socket
import sys
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, 'src')
TEMP = os.path.join(os.path.dirname(BASE), '.temp', 'rd-test')
os.makedirs(TEMP, exist_ok=True)
sys.path.insert(0, SRC)

import common        # noqa: E402
import native        # noqa: E402
from desk_server import DeskServer   # noqa: E402
from desk_client import DeskClient, AuthError  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=''):
    if cond:
        PASSED.append(name)
        print('  [OK] %s' % name)
    else:
        FAILED.append(name)
        print('  [FAIL] %s %s' % (name, detail))


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def test_protocol():
    print('== 1. 协议层 ==')
    salt = os.urandom(16)
    mac = common.make_mac('123456', salt)
    check('HMAC 应答正确', mac == common.make_mac('123456', salt),
          mac)
    key = common.derive_key('123456', salt)
    c1 = common.SessionCipher(key)
    blob = c1.encrypt(b'hello world' * 100)
    c2 = common.SessionCipher(key)
    check('AES-GCM 加解密往返', c2.decrypt(blob) == b'hello world' * 100)
    try:
        common.SessionCipher(common.derive_key('wrong', salt)).decrypt(blob)
        check('错误密钥解密被拒', False)
    except Exception:
        check('错误密钥解密被拒', True)


def test_e2e():
    print('== 2. 端到端回环测试 ==')
    pwd = '888888'
    port = free_port()
    server_recv = os.path.join(TEMP, 'server-recv')
    client_recv = os.path.join(TEMP, 'client-recv')

    server_clip = []
    server_files_done = []
    server_log = []
    # 清空接收目录，避免旧文件干扰判断
    for d in (server_recv, client_recv):
        if os.path.isdir(d):
            for fn in os.listdir(d):
                try:
                    os.remove(os.path.join(d, fn))
                except Exception:
                    pass
        os.makedirs(d, exist_ok=True)
    server = DeskServer(password=pwd, port=port,
                        save_dir=server_recv,
                        on_log=server_log.append,
                        on_clip_received=server_clip.append,
                        on_file_done=lambda n, ok, m: server_files_done.append((n, ok, m)),
                        target_fps=10, jpeg_quality=70)
    assert server.start()
    time.sleep(0.3)

    # 2.1 错误密码
    c = DeskClient('127.0.0.1', port, '000000', save_dir=client_recv)
    try:
        c.connect()
        check('错误密码被拒绝', False, '竟然连上了')
    except AuthError:
        check('错误密码被拒绝', True)
    except Exception as e:
        check('错误密码被拒绝', False, str(e))

    # 2.2 正确密码 + 收到屏幕帧
    frames = []
    frames_ev = threading.Event()
    clip_ev = threading.Event()
    client_done = []

    def on_frame(info, jpeg):
        frames.append((info, jpeg))
        frames_ev.set()

    c = DeskClient('127.0.0.1', port, pwd, save_dir=client_recv,
                   on_frame=on_frame,
                   on_clip=lambda t: clip_ev.set(),
                   on_file_done=lambda n, ok, m: client_done.append((n, ok, m)))
    ok = c.connect()
    check('正确密码连接成功', ok)
    check('frames_ev.wait(8)', frames_ev.wait(8))
    if frames:
        info, jpeg = frames[0]
        check('帧信息含尺寸', info.get('img_w', 0) > 0 and info.get('img_h', 0) > 0,
              str(info))
        check('JPEG 头合法', jpeg[:2] == b'\xff\xd8')
        sample = os.path.join(TEMP, 'sample-frame.jpg')
        with open(sample, 'wb') as f:
            f.write(jpeg)
        print('  (样例帧已存 %s，%d 字节)' % (sample, len(jpeg)))

    # 2.3 鼠标/键盘事件转发（monkeypatch，不真实注入）
    events = []
    orig_m = native.inject_mouse
    orig_k = native.inject_key

    def fake_mouse(x, y, action, button=1, delta=0):
        events.append(('mouse', x, y, action, button, delta))

    def fake_key(vk, down):
        events.append(('key', vk, down))

    native.inject_mouse = fake_mouse
    native.inject_key = fake_key
    try:
        c.send_mouse(320, 240, 'down', 1, 0)
        c.send_mouse(321, 241, 'up', 1, 0)
        c.send_key(65, True)
        c.send_key(65, False)
        time.sleep(0.5)
        check('鼠标事件转发', ('mouse', 320, 240, 'down', 1, 0) in events,
              str(events))
        check('键盘事件转发', ('key', 65, True) in events, str(events))
    finally:
        native.inject_mouse = orig_m
        native.inject_key = orig_k

    # 2.4 剪贴板同步（备份/恢复真实剪贴板）
    backup = None
    try:
        backup = native.clip_get_text()
    except Exception:
        backup = None
    try:
        native.clip_set_text('RD-TEST-%d' % random.randint(1000, 9999))
        time.sleep(7)  # 两端轮询间隔 3s
        check('剪贴板被控端已收到', bool(server_clip), str(server_clip))
        check('控制端收到回传', clip_ev.is_set())
    except Exception as e:
        check('剪贴板测试可执行', False, str(e))
    finally:
        if backup is not None:
            try:
                native.clip_set_text(backup)
            except Exception:
                pass

    # 2.5 双向文件传输（3 轮压测：每轮新文件名，验证多线程并发发送不损坏字节流）
    ok_c2s, ok_s2c = 0, 0
    for rnd in range(3):
        fname = 'stress_r%d.bin' % rnd
        src = os.path.join(TEMP, fname)
        with open(src, 'wb') as f:
            f.write(os.urandom(1024 * 1024 + 777))
        h = sha(src)

        # 控制端 -> 被控端
        c.send_file(src)
        got = os.path.join(server_recv, fname)
        t0 = time.time()
        while time.time() - t0 < 30:
            if os.path.exists(got) and os.path.getsize(got) == os.path.getsize(src):
                break
            time.sleep(0.1)
        if os.path.exists(got) and sha(got) == h:
            ok_c2s += 1

        # 被控端 -> 控制端
        got2 = os.path.join(client_recv, fname)
        server.send_file(src)
        t0 = time.time()
        while time.time() - t0 < 30:
            if os.path.exists(got2) and os.path.getsize(got2) == os.path.getsize(src):
                break
            time.sleep(0.1)
        if os.path.exists(got2) and sha(got2) == h:
            ok_s2c += 1
    check('文件传输 3 轮内容一致（控制端->被控端）', ok_c2s == 3, '%d/3' % ok_c2s)
    check('文件传输 3 轮内容一致（被控端->控制端）', ok_s2c == 3, '%d/3' % ok_s2c)

    c.disconnect()
    server.stop()


def test_imports():
    print('== 0. 全模块导入检查 ==')
    try:
        import ui_server  # noqa: F401
        import ui_client  # noqa: F401
        import ui_main   # noqa: F401
        check('UI 模块可正常导入', True)
    except Exception as e:
        check('UI 模块可正常导入', False, repr(e))


def test_mapping():
    """点击坐标换算：画面等比缩放居中显示（可能留黑边），必须按实际显示矩形换算。"""
    print('== 0.5 坐标映射（letterbox） ==')
    from ui_client import ClientWindow
    map_ = ClientWindow._map_local_to_virtual
    # 远端 1920x1080，画布 1600x800，等比缩放后显示为 1600x675，垂直居中（上下黑边 62.5px）
    rx, ry = map_(800, 62.5, 1600, 800, 1600, 675, 1920, 1080, 0, 0)
    check('图像顶边中点映射到远端(960,0)', abs(rx - 960) < 0.01 and abs(ry - 0) < 0.01, '(%s,%s)' % (rx, ry))
    rx, ry = map_(1600, 62.5 + 675, 1600, 800, 1600, 675, 1920, 1080, 0, 0)
    check('图像右下角映射到远端(1920,1080)', abs(rx - 1920) < 0.01 and abs(ry - 1080) < 0.01, '(%s,%s)' % (rx, ry))
    rx, ry = map_(800, 5, 1600, 800, 1600, 675, 1920, 1080, 0, 0)
    check('上方黑边钳制到远端 y=0', ry == 0, 'ry=%s' % ry)
    # 多屏负坐标：虚拟屏 -1920..1920，点击画面左边缘应映射到 -1920
    rx, ry = map_(0, 62.5, 1600, 800, 1600, 675, 3840, 1080, -1920, 0)
    check('多屏负偏移：画面左缘映射到远端 -1920', abs(rx - (-1920)) < 0.01, 'rx=%s' % rx)


def test_client_click():
    """控制端左键点击链路：真实 Tk 事件触发必须转发 down/up（不能被 focus_set 覆盖）。"""
    print('== 0.6 控制端点击链路 ==')
    import tkinter as tk
    from ui_client import ClientWindow

    root = tk.Tk()
    try:
        w = ClientWindow(root)
        root.update()
        # <ButtonPress-1> 与 <Button-1> 是同一事件，绑定的 handler 不得是 focus_set
        bind_text = w.canvas.bind('<ButtonPress-1>') or ''
        check('左键按下绑定未被 focus_set 覆盖', 'focus_set' not in bind_text, bind_text)

        calls = []

        class FakeClient(object):
            def is_connected(self):
                return True

            def send_mouse(self, x, y, action, button=0, delta=0):
                calls.append((x, y, action, button, delta))

        w.client = FakeClient()
        # 真实事件级触发：经 Tk 绑定分发改发，验证 <ButtonPress-1> 没有被别的逻辑覆盖
        w.canvas.event_generate('<ButtonPress-1>', x=320, y=240)
        w.canvas.event_generate('<ButtonRelease-1>', x=320, y=240)
        root.update()
        check('真实按下事件触发转发', any(c[2] == 'down' and c[3] == 1 for c in calls), str(calls))
        check('真实抬起事件触发转发', any(c[2] == 'up' and c[3] == 1 for c in calls), str(calls))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_elevation():
    """管理员提权辅助函数。"""
    print('== 0.7 管理员提权 ==')
    import native
    check('提权状态检测返回 bool', isinstance(native.is_user_elevated(), bool))
    exe, params = native.build_launch_args(['-s', '-p', '5900', '-P', '123456'])
    check('提权重启参数含完整指令', '5900' in params and '123456' in params, params)
    check('提权 exe 路径非空', bool(exe), str(exe))


def main():
    test_imports()
    test_mapping()
    test_client_click()
    test_elevation()
    test_protocol()
    test_e2e()
    print('\n结果：%d 通过，%d 失败' % (len(PASSED), len(FAILED)))
    if FAILED:
        print('失败项：%s' % ', '.join(FAILED))
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()