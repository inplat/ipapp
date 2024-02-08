from typing import Optional, Union
from urllib.parse import urlencode

from aiohttp import ClientResponse, web

from ipapp import BaseApplication, BaseConfig
from ipapp.http.client import Client, ClientHttpSpan
from ipapp.http.server import Server, ServerConfig, ServerHandler


async def test_http(unused_tcp_port):
    class TestClient(Client):
        _secret_keys = ('pwssd',)

        async def send(self, url: str) -> ClientResponse:
            return await self.request('GET', url)

        async def send_add_get(self, url: str) -> ClientResponse:
            return await self.request('GET', url)

        async def send_add_head(self, url: str) -> ClientResponse:
            return await self.request('HEAD', url)

        async def send_add_post(
            self,
            url: str,
            body: Optional[Union[dict, bytes]] = None,
            log_body: Optional[Union[dict, bytes]] = None,
        ) -> ClientResponse:
            return await self.request(
                'POST', url, body=body, log_body=log_body
            )

        async def send_add_put(self, url: str) -> ClientResponse:
            return await self.request('PUT', url)

        async def send_add_patch(self, url: str) -> ClientResponse:
            return await self.request('PATCH', url)

        async def send_add_delete(self, url: str) -> ClientResponse:
            return await self.request('DELETE', url)

    class Handler(ServerHandler):
        async def prepare(self) -> None:
            self.server.add_route('GET', '/', self.home)
            self.server.add_head('/', self.test_head)
            self.server.add_get('/test_get', self.test_get)
            self.server.add_get('/test_get_win1251', self.test_get_win1251)
            self.server.add_get(
                '/test_get_charset_resolver', self.test_get_charset_resolver
            )
            self.server.add_post('/test_post', self.test_post)
            self.server.add_patch('/test_patch', self.test_patch)
            self.server.add_put('/test_put', self.test_put)
            self.server.add_delete('/test_delete', self.test_delete)

        async def home(self, request: web.Request) -> web.Response:
            return web.Response(text='OK')

        async def test_get(self, request: web.Request) -> web.Response:
            body = request.method
            return web.Response(text=body)

        async def test_get_win1251(self, request: web.Request) -> web.Response:
            body = 'Тест кодировки'
            return web.Response(text=body, charset='windows-1251')

        async def test_get_charset_resolver(
            self, request: web.Request
        ) -> web.Response:
            body = 'Тест кодировки'.encode('windows-1251')
            return web.Response(
                body=body,
                headers={'Content-Type': 'windows-1251'},
            )

        async def test_head(self, request: web.Request) -> web.Response:
            return web.Response()

        async def test_post(self, request: web.Request) -> web.Response:
            body = request.method
            return web.Response(text=body)

        async def test_patch(self, request: web.Request) -> web.Response:
            body = request.method
            return web.Response(text=body)

        async def test_put(self, request: web.Request) -> web.Response:
            body = request.method
            return web.Response(text=body)

        async def test_delete(self, request: web.Request) -> web.Response:
            body = request.method
            return web.Response(text=body)

    app = BaseApplication(BaseConfig())
    app.add('srv', Server(ServerConfig(port=unused_tcp_port), Handler()))
    app.add('clt', TestClient())
    url_test = 'http://127.0.0.1:%d' % unused_tcp_port

    await app.start()

    resp = await app.get('clt').send(url_test)
    assert resp.status == 200
    assert await resp.text() == 'OK'

    resp_head = await app.get('clt').send_add_head(f'{url_test}/')
    assert resp_head.status == 200

    resp_get = await app.get('clt').send_add_get(f'{url_test}/test_get')
    assert resp_get.status == 200
    assert await resp_get.text() == 'GET'

    with app.logger.capture_span(ClientHttpSpan) as trap:
        resp_get_win = await app.get('clt').send_add_get(
            f'{url_test}/test_get_win1251'
        )
        assert trap.span.annotations['response_body'][0][0] == 'Тест кодировки'

    assert resp_get_win.status == 200
    assert await resp_get_win.text() == 'Тест кодировки'

    with app.logger.capture_span(ClientHttpSpan) as trap:
        resp_get_charset_resolver = await app.get('clt').send_add_get(
            f'{url_test}/test_get_charset_resolver'
        )
        assert trap.span.annotations['response_body'][0][0] == 'Тест кодировки'

    assert resp_get_charset_resolver.status == 200
    assert await resp_get_charset_resolver.text() == 'Тест кодировки'

    with app.logger.capture_span(ClientHttpSpan) as trap:
        await app.get('clt').send_add_get(f'{url_test}/test_get?pwssd=dggg')
        assert (
            trap.span.tags[ClientHttpSpan.TAG_HTTP_URL]
            == f'{url_test}/test_get?pwssd=***'
        )

    resp_post = await app.get('clt').send_add_post(f'{url_test}/test_post')
    assert resp_post.status == 200
    assert await resp_post.text() == 'POST'

    with app.logger.capture_span(ClientHttpSpan) as trap:
        body = {'pdqwq': '334ff'}
        log_body = {'pdqwq': '***'}
        await app.get('clt').send_add_post(
            f'{url_test}/test_post', body=body, log_body=log_body
        )
        assert trap.span.annotations[ClientHttpSpan.ANN_REQUEST_BODY][0][
            0
        ] == str(log_body)
        assert trap.span.tags[ClientHttpSpan.TAG_HTTP_REQUEST_SIZE] == str(
            len(urlencode(body).encode())
        )

    resp_patch = await app.get('clt').send_add_patch(f'{url_test}/test_patch')
    assert resp_patch.status == 200
    assert await resp_patch.text() == 'PATCH'

    resp_put = await app.get('clt').send_add_put(f'{url_test}/test_put')
    assert resp_put.status == 200
    assert await resp_put.text() == 'PUT'

    resp_delete = await app.get('clt').send_add_delete(
        f'{url_test}/test_delete',
    )
    assert resp_delete.status == 200
    assert await resp_delete.text() == 'DELETE'

    await app.stop()


# todo test healthcheck
# todo test error handler
