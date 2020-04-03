from datetime import datetime
from typing import IO, Any, List, NamedTuple, Optional
from urllib.parse import ParseResult, urlparse

import aiobotocore
import magic
from aiobotocore.response import StreamingBody
from pydantic import BaseModel, Field

from ipapp.component import Component
from ipapp.s3.exceptions import FileTypeNotAllowedError


class Bucket(NamedTuple):
    name: str
    creation_date: datetime


class Object(NamedTuple):
    bucket_name: str
    object_name: str
    size: int
    etag: str
    content_type: str
    accept_ranges: str
    last_modified: datetime
    body: StreamingBody
    metadata: dict


class S3Config(BaseModel):
    endpoint_url: Optional[str] = Field(
        None,
        description="Адрес для подключения к S3",
        example="https://s3.amazonaws.com",
    )
    region_name: Optional[str] = Field(
        None, description="Название региона S3", example="us-east-1"
    )
    aws_access_key_id: Optional[str] = Field(
        None,
        description="ID ключа доступа к S3",
        example="AKIAIOSFODNN7EXAMPLE",
    )
    aws_secret_access_key: Optional[str] = Field(
        None,
        description="Ключ доступа к S3",
        example="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    bucket_name: str = Field(
        "bucket", description="Название бакета в S3", example="books"
    )
    allowed_types: str = Field(
        "pdf,jpeg,png,gif",
        description="Разрешенные для сохранения типы данных",
    )


class S3(Component):
    def __init__(self, cfg: S3Config, **kwargs: dict) -> None:
        self.cfg = cfg
        self.bucket_name = cfg.bucket_name
        self.allowed_types = cfg.allowed_types.split(',')
        self.kwargs = dict(
            endpoint_url=cfg.endpoint_url,
            region_name=cfg.region_name,
            aws_access_key_id=cfg.aws_access_key_id,
            aws_secret_access_key=cfg.aws_secret_access_key,
            **kwargs,
        )

    async def start(self) -> None:
        self.session = aiobotocore.get_session()

    async def list_buckets(self) -> List[Bucket]:
        async with self.session.create_client('s3', **self.kwargs) as s3:
            response = await s3.list_buckets()

            return [
                Bucket(
                    name=bucket.get('Name'),
                    creation_date=bucket.get('CreationDate'),
                )
                for bucket in response.get('Buckets', [])
            ]

    async def bucket_exists(self, bucket_name: str = None) -> bool:
        buckets = await self.list_buckets()

        for bucket in buckets:
            if bucket.name == (bucket_name or self.bucket_name):
                return True
        return False

    async def create_bucket(self, bucket_name: str = None) -> str:
        async with self.session.create_client('s3', **self.kwargs) as s3:
            response = await s3.create_bucket(
                ACL='private', Bucket=bucket_name or self.bucket_name,
            )
            return response.get('Location')

    async def delete_bucket(self, bucket_name: str = None) -> None:
        async with self.session.create_client('s3', **self.kwargs) as s3:
            await s3.delete_bucket(Bucket=bucket_name or self.bucket_name,)

    async def put_object(
        self,
        data: IO[Any],
        filename: str = None,
        folder: str = None,
        metadata: dict = None,
        bucket_name: str = None,
    ) -> str:
        content_type = magic.from_buffer(data.read(1024), mime=True)
        filetype = content_type.split('/')[-1]
        if filetype not in self.allowed_types:
            raise FileTypeNotAllowedError
        data.seek(0)

        object_name = f'{folder}/{filename}.{filetype}'.lower()

        async with self.session.create_client('s3', **self.kwargs) as s3:

            await s3.put_object(
                Bucket=bucket_name or self.bucket_name,
                Key=object_name,
                Body=data,
                ContentType=content_type,
                Metadata=metadata or {},
            )

        return object_name

    async def get_object(
        self, object_name: str, bucket_name: str = None
    ) -> StreamingBody:
        async with self.session.create_client('s3', **self.kwargs) as s3:
            response = await s3.get_object(
                Bucket=bucket_name or self.bucket_name, Key=object_name,
            )
            return Object(
                bucket_name=bucket_name or self.bucket_name,
                object_name=object_name,
                size=response.get('ContentLength'),
                etag=response.get('Etag'),
                content_type=response.get('ContentType'),
                accept_ranges=response.get('AcceptRanges'),
                last_modified=response.get('LastModified'),
                body=response.get('Body'),
                metadata=response.get('Metadata'),
            )

    async def generate_presigned_url(
        self, object_name: str, expires: int = 3600, bucket_name: str = None
    ) -> ParseResult:
        async with self.session.create_client('s3', **self.kwargs) as s3:
            url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': bucket_name or self.bucket_name,
                    'Key': object_name,
                },
                ExpiresIn=expires,
            )
            return urlparse(url)

    async def prepare(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> None:
        pass
