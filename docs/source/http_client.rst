Компонент HTTP клиент
=====================

Компонент: :class:`ipapp.http.client.Client`

Пример
------

.. code-block:: python

    import datetime
    from defusedxml import ElementTree as ET
    from yarl import URL
    from pydantic import BaseModel
    from typing import List
    from ipapp import BaseApplication, BaseConfig
    from ipapp.http.client import Client


    class CbrfConfig(BaseModel):
        url: str = 'http://www.cbr.ru/scripts/XML_daily.asp?date_req=02/03/2002'


    class Config(BaseConfig):
        cbrf: CbrfConfig


    class ExchangeRate(BaseModel):
        num_code: str
        char_code: str
        nominal: int
        name: str
        value: float


    class CbrfClient(Client):
        def __init__(self, cfg: CbrfConfig) -> None:
            self.cfg = cfg

        async def exchange_rate(self, date: datetime.date) -> List[ExchangeRate]:
            url = URL(self.cfg.url).with_query(
                {'date_req': date.strftime('%d/%m/%Y')}
            )
            resp = await self.request('GET', url)
            xml_str = await resp.read()
            tree = ET.fromstring(xml_str)

            result: List[ExchangeRate] = []
            for row in tree:
                result.append(
                    ExchangeRate(
                        num_code=row.findtext('NumCode'),
                        char_code=row.findtext('CharCode'),
                        nominal=row.findtext('Nominal'),
                        name=row.findtext('Name'),
                        value=row.findtext('Value').replace(',', '.'),
                    )
                )
            return result


    class App(BaseApplication):
        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self.add('cbrf', CbrfClient(cfg.cbrf))

