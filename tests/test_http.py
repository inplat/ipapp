from aiohttp import ClientResponse, web

from ipapp import Application
from ipapp.http.client import Client
from ipapp.http.server import Server, ServerConfig, ServerHandler


async def test_http(unused_tcp_port):
    class TestClient(Client):
        async def send(self, url: str) -> ClientResponse:
            return await self.request('GET', url)

    class Handler(ServerHandler):
        async def prepare(self) -> None:
            self.server.add_route('GET', '/', self.home)

        async def home(self, request: web.Request) -> web.Response:
            return web.Response(text='OK')

    app = Application()
    app.add('srv', Server(ServerConfig(port=unused_tcp_port), Handler()))
    app.add('clt', TestClient())
    await app.start()

    resp = await app.get('clt').send('http://127.0.0.1:%d/' % unused_tcp_port)

    assert resp.status == 200
    assert await resp.text() == 'OK'

    await app.stop()


# todo test healthcheck
# todo test error handler
