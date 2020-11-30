import base64
import os
from uuid import UUID

from ipapp.misc import json_encode


class CustomUUID(UUID):
    pass


def test_encode_subclass() -> None:
    data = {"uuid": CustomUUID("53fa72dd-16d5-418c-8faa-b6a201202930")}
    json_encode(data)


def test_encode_bytes() -> None:
    data = os.urandom(100)
    enc_data = json_encode(data)
    print(enc_data)
    assert data == base64.b64decode(enc_data.encode())
