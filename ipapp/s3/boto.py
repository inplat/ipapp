import json
import mimetypes
from types import TracebackType
from typing import IO, Any, Dict, List, Optional, Type, Union
from urllib.parse import ParseResult, urlparse

import aiobotocore
import magic
from aiobotocore import AioSession
from aiobotocore.config import AioConfig
from aiobotocore.session import ClientCreatorContext
from pydantic import BaseModel, Field

from ipapp.component import Component
from ipapp.s3.exceptions import FileTypeNotAllowedError
from ipapp.s3.models import (
    Bucket,
    CopyObject,
    DeleteObject,
    GetObject,
    ListObjects,
)

from ..logger import Span, wrap2span


class S3ClientSpan(Span):
    KIND_CLIENT = "CLIENT"

    NAME_LIST_BUCKETS = "s3::list_buckets"
    NAME_BUCKET_EXISTS = "s3::bucket_exists"
    NAME_FILE_EXISTS = "s3::file_exists"
    NAME_CREATE_BUCKET = "s3::create_bucket"
    NAME_COPY_OBJECT = "s3::copy_object"
    NAME_DELETE_OBJECT = "s3::delete_object"
    NAME_LIST_OBJECTS = "s3::list_objects"
    NAME_DELETE_BUCKET = "s3::delete_bucket"
    NAME_PUT_OBJECT = "s3::put_object"
    NAME_GET_OBJECT = "s3::get_object"
    NAME_GENERATE_PRESIGNED_URL = "s3::generate_presigned_url"

    ANN_EVENT = "event"


class S3Config(BaseModel):
    endpoint_url: Optional[str] = Field(
        None,
        description='Адрес для подключения к S3',
        example='https://s3.amazonaws.com',
    )
    region_name: Optional[str] = Field(
        None, description='Название региона S3', example='us-east-1'
    )
    aws_access_key_id: Optional[str] = Field(
        None,
        description='ID ключа доступа к S3',
        example='AKIAIOSFODNN7EXAMPLE',
    )
    aws_secret_access_key: Optional[str] = Field(
        None,
        description='Ключ доступа к S3',
        example='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
    )
    api_version: Optional[str] = Field(
        None,
        description='Версия API. По умолчанию используется последняя',
        example='2013-08-21',
    )
    use_ssl: bool = Field(True, description='Использовать или нет SSL')
    verify: Optional[Union[str, bool]] = Field(
        None,
        description=(
            'Проверять или нет SSL сертификаты. '
            'По умолчанию сертификаты проверяются'
        ),
        example='False или path/to/cert/bundle.pem',
    )
    aws_session_token: Optional[str] = Field(
        None, description='Сессионный токен S3'
    )
    use_dns_cache: bool = Field(
        True, description='Использовать или нет кэш DNS'
    )
    force_close: bool = Field(False)
    keepalive_timeout: Union[int, float] = Field(
        60, description='Таймаут активных соединений'
    )
    connect_timeout: Union[int, float] = Field(
        60, description='Таймаут соединения'
    )
    read_timeout: Union[int, float] = Field(60, description='Таймаут чтения')
    max_pool_connections: int = Field(
        10, description='Максимальное количество соединений в пуле'
    )
    retry_max_attempts: int = Field(
        3,
        description='Максимальное количество попыток повторно выполнить запрос',
    )
    retry_mode: str = Field(
        'standard',
        regex=r'(legacy|standard|adaptive)',
        description='Режим повторных запросов',
    )
    bucket_name: str = Field(
        'bucket', description='Название бакета в S3', example='books'
    )
    allowed_types: str = Field(
        'pdf,jpeg,png,gif',
        description='Разрешенные для сохранения типы данных',
    )
    log_result: bool = Field(
        False, description="Логирование результатов запросов"
    )


