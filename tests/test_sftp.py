import os
import uuid
from tempfile import TemporaryDirectory

from ipapp import BaseApplication, BaseConfig
from ipapp.sftp.client import SftpClient, SftpClientConfig


async def test_sftp() -> None:
    app = BaseApplication(BaseConfig())
    app.add(
        "sftp",
        SftpClient(
            SftpClientConfig(url="sftp://user:password@localhost:2222/upload")
        ),
    )
    await app.start()

    sftp: SftpClient = app.get("sftp")  # type: ignore
    test_data = "Test data"

    base_remote_path = await sftp.getcwd()
    assert base_remote_path == "/upload"

    remote_path = os.path.join(base_remote_path, str(uuid.uuid4()))
    await sftp.mkdir(remote_path)
    await sftp.chdir(remote_path)

    with TemporaryDirectory() as local_path:
        local_file_out = os.path.join(local_path, f"{uuid.uuid4()}_out.txt")
        local_file_ren = os.path.join(local_path, f"{uuid.uuid4()}_ren.txt")
        local_file_in = os.path.join(local_path, f"{uuid.uuid4()}_in.txt")
        with open(local_file_out, "w") as f:
            f.write(test_data)

        remote_file_out = os.path.join(
            remote_path, os.path.basename(local_file_out)
        )

        remote_file_ren = os.path.join(
            remote_path, os.path.basename(local_file_ren)
        )

        await sftp.put(local_file_out, remote_file_out)
        assert await sftp.exists(remote_file_out) is True
        assert os.path.basename(local_file_out) in await sftp.listdir(
            remote_path
        )

        await sftp.get(remote_file_out, local_file_in)
        with open(local_file_in) as f:
            assert f.read() == test_data

        await sftp.rename(remote_file_out, remote_file_ren)
        assert await sftp.exists(remote_file_ren) is True
        assert os.path.basename(remote_file_ren) in [
            path.filename for path in await sftp.readdir(remote_path)
        ]

        await sftp.remove(remote_file_ren)
        assert await sftp.exists(remote_file_ren) is False
        assert os.path.basename(remote_file_ren) not in await sftp.listdir(
            remote_path
        )

    await app.stop()
