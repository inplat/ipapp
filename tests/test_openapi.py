from examples.openapi import App, Config
from ipapp.http.client import Client


async def test_openapi(unused_tcp_port: int) -> None:
    cfg = Config.from_dict({"http": {"port": unused_tcp_port}})
    app = App(cfg=cfg)
    app.add('clt', Client())

    await app.start()

    url = f"http://127.0.0.1:{unused_tcp_port}"
    clt = app.get('clt')

    response = await clt.request("GET", f"{url}/openapi.json")
    assert response.status == 200
    with open("tests/openapi.json") as f:
        assert await response.text() == f.read().replace(
            "8080", f"{unused_tcp_port}"
        )

    response = await clt.request("GET", f"{url}/docs")
    assert response.status == 200
    with open("tests/docs.html") as f:
        assert await response.text() == f.read()

    response = await clt.request("GET", f"{url}/redoc")
    assert response.status == 200
    with open("tests/redoc.html") as f:
        assert await response.text() == f.read()

    await app.stop()
