Компонент HTTP сервер
=====================

Компонент: :class:`ipapp.http.server.Server`

Конфигурация: :class:`ipapp.http.server.ServerConfig`

Обработчик: :class:`ipapp.http.server.ServerHandler`

Пример
------

.. code-block:: python

    from aiohttp import web
    from ipapp import BaseApplication, BaseConfig, main
    from ipapp.http.server import Server, ServerConfig, ServerHandler


    class Config(BaseConfig):
        http: ServerConfig


    class HttpHandler(ServerHandler):
        async def prepare(self) -> None:
            self.server.add_route('GET', '/', self.home)
            self.server.add_static('/static', '/path/to/static/folder')

        async def home(self, request: web.Request) -> web.Response:
            return web.Response(text='Hello, world!')


    class App(BaseApplication):
        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self.add('srv', Server(cfg.http, HttpHandler()))
