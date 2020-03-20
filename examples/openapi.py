import logging
import sys
from datetime import date
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from ipapp import BaseApplication, BaseConfig, main
from ipapp.http.server import Server, ServerConfig
from ipapp.rpc.http import BaseError
from ipapp.rpc.http.server import OpenApiRpcHandler as RpcHandler
from ipapp.rpc.http.server import OpenApiRpcHandlerConfig as RpcHandlerConfig_
from ipapp.rpc.http.server import method

VERSION = "1.0.0"


class RpcHandlerConfig(RpcHandlerConfig_):
    title: str = "Customer API"
    description: str = "Customer service description"
    contact_name: str = "Ivan Ivanov"
    contact_email: str = "ivan.ivanov@acme.inc"
    version: str = VERSION
    openapi_schemas: List[str] = ["examples/api.json"]


class Config(BaseConfig):
    http: ServerConfig
    handler: RpcHandlerConfig = RpcHandlerConfig()


class CustomerNotFound(BaseError):
    code = 404
    message = "Customer not found"


class Gender(str, Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"


class Passport(BaseModel):
    series: str = Field(..., regex=r"^\d{4}$", example="1234")
    number: str = Field(..., regex=r"^\d{6}$", example="567890")


class Customer(BaseModel):
    customer_id: UUID = Field(..., description="Customer UUID")
    username: str = Field(..., description="Username", example="ivan.ivanov")
    first_name: Optional[str] = Field(None, example="Ivan")
    last_name: Optional[str] = Field(None, example="Ivanov")
    birth_date: Optional[date] = Field(None, description="Date of birth")
    gender: Optional[Gender] = Field(None, description="Gender")
    passport: Optional[Passport] = Field(None, description="Passport")
    is_active: bool = Field(False, deprecated=True)


class CreateCustomerRequest(Customer):
    pass


class CreateCustomerResponse(Customer):
    pass


class UpdateCustomerResponse(Customer):
    pass


class Api:
    """
    Customer API
    """

    @method(
        request_model=CreateCustomerRequest,  # type: ignore
        response_model=CreateCustomerResponse,
    )
    async def create_customer(self, **kwargs) -> None:
        """Create Customer

        Create customer description
        """

    @method(summary="Get Customers", description="Get customers description")
    async def get_customers(self, customers: List[Customer]) -> List[Customer]:
        pass

    @method(errors=[CustomerNotFound])
    async def update_customer(
        self,
        customer_id: UUID,
        username: str = Field(..., example="ivan.ivanov"),
        first_name: Optional[str] = Field(None, example="Ivan"),
        last_name: Optional[str] = Field(None, example="Ivanov"),
        birth_date: Optional[date] = Field(None, description="Date of birth"),
        gender: Optional[Gender] = Field(None, description="Gender"),
        passport: Optional[Passport] = Field(None, description="Passport"),
        is_active: bool = Field(False, deprecated=True),
    ) -> UpdateCustomerResponse:
        if customer_id == UUID("435ff4ec-ac73-413c-ad4d-270020a354de"):
            raise CustomerNotFound
        return UpdateCustomerResponse(
            customer_id=customer_id, username=username
        )

    @method(deprecated=True)
    async def delete_customer(self, customer_id: UUID) -> UUID:
        return customer_id

    @method(
        request_ref="/api.json#/components/schemas/Request",  # type: ignore
        response_ref="/api.json#/components/schemas/Response",
    )
    async def find_customer(self, **kwargs) -> None:
        pass


class App(BaseApplication):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.add("srv", Server(cfg.http, RpcHandler(Api(), cfg.handler)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(sys.argv, VERSION, App, Config)
