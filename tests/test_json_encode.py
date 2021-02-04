import base64
import json
import os
from uuid import UUID

from ipapp.misc import json_encode, BASE64_MARKER


class CustomUUID(UUID):
    pass


def test_encode_subclass() -> None:
    data = {"uuid": CustomUUID("53fa72dd-16d5-418c-8faa-b6a201202930")}
    json_encode(data)


def test_encode_bytes() -> None:
    data = os.urandom(100)
    enc_data = json_encode(data)
    assert enc_data == f'"{BASE64_MARKER}{base64.b64encode(data).decode()}"'

    enc_data_ = json.loads(enc_data)[len(BASE64_MARKER) :]
    bytes_from_enc_data = base64.b64decode(enc_data_.encode())
    assert data == bytes_from_enc_data
