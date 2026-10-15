# -*- coding: utf-8 -*-
"""
控制端核心（无 UI）：
- 连接被控端并进行密码认证
- 接收屏幕帧（JPEG 数据，由 UI 层渲染）
- 发送鼠标 / 键盘事件
- 剪贴板文本双向同步
- 文件发送（本机文件 -> 被控端）与文件接收（保存至接收目录）
"""
import os
import socket
import threading
import time
import traceback

import common
import native

CHUNK = 256 * 1024


class DeskClient(object):
    """
    控制端核心。
    回调：
      on_connected()
      on_disconnected(reason)
      on_frame(info: dict, jpeg: bytes)
      on_log(text)
      on_clip(text)                     # 远端剪贴板内容
      on_file_progress(name, direction, sent, total)
      on_file_done(name, ok, msg)
      on_remote_clip_set()             # 本地剪贴板被远端更新
    """

    def __init__(self, host, port, password, save_dir=None,
                 on_connected=None, on_disconnected=None, on_frame=None,
                 on_log=None, on_clip=None, on_file_progress=None,
                 on_file_done=None):
        self.host = host
        self.port = int(port)
        self.password = password
        self.save_dir = save_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'received')
        os.makedirs(self.save_dir, exist_ok=True)

        self.on_connected = on_connected or (lambda *a, **k: None)
        self.on_disconnected = on_disconnected or (lambda *a, **k: None)
        self.on_frame = on_frame or (lambda *a, **k: None)
        self.on_log = on_log or (lambda *a, **k: None)
        self.on_clip = on_clip or (lambda *a, **k: None)
        self.on_file_progress = on_file_progress or (lambda *a, **k: None)
        self.on_file_done = on_file_done or (lambda *a, **k: None)

        self._sock = None
        self._cipher = None
        self._running = False
        self._connected = False
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()   # 串行化所有 socket 写操作，防止多线程 sendall 交错

    # ---------------- 连接 ----------------
    def connect(self, timeout=8):
        """阻塞式连接+认证，成功返回 True。"""
        if self._connected:
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((self.host, self.port))
            common.send_plain(sock, common.T_HELLO, {'ver': common.PROTO_VERSION})
            mtype, obj = common.recv_plain(sock)
            if mtype != common.T_CHALLENGE:
                raise ConnectionError('被控端协议异常')
            salt = bytes.fromhex(obj['salt'])
            common.send_plain(sock, common.T_AUTH,
                              {'mac': common.make_mac(self.password, salt)})
            mtype, obj = common.recv_plain(sock)
            if mtype == common.T_AUTH_FAIL:
                raise AuthError(obj.get('reason', '密码错误'))
            if mtype != common.T_AUTH_OK:
                raise ConnectionError('认证应答异常')
            key = common.derive_key(self.password, salt)
            self._cipher = common.SessionCipher(key)
            self._sock = sock
            sock.settimeout(None)
            self._connected = True
            self._running = True
            self.on_connected()
            threading.Thread(target=self._recv_loop, daemon=True).start()
            threading.Thread(target=self._clip_loop, daemon=True).start()
            return True
        except AuthError:
            try:
                sock.close()
            except Exception:
                pass
            raise
        except (socket.timeout, ConnectionError, OSError, ValueError) as e:
            try:
                sock.close()
            except Exception:
                pass
            raise ConnectionError('无法连接 %s:%d：%s' % (self.host, self.port, e))

    def disconnect(self):
        self._running = False
        self._connected = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._cipher = None

    def is_connected(self):
        return self._connected

    # ---------------- 接收 ----------------
    def _recv_loop(self):
        try:
            while self._running and self._connected:
                mtype, obj, blob = common.recv_cipher(self._sock, self._cipher)
                if mtype == common.T_SCREEN_FRAME:
                    self.on_frame(obj, blob)
                elif mtype == common.T_CLIP:
                    text = obj.get('text', '')
                    if text:
                        self.on_clip(text)
                elif mtype == common.T_FILE_START:
                    self._receive_file_inline(obj)
                elif mtype == common.T_PONG:
                    pass
                elif mtype == common.T_ERR:
                    self.on_log('被控端错误：%s' % obj)
                else:
                    self.on_log('收到未知消息：%s' % hex(mtype))
        except (ConnectionError, socket.timeout, ValueError):
            pass
        except Exception as e:
            self.on_log('接收异常：%s' % traceback.format_exc())
        finally:
            if self._connected:
                self._connected = False
                self._running = False
                self.on_disconnected('连接已断开')

    # ---------------- 发送 ----------------
    def _send(self, mtype, obj, blob=b''):
        if not self._connected or not self._sock:
            raise ConnectionError('未连接')
        with self._send_lock:
            common.send_cipher(self._sock, self._cipher, mtype, obj, blob)

    def send_mouse(self, x, y, action, button=1, delta=0):
        try:
            self._send(common.T_MOUSE, {'x': x, 'y': y, 'action': action,
                                        'button': button, 'delta': delta})
        except ConnectionError:
            pass

    def send_key(self, vk, down):
        try:
            self._send(common.T_KEY, {'vk': vk, 'down': bool(down)})
        except ConnectionError:
            pass

    def send_clip(self, text):
        try:
            self._send(common.T_CLIP, {'text': text})
            return True
        except ConnectionError:
            return False

    # ---------------- 文件发送（本机 -> 被控端） ----------------
    def send_file(self, path):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self.on_log('文件不存在：%s' % path)
            return False
        threading.Thread(target=self._send_file_thread, args=(path,), daemon=True).start()
        return True

    def _send_file_thread(self, path):
        name = os.path.basename(path)
        size = os.path.getsize(path)
        fid = '%d-%d' % (int(time.time() * 1000), os.getpid() % 10000)
        try:
            self._send(common.T_FILE_START, {'id': fid, 'name': name, 'size': size})
            sent = 0
            with open(path, 'rb') as f:
                while sent < size:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    self._send(common.T_FILE_DATA,
                               {'id': fid, 'offset': sent, 'size': size}, chunk)
                    sent += len(chunk)
                    self.on_file_progress(name, 'send', sent, size)
            self._send(common.T_FILE_END, {'id': fid})
            self.on_file_done(name, True, '已发送 %d 字节' % size)
        except Exception as e:
            self.on_file_done(name, False, str(e))
            self.on_log('文件发送失败：%s' % e)

    # ---------------- 文件接收（与 _recv_loop 串行） ----------------
    def _receive_file_inline(self, start_obj):
        name = os.path.basename(start_obj.get('name', 'file.bin')) or 'file.bin'
        size = int(start_obj.get('size', 0))
        path = os.path.join(self.save_dir, name)
        self.on_log('开始接收文件：%s（%.1f KB）' % (name, size / 1024.0))
        total = 0
        try:
            with open(path, 'wb') as f:
                while self._running and self._connected:
                    mtype, obj, blob = common.recv_cipher(self._sock, self._cipher)
                    if mtype == common.T_FILE_DATA:
                        f.write(blob)
                        total += len(blob)
                        self.on_file_progress(name, 'recv', total, size)
                    elif mtype == common.T_FILE_END:
                        f.flush()
                        self.on_file_done(name, True, '保存至：%s' % path)
                        self.on_log('文件接收完成：%s（%d 字节）' % (name, total))
                        return
                    elif mtype in (common.T_FILE_ERR, common.T_FILE_CANCEL):
                        self.on_file_done(name, False, '对方取消传输')
                        return
            self.on_file_done(name, False, '连接中断')
        except Exception as e:
            self.on_file_done(name, False, str(e))
            self.on_log('文件接收失败：%s' % e)

    # ---------------- 剪贴板同步 ----------------
    def _clip_loop(self):
        last = None
        remote = ''
        while self._running and self._connected:
            try:
                txt = native.clip_get_text()
            except Exception:
                txt = None
            if txt and txt != last and txt != remote:
                if self.send_clip(txt):
                    remote = txt
            last = txt
            time.sleep(3)


class AuthError(Exception):
    pass