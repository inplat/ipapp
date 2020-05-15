import logging
import sys

from aiohttp.web import Request, Response

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig, ServerHandler
from ipapp.logger.adapters.zipkin import ZipkinAdapter, ZipkinConfig
from ipapp.s3 import S3, S3Config


class Config(BaseConfig):
    s3: S3Config
    srv: ServerConfig
    log_zipkin: ZipkinConfig


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.s3 = S3(cfg.s3)
        self.add('s3', self.s3)
        self.add('srv', Server(cfg.srv, Handler()))
        if cfg.log_zipkin.enabled:
            self.logger.add(ZipkinAdapter(cfg.log_zipkin))


class Handler(ServerHandler):

    app: App

    async def prepare(self) -> None:
        self.server.add_route('GET', '/link', self.link_handler)
        self.server.add_route('GET', '/file.pdf', self.pdf_handler)
        self.server.add_route('GET', '/file.png', self.png_handler)
        self.server.add_route('GET', '/file.jpg', self.jpg_handler)

    async def link_handler(self, request: Request) -> Response:
        link = await self.app.s3.generate_presigned_url('file.pdf')
        return Response(text=link.geturl())

    async def pdf_handler(self, request: Request) -> Response:
        obj = await self.app.s3.get_object('file.pdf')
        return Response(body=obj.body, content_type=obj.content_type)

    async def png_handler(self, request: Request) -> Response:
        async with self.app.s3 as s3:
            obj = await s3.get_object('file.png')
            return Response(body=obj.body, content_type=obj.content_type)

    async def jpg_handler(self, request: Request) -> Response:
        async with self.app.s3.create_client() as client:
            bucket = self.app.s3.bucket_name
            obj = await client.get_object(Key='file.jpg', Bucket=bucket)
            async with obj['Body'] as f:
                body = await f.read()
                return Response(body=body, content_type=obj['ContentType'])


if __name__ == '__main__':
    """
    Usage:

    APP_S3_ENDPOINT_URL=http://localhost:9000 \
    APP_S3_AWS_ACCESS_KEY_ID=EXAMPLEACCESSKEY \
    APP_S3_AWS_SECRET_ACCESS_KEY=EXAMPLESECRETKEY \
    python -m examples.s3
    """

    logging.basicConfig(level=logging.INFO)
    main(sys.argv, '1.0.0', App, Config)
