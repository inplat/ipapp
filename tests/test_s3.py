from asyncio import AbstractEventLoop
from typing import Any, AsyncGenerator, Dict
from uuid import uuid4

import pytest

from ipapp import BaseApplication, BaseConfig
from ipapp.s3 import S3, FileTypeNotAllowedError, S3Config


@pytest.fixture(scope='session')
async def s3(loop: AbstractEventLoop) -> AsyncGenerator[S3, None]:
    s3 = S3(
        S3Config(
            endpoint_url='http://127.0.0.1:9000',
            aws_access_key_id='EXAMPLEACCESSKEY',
            aws_secret_access_key='EXAMPLESECRETKEY',
        )
    )
    app = BaseApplication(BaseConfig())
    app.add('s3', s3)

    await app.start()
    yield s3
    await app.stop()


async def save(
    s3: S3,
    uuid: str,
    filepath: str,
    bucket_name: str,
    content_type: str,
    metadata: Dict[str, Any],
) -> None:
    file_type = content_type.split('/')[-1]
    with open(filepath, 'rb') as f:
        if not await s3.bucket_exists(bucket_name):
            await s3.create_bucket(bucket_name)

        object_name = await s3.put_object(
            data=f,
            filename=uuid,
            folder='folder',
            metadata=metadata,
            bucket_name=bucket_name,
        )

        url = await s3.generate_presigned_url(object_name, 60)

        assert object_name == f'folder/{uuid}.{file_type}'
        assert url.scheme == 'http'
        assert url.netloc == '127.0.0.1:9000'
        assert url.path == f'/bucket/folder/{uuid}.{file_type}'

        obj = await s3.get_object(object_name, bucket_name=bucket_name)
        assert obj.bucket_name == bucket_name
        assert obj.object_name == object_name
        assert obj.content_type == content_type
        assert obj.metadata == metadata
        assert obj.size > 0

        f.seek(0)
        assert obj.body == f.read()


async def test_s3(s3: S3) -> None:
    # create/Delete Bucket
    uuid = uuid4().hex
    location = await s3.create_bucket(uuid)
    assert location == f'/{uuid}'
    await s3.delete_bucket(uuid)

    # create list Buckets
    uuid1 = uuid4().hex
    uuid2 = uuid4().hex

    await s3.create_bucket(uuid1)
    await s3.create_bucket(uuid2)

    buckets = await s3.list_buckets()
    assert buckets != []

    uuid1_exists = False
    uuid2_exists = False

    for bucket in buckets:
        if bucket.name == uuid1:
            uuid1_exists = True
        elif bucket.name == uuid2:
            uuid2_exists = True

    assert uuid1_exists is True
    assert uuid2_exists is True

    await s3.delete_bucket(uuid1)
    await s3.delete_bucket(uuid2)

    # Bucket exists
    uuid = uuid4().hex

    await s3.create_bucket(uuid)
    assert await s3.bucket_exists(uuid) is True
    await s3.delete_bucket(uuid)


async def test_s3_file_save(s3: S3) -> None:
    # Save PDF
    uuid = uuid4().hex
    filepath = f'tests/files/test.pdf'
    bucket_name = 'tests'
    content_type = 'application/pdf'
    metadata = {'foo': 'bar'}
    await save(s3, uuid, filepath, bucket_name, content_type, metadata)

    # Save JPG
    uuid = uuid4().hex
    filepath = f'tests/files/test.jpeg'
    bucket_name = 'tests'
    content_type = 'image/jpeg'
    metadata = {'foo': 'bar'}
    await save(s3, uuid, filepath, bucket_name, content_type, metadata)

    # Save GIF
    uuid = uuid4().hex
    filepath = f'tests/files/test.gif'
    bucket_name = 'tests'
    content_type = 'image/gif'
    metadata = {'foo': 'bar'}
    await save(s3, uuid, filepath, bucket_name, content_type, metadata)

    # Save Not Allowed
    uuid = uuid4().hex
    filepath = f'tests/files/test.ico'
    bucket_name = 'tests'
    content_type = 'image/ico'
    metadata = {'foo': 'bar'}
    with pytest.raises(FileTypeNotAllowedError):
        await save(s3, uuid, filepath, bucket_name, content_type, metadata)