class Client:
    def __init__(
        self,
        component: "S3",
        base_client_creator: ClientCreatorContext,
        bucket_name: str,
        allowed_types: List[str],
    ) -> None:
        self.component = component
        self.base_client_creator = base_client_creator
        self.bucket_name = bucket_name
        self.allowed_types = allowed_types

    async def __aenter__(self) -> 'Client':
        self.base_client = await self.base_client_creator.__aenter__()
        return self

    async def __aexit__(
        self, exc_type: Type, exc_val: Exception, exc_tb: TracebackType
    ) -> None:
        await self.base_client_creator.__aexit__(exc_type, exc_val, exc_tb)

    async def list_buckets(self) -> List[Bucket]:
        self.component.app.log_debug("S3 list_buckets")

        with wrap2span(
            name=S3ClientSpan.NAME_LIST_BUCKETS,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            response = await self.base_client.list_buckets()
            buckets = [
                Bucket(**bucket) for bucket in response.get('Buckets', [])
            ]

            if self.component.cfg.log_result:
                span.annotate(S3ClientSpan.ANN_EVENT, buckets)

            return buckets

    async def copy_object(
        self,
        src: str,
        dst: str,
        src_bucket_name: Optional[str] = None,
        dst_bucket_name: Optional[str] = None,
        **kwargs: Any,
    ) -> CopyObject:
        """https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.copy_object"""
        self.component.app.log_debug(
            "S3 copy object '%s':'%s' to '%s':'%s'",
            src_bucket_name,
            src,
            dst_bucket_name,
            dst,
        )

        with wrap2span(
            name=S3ClientSpan.NAME_COPY_OBJECT,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:

            result_copy_object = await self.base_client.copy_object(
                Bucket=dst_bucket_name,
                CopySource=f"{src_bucket_name}/{src}",
                Key=dst,
                **kwargs,
            )

            trace = {
                "src_file_title": f"{src_bucket_name}:{src}",
                "dst_file_title": f"{dst_bucket_name}:{dst}",
                "copy_object_result": result_copy_object['CopyObjectResult'],
            }

            span.annotate(
                S3ClientSpan.ANN_EVENT,
                json.dumps(trace, indent=4, default=str),
            )

            return CopyObject.parse_obj(result_copy_object)

    async def delete_object(
        self, file_path: str, bucket_name: Optional[str] = None, **kwargs: Any
    ) -> DeleteObject:
        """https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.delete_object"""

        bucket_name = bucket_name or self.bucket_name

        self.component.app.log_debug(
            "S3 delete object '%s' in '%s'", file_path, bucket_name
        )

        with wrap2span(
            name=S3ClientSpan.NAME_DELETE_OBJECT,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:

            result_delete_object = await self.base_client.delete_object(
                Bucket=bucket_name, Key=file_path, **kwargs
            )

            trace = {
                "src_file_title": f"{bucket_name}:{file_path}",
                "http_status_code": result_delete_object["ResponseMetadata"][
                    "HTTPStatusCode"
                ],
            }

            span.annotate(
                S3ClientSpan.ANN_EVENT,
                json.dumps(trace, indent=4, default=str),
            )

            return DeleteObject.parse_obj(result_delete_object)

    async def bucket_exists(self, bucket_name: Optional[str] = None) -> bool:
        bucket_name = bucket_name or self.bucket_name

        self.component.app.log_debug("S3 bucket_exists '%s'", bucket_name)

        with wrap2span(
            name=S3ClientSpan.NAME_BUCKET_EXISTS,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            buckets = await self.list_buckets()

            exists = False

            for bucket in buckets:
                if bucket.name == bucket_name:
                    exists = True

            span.annotate(S3ClientSpan.ANN_EVENT, exists)

            return exists

    async def list_objects(
        self, path: str, bucket_name: Optional[str] = None, **kwargs: Any
    ) -> ListObjects:
        """https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html#S3.Client.list_objects_v2"""
        bucket_name = bucket_name or self.bucket_name

        self.component.app.log_debug(
            "S3 list objects '%s' '%s'", bucket_name, path
        )

        with wrap2span(
            name=S3ClientSpan.NAME_LIST_OBJECTS,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:

            result_list_objects = await self.base_client.list_objects_v2(
                Bucket=bucket_name, Prefix=path, **kwargs
            )

            trace = {
                "folder": f"{bucket_name}:{path}",
                "contents": result_list_objects.get('Contents', None),
            }

            span.annotate(
                S3ClientSpan.ANN_EVENT,
                json.dumps(trace, indent=4, default=str),
            )

            return ListObjects.parse_obj(result_list_objects)

    async def file_exists(
        self,
        bucket_name: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> bool:
        bucket_name = bucket_name or self.bucket_name

        self.component.app.log_debug("S3 file_exists '%s'", file_name)

        with wrap2span(
            name=S3ClientSpan.NAME_FILE_EXISTS,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            buckets = await self.list_buckets()

            exists = False

            for bucket in buckets:

                if bucket.name == bucket_name:
                    response = await self.base_client.list_objects_v2(
                        Bucket=bucket_name,
                        Prefix=file_name,
                    )

                    for obj in response.get('Contents', []):
                        if obj['Key'] == file_name:
                            exists = True

            span.annotate(S3ClientSpan.ANN_EVENT, exists)

            return exists

    async def create_bucket(
        self, bucket_name: Optional[str] = None, acl: str = 'private'
    ) -> str:
        bucket_name = bucket_name or self.bucket_name

        self.component.app.log_debug("S3 create_bucket '%s'", bucket_name)

        with wrap2span(
            name=S3ClientSpan.NAME_CREATE_BUCKET,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            response = await self.base_client.create_bucket(
                ACL=acl,
                Bucket=bucket_name,
            )
            location = response.get('Location')

            span.annotate(S3ClientSpan.ANN_EVENT, location)

            return location

    async def delete_bucket(self, bucket_name: Optional[str] = None) -> None:
        bucket_name = bucket_name or self.bucket_name

        self.component.app.log_debug("S3 delete_bucket '%s'", bucket_name)

        with wrap2span(
            name=S3ClientSpan.NAME_DELETE_BUCKET,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            span.annotate(S3ClientSpan.ANN_EVENT, bucket_name)

            await self.base_client.delete_bucket(Bucket=bucket_name)

    async def put_object(
        self,
        data: IO[Any],
        filename: Optional[str] = None,
        folder: Optional[str] = None,
        metadata: Dict[str, Any] = None,
        bucket_name: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> str:
        bucket_name = bucket_name or self.bucket_name

        with wrap2span(
            name=S3ClientSpan.NAME_PUT_OBJECT,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            if not mime_type:
                mime_type_ = magic.from_buffer(data.read(2048), mime=True)
            else:
                mime_type_ = mime_type
            filetypes = mimetypes.guess_all_extensions(mime_type_)
            if filetypes:
                filetypes = [ft[1:] for ft in filetypes]
            else:
                filetypes = [mime_type_.split('/')[-1]]
            int_set = set(filetypes) & set(self.allowed_types)
            if not int_set:
                raise FileTypeNotAllowedError
            filetype = int_set.pop()

            data.seek(0)

            object_name = f'{folder}/{filename}.{filetype}'.lower()

            event = f"'{object_name}' to '{bucket_name}'"
            self.component.app.log_debug("S3 put_object %s", event)

            await self.base_client.put_object(
                Bucket=bucket_name,
                Key=object_name,
                Body=data,
                ContentType=mime_type_,
                Metadata=metadata or {},
            )

            span.annotate(S3ClientSpan.ANN_EVENT, event)

            return object_name

    async def get_object(
        self, object_name: str, bucket_name: Optional[str] = None
    ) -> GetObject:
        bucket_name = bucket_name or self.bucket_name
        event = f"'{object_name}' from '{bucket_name}'"
        self.component.app.log_debug("S3 get_object %s", event)

        with wrap2span(
            name=S3ClientSpan.NAME_GET_OBJECT,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            response = await self.base_client.get_object(
                Bucket=bucket_name,
                Key=object_name,
            )

            async with response['Body'] as f:
                body = await f.read()

            span.annotate(S3ClientSpan.ANN_EVENT, event)

            return GetObject(
                bucket_name=bucket_name,
                object_name=object_name,
                body=body,
                **response,
            )

    async def generate_presigned_url(
        self,
        object_name: str,
        expires: int = 3600,
        bucket_name: Optional[str] = None,
    ) -> ParseResult:
        bucket_name = bucket_name or self.bucket_name

        event = f"'{object_name}' from '{bucket_name}'"
        self.component.app.log_debug("S3 generate_presigned_url %s", event)

        with wrap2span(
            name=S3ClientSpan.NAME_GENERATE_PRESIGNED_URL,
            kind=S3ClientSpan.KIND_CLIENT,
            cls=S3ClientSpan,
            app=self.component.app,
        ) as span:
            span.annotate(S3ClientSpan.ANN_EVENT, event)

            url = await self.base_client.generate_presigned_url(
                ClientMethod='get_object',
                Params={'Bucket': bucket_name, 'Key': object_name},
                ExpiresIn=expires,
            )

            if self.component.cfg.log_result:
                span.annotate(S3ClientSpan.ANN_EVENT, url)

            return urlparse(url)


class S3(Component):

    session: AioSession

    def __init__(self, cfg: S3Config) -> None:
        self.cfg = cfg
        self.config = AioConfig(
            connector_args={
                'keepalive_timeout': cfg.keepalive_timeout,
                'use_dns_cache': cfg.use_dns_cache,
                'force_close': cfg.force_close,
            },
            connect_timeout=cfg.connect_timeout,
            read_timeout=cfg.read_timeout,
            max_pool_connections=cfg.max_pool_connections,
            retries={
                'max_attempts': cfg.retry_max_attempts,
                'mode': cfg.retry_mode,
            },
        )
        self.bucket_name = cfg.bucket_name
        self.allowed_types = cfg.allowed_types.split(',')

    async def __aenter__(self) -> Client:
        self.client = self._create_client()
        return self.client

    async def __aexit__(
        self, exc_type: Type, exc_val: Exception, exc_tb: TracebackType
    ) -> None:
        await self.client.base_client.close()

    async def list_buckets(self) -> List[Bucket]:
        async with self._create_client() as client:
            return await client.list_buckets()

    async def bucket_exists(self, bucket_name: Optional[str] = None) -> bool:
        async with self._create_client() as client:
            return await client.bucket_exists(bucket_name)

    async def file_exists(
        self,
        bucket_name: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> bool:
        async with self._create_client() as client:
            return await client.file_exists(bucket_name, file_name)

    async def create_bucket(
        self, bucket_name: Optional[str] = None, acl: str = 'private'
    ) -> str:
        async with self._create_client() as client:
            return await client.create_bucket(bucket_name, acl)

    async def delete_bucket(self, bucket_name: Optional[str] = None) -> None:
        async with self._create_client() as client:
            return await client.delete_bucket(bucket_name)

    async def put_object(
        self,
        data: IO[Any],
        filename: Optional[str] = None,
        folder: Optional[str] = None,
        metadata: Dict[str, Any] = None,
        bucket_name: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> str:
        async with self._create_client() as client:
            return await client.put_object(
                data,
                filename,
                folder,
                metadata,
                bucket_name,
                mime_type,
            )

    async def copy_object(
        self,
        src: str,
        dst: str,
        src_bucket_name: Optional[str] = None,
        dst_bucket_name: Optional[str] = None,
        **kwargs: Any,
    ) -> CopyObject:
        async with self._create_client() as client:
            return await client.copy_object(
                src, dst, src_bucket_name, dst_bucket_name, **kwargs
            )

    async def delete_object(
        self,
        file_path: str,
        bucket_name: Optional[str] = None,
        **kwargs: Any,
    ) -> DeleteObject:
        async with self._create_client() as client:
            return await client.delete_object(file_path, bucket_name, **kwargs)

    async def get_object(
        self, object_name: str, bucket_name: Optional[str] = None
    ) -> GetObject:
        async with self._create_client() as client:
            return await client.get_object(object_name, bucket_name)

    async def list_objects(
        self,
        path: str,
        bucket_name: Optional[str] = None,
        **kwargs: Any,
    ) -> ListObjects:
        async with self._create_client() as client:
            return await client.list_objects(path, bucket_name, **kwargs)

    async def generate_presigned_url(
        self,
        object_name: str,
        expires: int = 3600,
        bucket_name: Optional[str] = None,
    ) -> ParseResult:
        async with self._create_client() as client:
            return await client.generate_presigned_url(
                object_name, expires, bucket_name
            )

    def _create_client(self) -> Client:
        return Client(
            self, self.create_client(), self.bucket_name, self.allowed_types
        )

    def create_client(self, **kwargs: Any) -> ClientCreatorContext:
        return self.session.create_client(
            's3',
            **{
                'endpoint_url': self.cfg.endpoint_url,
                'region_name': self.cfg.region_name,
                'aws_access_key_id': self.cfg.aws_access_key_id,
                'aws_secret_access_key': self.cfg.aws_secret_access_key,
                'config': self.config,
                'api_version': self.cfg.api_version,
                'use_ssl': self.cfg.use_ssl,
                'verify': self.cfg.verify,
                'aws_session_token': self.cfg.aws_session_token,
                **kwargs,
            },
        )

    async def prepare(self) -> None:
        self.session = aiobotocore.get_session()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> None:
        pass
