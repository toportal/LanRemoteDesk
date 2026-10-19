# -*- coding: utf-8 -*-
"""
LanRemoteDesk 公共层：协议帧、AES-256-GCM 会话加密、密码认证握手。
所有传输帧结构： [1B 类型][4B 大端长度][密文 body]
认证阶段（HELLO/CHALLENGE/AUTH/AUTH_OK/AUTH_FAIL）明文传输，不包含敏感数据；
认证通过后所有业务帧使用 AES-256-GCM 加密，密钥由密码+随机盐 HKDF 派生。
"""
import hmac
import hashlib
import json
import os
import socket
import struct
import threading

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF

PROTO_VERSION = 1
DEFAULT_PORT = 9100          # 默认监听端口（界面可修改；避开 VNC 常用的 5900）
MAX_FRAME = 128 * 1024 * 1024  # 单帧上限 128MB（文件分块也走帧）

# 消息类型
T_HELLO = 0x01        # client -> server : 握手
T_CHALLENGE = 0x02    # server -> client : 发送盐
T_AUTH = 0x03         # client -> server : 携带 HMAC
T_AUTH_OK = 0x04      # server -> client : 认证通过
T_AUTH_FAIL = 0x05    # server -> client : 认证失败
T_SCREEN_FRAME = 0x11 # server -> client : 屏幕 JPEG
T_MOUSE = 0x20        # client -> server : 鼠标事件
T_KEY = 0x21          # client -> server : 键盘事件
T_CLIP = 0x30         # 双向              : 剪贴板文本
T_FILE_START = 0x40   # 双向              : 文件开始
T_FILE_DATA = 0x41    # 双向              : 文件数据（嵌二进制）
T_FILE_END = 0x42     # 双向              : 文件结束
T_FILE_ERR = 0x43     # 双向              : 文件错误
T_FILE_CANCEL = 0x44  # 双向              : 文件取消
T_PING = 0x50         # 双向              : 心跳
T_PONG = 0x51         # 双向              : 心跳回
T_ERR = 0xE0          # 双向              : 通用错误


def app_base_dir():
    """打包后返回 exe 所在目录；源码运行时返回 src 目录。"""
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def received_dir():
    d = os.path.join(app_base_dir(), 'received')
    os.makedirs(d, exist_ok=True)
    return d


def derive_key(password, salt):
    """HKDF-SHA256：由密码与盐派生 32 字节会话密钥。"""
    return HKDF(password.encode('utf-8'), 32, salt, SHA256)


def make_mac(password, salt):
    """HMAC-SHA256：客户端应答认证（password 不经过网络传输）。"""
    return hmac.new(password.encode('utf-8'), salt, hashlib.sha256).hexdigest()


class SessionCipher(object):
    """AES-256-GCM 会话加密。"""

    def __init__(self, key):
        self.key = key

    def encrypt(self, plain):
        nonce = os.urandom(12)
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        ct, tag = cipher.encrypt_and_digest(plain)
        return nonce + ct + tag

    def decrypt(self, blob):
        if len(blob) < 12 + 16:
            raise ValueError('密文长度非法')
        nonce, ct, tag = blob[:12], blob[12:-16], blob[-16:]
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ct, tag)


def pack_obj(obj):
    """对象 -> JSON bytes（供 frame 加密）。"""
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


def unpack_obj(raw):
    return json.loads(raw.decode('utf-8'))


def recv_n(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('连接已断开')
        buf += chunk
    return buf


def send_plain(sock, mtype, obj):
    body = pack_obj(obj)
    sock.sendall(struct.pack('>BI', mtype, len(body)) + body)


def recv_plain(sock):
    head = recv_n(sock, 5)
    mtype = head[0]
    length = struct.unpack('>I', head[1:5])[0]
    if length > MAX_FRAME:
        raise ValueError('帧过大')
    return mtype, unpack_obj(recv_n(sock, length))


def send_cipher(sock, cipher, mtype, obj, blob=b''):
    """加密帧；blob 为可选的二进制附件（例如 JPEG / 文件分块），
    与 JSON 头之间以 0x00 分隔后整体加密。"""
    head = pack_obj(obj) + b'\x00' + blob
    body = cipher.encrypt(head)
    sock.sendall(struct.pack('>BI', mtype, len(body)) + body)


def recv_cipher(sock, cipher):
    mtype, body = recv_encrypted_frame(sock, cipher)
    head, sep, blob = body.partition(b'\x00')
    obj = unpack_obj(head)
    return mtype, obj, blob


def recv_encrypted_frame(sock, cipher):
    mtype = recv_n(sock, 1)[0]
    length = struct.unpack('>I', recv_n(sock, 4))[0]
    if length > MAX_FRAME:
        raise ValueError('消息长度非法')
    body = recv_n(sock, length)
    return mtype, cipher.decrypt(body)