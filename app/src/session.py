import base64
import ipaddress
import struct

from telethon.sessions import SQLiteSession
from telethon.crypto import AuthKey
from telethon.sessions.string import _STRUCT_PREFORMAT, CURRENT_VERSION

class MyStringSession(SQLiteSession):
    def __init__(self, string: str = None):
        super().__init__('client')
        if string:
            if string[0] != CURRENT_VERSION:
                raise ValueError('Not a valid string')

            string = string[1:]
            ip_len = 4 if len(string) == 352 else 16
            self._dc_id, ip, self._port, key = struct.unpack(
                _STRUCT_PREFORMAT.format(ip_len), MyStringSession.decode(string))

            self._server_address = ipaddress.ip_address(ip).compressed
            if any(key):
                self._auth_key = AuthKey(key)

    @staticmethod
    def encode(x: bytes) -> str:
        return base64.urlsafe_b64encode(x).decode('ascii')

    @staticmethod
    def decode(x: str) -> bytes:
        return base64.urlsafe_b64decode(x)