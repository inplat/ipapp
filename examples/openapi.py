import logging
import sys
from datetime import date
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig
from ipapp.rpc.http import BaseError
from ipapp.rpc.http.server import OpenApiRpcHandler
from ipapp.rpc.http.server import (
    OpenApiRpcHandlerConfig as OpenApiRpcHandlerConfig_,
)
from ipapp.rpc.http.server import method

VERSION = "1.0.0"


class OpenApiRpcHandlerConfig(OpenApiRpcHandlerConfig_):
    title: str = "Customer API"
    description: str = "Customer service description"
    contact_name: str = "Ivan Ivanov"
    license_name: str = "Acme License 1.0"
    version: str = VERSION
    openapi_schemas: List[str] = ["examples/api.json"]


class Config(BaseConfig):
    http: ServerConfig
    handler: OpenApiRpcHandlerConfig = OpenApiRpcHandlerConfig()


class CustomerNotFound(BaseError):
    code = 404
    message = "Customer not found"


class Gender(str, Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"


class Passport(BaseModel):
    series: str = Field(..., regex=r"^\d{4}$", description="Passport series")
    number: str = Field(..., regex=r"^\d{6}$", description="Passport number")

    class Config:
        schema_extra = {"examples": [{"series": "1234", "number": "567890"}]}


class Customer(BaseModel):
    customer_id: UUID = Field(..., description="Customer UUID")
    username: str = Field(..., description="Username")
    first_name: Optional[str] = Field(None, description="First name")
    last_name: Optional[str] = Field(None, description="Last name")
    birth_date: Optional[date] = Field(None, description="Date of birth")
    gender: Optional[Gender] = Field(None, description="Gender")
    passport: Optional[Passport] = Field(None, description="Passport")
    is_active: bool = False

    class Config:
        schema_extra = {
            "examples": [
                {
                    "username": "ivan.ivanov",
                    "first_name": "Ivan",
                    "last_name": "Ivanov",
                }
            ]
        }


class Api:
    """
    Customer API
    """

    @method(request_model=Customer, response_model=Customer)
    async def create_customer(self, **kwargs) -> None:  # type: ignore
        """Create Customer

        Create customer description
        """

    @method(summary="Get Customer", description="Get customer description")
    async def get_customer(self, customer_id: UUID) -> Customer:
        return Customer(customer_id=customer_id, username="iivanov")

    @method()
    async def update_customer(
        self,
        username: str = Field(..., description="First name"),
        first_name: Optional[str] = Field(None, description="First name"),
        last_name: Optional[str] = Field(None, description="Last name"),
        birth_date: Optional[date] = Field(None, description="Date of birth"),
        gender: Optional[Gender] = Field(None, description="Gender"),
        passport: Optional[Passport] = Field(None, description="Passport"),
        is_active: bool = False,
    ) -> UUID:
        return uuid4()

    @method(errors=[CustomerNotFound], deprecated=True)
    async def delete_customer(self, customer_id: UUID) -> None:
        if customer_id == UUID("435ff4ec-ac73-413c-ad4d-270020a354de"):
            raise CustomerNotFound

    @method(
        request_ref="/api.json#/components/schemas/Request",  # type: ignore
        response_ref="/api.json#/components/schemas/Response",
    )
    async def find_customer(self, **kwargs) -> None:
        pass


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add(
            "srv", Server(cfg.http, OpenApiRpcHandler(Api(), cfg.handler)),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, VERSION, App, Config)
