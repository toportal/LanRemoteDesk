# -*- coding: utf-8 -*-
"""
被控端核心（无 UI，便于自动化测试与打包）：
- 监听 TCP，接受单个控制端连接
- 密码认证（HMAC 挑战应答，密码不外传）
- 屏幕抓取 -> JPEG -> AES-256-GCM 发送
- 鼠标 / 键盘事件注入
- 剪贴板文本双向同步
- 文件接收（保存至接收目录）与文件发送
"""
import io
import os
import random
import socket
import threading
import time
import traceback

from PIL import Image, ImageGrab

import common
import native

CHUNK = 256 * 1024  # 文件分块大小


def generate_password(digits=6):
    """生成随机数字密码。"""
    return ''.join(random.choice('0123456789') for _ in range(digits))


def get_local_ips():
    """枚举本机 IPv4 地址（过滤回环与无效地址）。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except Exception:
        pass
    # UDP 探测（不会真正发包），拿默认路由网卡地址
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith('127.'))


FIREWALL_RULE_NAME = 'LanRemoteDesk'


def ensure_firewall_rule(port, rule_name=FIREWALL_RULE_NAME):
    """尝试在 Windows 防火墙放行本监听端口的入站 TCP 规则。
    注意：需要管理员权限；非管理员运行会返回 (False, 原因)，不影响监听本身。
    每次先删除同名规则再添加，保证规则跟随当前端口且不重复堆积。"""
    try:
        import subprocess
        create_no_window = 0x08000000   # 避免在无控制台程序中弹出黑框
        # 先尝试删除同名规则（不存在时返回非 0，忽略）
        subprocess.run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule',
                        'name=%s' % rule_name],
                       capture_output=True, creationflags=create_no_window)
        r = subprocess.run(['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                            'name=%s' % rule_name, 'dir=in', 'action=allow',
                            'protocol=TCP', 'localport=%d' % int(port)],
                           capture_output=True, creationflags=create_no_window)
        if r.returncode == 0:
            return True, ''
        out = (r.stderr or r.stdout or b'').decode('gbk', errors='ignore')
        return False, out.strip()[:120]
    except Exception as e:
        return False, str(e)


class DeskServer(object):
    """
    被控端核心。
    回调（全部可在任意线程触发，UI 层需自行投递）：
      on_log(text)
      on_client_change(connected: bool, addr: str)
      on_file_progress(name, direction, sent, total)   direction: 'send'|'recv'
      on_file_done(name, ok, msg)
    """

    def __init__(self, password=None, port=common.DEFAULT_PORT, save_dir=None,
                 on_log=None, on_client_change=None,
                 on_file_progress=None, on_file_done=None,
                 on_clip_received=None,
                 all_screens=False, scale=1.0, jpeg_quality=80, target_fps=12,
                 auto_firewall=True):
        self.password = password or generate_password()
        self.port = int(port)
        self.save_dir = save_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'received')
        os.makedirs(self.save_dir, exist_ok=True)

        self.on_log = on_log or (lambda *a, **k: None)
        self.on_client_change = on_client_change or (lambda *a, **k: None)
        self.on_file_progress = on_file_progress or (lambda *a, **k: None)
        self.on_file_done = on_file_done or (lambda *a, **k: None)
        self.on_clip_received = on_clip_received or (lambda *a, **k: None)

        self.all_screens = bool(all_screens)
        self.scale = scale
        self.jpeg_quality = int(jpeg_quality)
        self.target_fps = max(1, min(60, int(target_fps)))
        self.auto_firewall = bool(auto_firewall)

        self._sock = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()   # 串行化所有 socket 写操作，防止多线程 sendall 交错
        self._client_sock = None
        self._client_addr = None
        self._cipher = None
        self._running = False
        self._frame_info = {'img_w': 0, 'img_h': 0,
                            'virt_w': 0, 'virt_h': 0,
                            'offset_x': 0, 'offset_y': 0}

    # ---------------- 生命周期 ----------------
    def start(self):
        if self._running:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(('0.0.0.0', self.port))
            self._sock.listen(2)
            self._sock.settimeout(1.0)
        except OSError as e:
            self.on_log('监听端口 %d 失败：%s' % (self.port, e))
            return False
        self._running = True
        self.on_log('已监听端口 %d，等待控制端连接…' % self.port)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        threading.Thread(target=self._clip_loop, daemon=True).start()
        if self.auto_firewall:
            threading.Thread(target=self._firewall_thread, daemon=True).start()
        return True

    def _firewall_thread(self):
        """后台尝试放行防火墙端口（需管理员权限；失败仅提示，不影响监听）。"""
        ok, msg = ensure_firewall_rule(self.port)
        if ok:
            self.on_log('已自动在 Windows 防火墙放行入站 TCP %d' % self.port)
        else:
            self.on_log('提示：未能自动放行防火墙（需管理员权限）。'
                        '若控制端连不上，请用管理员模式运行一次，或手动放行 TCP %d' % self.port)

    def stop(self):
        self._running = False
        with self._lock:
            c = self._client_sock
            self._client_sock = None
            self._cipher = None
            self._client_addr = None
        if c:
            try:
                c.close()
            except Exception:
                pass
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self.on_log('服务已停止')

    def set_password(self, pwd):
        self.password = pwd

    # ---------------- 连接 ----------------
    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    self.on_log('监听异常：%s' % traceback.format_exc())
                break
            threading.Thread(target=self._handle_client,
                             args=(conn, addr), daemon=True).start()

    def _handle_client(self, conn, addr):
        conn.settimeout(6.0)
        try:
            mtype, obj = common.recv_plain(conn)
            if mtype != common.T_HELLO:
                common.send_plain(conn, common.T_ERR, {'error': '协议错误'})
                conn.close()
                return
            salt = os.urandom(16)
            common.send_plain(conn, common.T_CHALLENGE, {'salt': salt.hex()})
            mtype, obj = common.recv_plain(conn)
            if mtype != common.T_AUTH or obj.get('mac') != common.make_mac(self.password, salt):
                common.send_plain(conn, common.T_AUTH_FAIL, {'reason': '密码错误'})
                time.sleep(0.5)
                conn.close()
                self.on_log('来自 %s 的认证失败（密码错误）' % addr[0])
                return
            key = common.derive_key(self.password, salt)
            cipher = common.SessionCipher(key)
            conn.settimeout(None)
            common.send_plain(conn, common.T_AUTH_OK, {'ok': True, 'ver': common.PROTO_VERSION})
            with self._lock:
                old = self._client_sock
                self._client_sock = conn
                self._client_addr = addr
                self._cipher = cipher
            if old and old is not conn:
                try:
                    old.close()
                except Exception:
                    pass
            self.on_log('控制端 %s:%d 已连接' % (addr[0], addr[1]))
            self.on_client_change(True, '%s:%d' % (addr[0], addr[1]))
            threading.Thread(target=self._screen_loop, daemon=True).start()
            self._recv_loop(conn)
        except (ConnectionError, socket.timeout, ValueError) as e:
            self.on_log('与 %s 的连接结束：%s' % (addr[0], e))
        except Exception as e:
            self.on_log('连接处理异常：%s' % e)
        finally:
            with self._lock:
                if self._client_sock is conn:
                    self._client_sock = None
                    self._cipher = None
                    self._client_addr = None
            try:
                conn.close()
            except Exception:
                pass
            self.on_client_change(False, '')

    def _client(self):
        with self._lock:
            return self._client_sock, self._cipher

    def _send(self, mtype, obj, blob=b''):
        """所有加密帧发送都经过发送锁串行化，避免多线程 sendall 交错损坏字节流。"""
        sock, cipher = self._client()
        if sock is None or cipher is None:
            raise ConnectionError('没有已连接的控制端')
        with self._send_lock:
            common.send_cipher(sock, cipher, mtype, obj, blob)

    # ---------------- 屏幕 ----------------
    def _grab_jpeg(self):
        if self.all_screens:
            x, y, w, h = native.get_virtual_screen()
            img = ImageGrab.grab(all_screens=True)   # 含负坐标副屏，比手工 bbox 更健壮
        else:
            x, y, w, h = native.get_primary_screen()
            img = ImageGrab.grab()                    # 抓主屏
        if self.scale and self.scale != 1.0:
            img = img.resize((max(1, int(img.width * self.scale)),
                              max(1, int(img.height * self.scale))), Image.LANCZOS)
        self._frame_info = {'img_w': img.width, 'img_h': img.height,
                            'virt_w': w, 'virt_h': h,
                            'offset_x': x, 'offset_y': y}
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=self.jpeg_quality, optimize=True)
        return buf.getvalue()

    def _screen_loop(self):
        interval = 1.0 / self.target_fps
        fail_count = 0
        while self._running:
            if self._client()[0] is None:
                return
            t0 = time.time()
            try:
                jpeg = self._grab_jpeg()
                fail_count = 0
                self._send(common.T_SCREEN_FRAME, self._frame_info, jpeg)
            except (ConnectionError, socket.timeout, ValueError):
                return
            except Exception as e:
                # 抓屏失败多为桌面会话不可用（屏幕锁定/远程桌面断开/无交互桌面），
                # 属于可恢复情况：降低频率重试，不退出屏幕循环，会话恢复后自动续帧。
                fail_count += 1
                if fail_count == 1 or fail_count % 10 == 0:
                    self.on_log('抓屏失败（第%d次）：%s。常见原因：屏幕已锁定、远程桌面已断开或会话无活动桌面，请保持被控端桌面为解锁状态。' % (fail_count, e))
                time.sleep(max(0.5, interval + 0.3))
                continue
            dt = time.time() - t0
            if dt < interval:
                time.sleep(interval - dt)

    # ---------------- 指令接收 ----------------
    def _recv_loop(self, conn):
        cipher = self._cipher
        while self._running:
            try:
                mtype, obj, blob = common.recv_cipher(conn, cipher)
            except (ConnectionError, socket.timeout, ValueError):
                return
            if mtype == common.T_MOUSE:
                native.inject_mouse(obj.get('x', 0), obj.get('y', 0),
                                    obj.get('action', 'move'),
                                    obj.get('button', 1),
                                    obj.get('delta', 0))
            elif mtype == common.T_KEY:
                native.inject_key(obj.get('vk', 0), obj.get('down', False))
            elif mtype == common.T_CLIP:
                text = obj.get('text', '')
                if text:
                    try:
                        native.clip_set_text(text)
                    except OSError:
                        pass
                    self.on_clip_received(text)
            elif mtype == common.T_FILE_START:
                self._receive_file_inline(conn, cipher, obj)
            elif mtype == common.T_PING:
                try:
                    self._send(common.T_PONG, {})
                except Exception:
                    return
            else:
                self.on_log('收到未知消息：%s' % hex(mtype))

    # ---------------- 文件接收（与 _recv_loop 串行，避免抢读同一 socket） ----------------
    def _receive_file_inline(self, conn, cipher, start_obj):
        fid = start_obj.get('id', '')
        name = os.path.basename(start_obj.get('name', 'file.bin')) or 'file.bin'
        size = int(start_obj.get('size', 0))
        path = os.path.join(self.save_dir, name)
        self.on_log('开始接收文件：%s（%.1f KB）' % (name, size / 1024.0))
        total = 0
        try:
            with open(path, 'wb') as f:
                while self._running:
                    mtype, obj, blob = common.recv_cipher(conn, cipher)
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
                        self.on_log('文件接收被取消：%s' % name)
                        return
            self.on_file_done(name, False, '连接中断')
        except Exception as e:
            self.on_file_done(name, False, str(e))
            self.on_log('文件接收失败：%s' % e)

    # ---------------- 文件发送 ----------------
    def send_file(self, path):
        if self._client()[0] is None:
            self.on_log('没有已连接的控制端，无法发送文件')
            return False
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self.on_log('文件不存在：%s' % path)
            return False
        threading.Thread(target=self._send_file_thread, args=(path,), daemon=True).start()
        return True

    def _send_file_thread(self, path):
        name = os.path.basename(path)
        size = os.path.getsize(path)
        fid = '%d-%d' % (int(time.time() * 1000), random.randint(1000, 9999))
        try:
            self._send(common.T_FILE_START,
                       {'id': fid, 'name': name, 'size': size})
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
            self.on_log('文件发送完成：%s' % name)
        except Exception as e:
            self.on_file_done(name, False, str(e))
            self.on_log('文件发送失败：%s' % e)

    # ---------------- 剪贴板同步 ----------------
    def _clip_loop(self):
        last = None
        remote = ''
        while self._running:
            try:
                txt = native.clip_get_text()
            except Exception:
                txt = None
            if txt and txt != last and txt != remote:
                try:
                    self._send(common.T_CLIP, {'text': txt})
                    remote = txt
                except Exception:
                    pass
            last = txt
            time.sleep(3)