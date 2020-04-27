from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Union

from pydantic import AnyUrl, BaseModel, Field

try:
    import email_validator

    # make autoflake ignore the unused import
    assert email_validator  # nosec
    from pydantic import EmailStr
except ImportError:  # pragma: no cover

    class EmailStr(str):  # type: ignore
        @classmethod
        def __get_validators__(cls) -> Iterable[Callable]:
            yield cls.validate

        @classmethod
        def validate(cls, v: Any) -> str:
            return str(v)


class Ref(BaseModel):
    ref: str = Field(..., alias="$ref")


class License(BaseModel):
    name: str
    url: Optional[AnyUrl]


class Contact(BaseModel):
    name: Optional[str]
    email: Optional[EmailStr]
    url: Optional[AnyUrl]


class Info(BaseModel):
    title: str
    description: Optional[str]
    termsOfService: Optional[AnyUrl]
    version: str
    contact: Optional[Contact]
    license: Optional[License]


class ServerVariable(BaseModel):
    default: str
    description: Optional[str]
    enum: Optional[List[str]]


class Server(BaseModel):
    url: str
    name: Optional[str]
    description: Optional[str]
    summary: Optional[str]
    variables: Optional[Dict[str, ServerVariable]]


class ExternalDocs(BaseModel):
    description: Optional[str] = None
    url: AnyUrl


class Tag(BaseModel):
    name: str
    summary: Optional[str]
    description: Optional[str]
    externalDocs: Optional[ExternalDocs]


class Discriminator(BaseModel):
    propertyName: str
    mapping: Optional[Dict[str, str]] = None


class XML(BaseModel):
    name: Optional[str] = None
    namespace: Optional[str] = None
    prefix: Optional[str] = None
    attribute: Optional[bool] = None
    wrapped: Optional[bool] = None


class SchemaBase(BaseModel):
    ref: Optional[str] = Field(None, alias="$ref")
    title: Optional[str] = None
    multipleOf: Optional[float] = None
    maximum: Optional[float] = None
    exclusiveMaximum: Optional[float] = None
    minimum: Optional[float] = None
    exclusiveMinimum: Optional[float] = None
    maxLength: Optional[int] = Field(None, gte=0)
    minLength: Optional[int] = Field(None, gte=0)
    pattern: Optional[str] = None
    maxItems: Optional[int] = Field(None, gte=0)
    minItems: Optional[int] = Field(None, gte=0)
    uniqueItems: Optional[bool] = None
    maxProperties: Optional[int] = Field(None, gte=0)
    minProperties: Optional[int] = Field(None, gte=0)
    required: Optional[List[str]] = None
    enum: Optional[List[Any]] = None
    type: Optional[str] = None
    allOf: Optional[List[Any]] = None
    oneOf: Optional[List[Any]] = None
    anyOf: Optional[List[Any]] = None
    not_: Optional[List[Any]] = Field(None, alias="not")
    items: Optional[Any] = None
    properties: Optional[Dict[str, Any]] = None
    additionalProperties: Optional[Union[Dict[str, Any], bool]] = None
    description: Optional[str] = None
    format: Optional[str] = None
    default: Optional[Any] = None
    nullable: Optional[bool] = None
    discriminator: Optional[Discriminator] = None
    readOnly: Optional[bool] = None
    writeOnly: Optional[bool] = None
    xml: Optional[XML] = None
    externalDocs: Optional[ExternalDocs] = None
    example: Optional[Any] = None
    deprecated: Optional[bool] = None


class Schema(SchemaBase):
    allOf: Optional[List[SchemaBase]] = None
    oneOf: Optional[List[SchemaBase]] = None
    anyOf: Optional[List[SchemaBase]] = None
    not_: Optional[List[SchemaBase]] = Field(None, alias="not")
    items: Optional[SchemaBase] = None
    properties: Optional[Dict[str, SchemaBase]] = None
    additionalProperties: Optional[Union[Dict[str, Any], bool]] = None


class ContentDescriptor(BaseModel):
    name: str
    summary: Optional[str]
    description: Optional[str]
    required: bool = False
    schema_: Schema = Field(..., alias="schema")
    deprecated: bool = False


class OneOf(BaseModel):
    oneOf: List[ContentDescriptor]


class Error(BaseModel):
    code: int
    message: str
    data: Optional[Any]


class Link(BaseModel):
    name: Optional[str]
    description: Optional[str]
    summary: Optional[str]
    method: Optional[str]
    params: Optional[Dict[str, Any]]
    server: Optional[Server]


class ParamStructure(Enum):
    BY_NAME = "by-name"
    BY_POSITION = "by-position"
    EITHER = "either"


class Example(BaseModel):
    name: Optional[str]
    summary: Optional[str]
    description: Optional[str]
    value: Any
    externalValue: Optional[str]


class ExamplePairing(BaseModel):
    name: Optional[str]
    description: Optional[str]
    summary: Optional[str]
    params: Optional[List[Union[Example, Ref]]]
    result: Optional[Union[Example, Ref]]


class Method(BaseModel):
    name: str
    tags: Optional[List[Union[Tag, Ref]]]
    summary: Optional[str]
    description: Optional[str]
    externalDocs: Optional[ExternalDocs]
    params: List[Union[ContentDescriptor, Ref, OneOf]]
    result: Union[ContentDescriptor, Ref, OneOf]
    deprecated: bool = False
    servers: Optional[List[Server]]
    errors: Optional[List[Error]]
    links: Optional[List[Link]]
    paramStructure: ParamStructure = ParamStructure.EITHER
    examples: Optional[List[ExamplePairing]]


class Components(BaseModel):
    contentDescriptors: Optional[Dict[str, ContentDescriptor]]
    schemas: Optional[Dict[str, Schema]]
    examples: Optional[Dict[str, Example]]
    links: Optional[Dict[str, Link]]
    errors: Optional[Dict[str, Error]]
    examplePairingObjects: Optional[Dict[str, ExamplePairing]]
    tags: Optional[Dict[str, Tag]]


class OpenRPC(BaseModel):
    openrpc: str
    info: Info
    servers: Optional[List[Server]]
    methods: List[Union[Method, Ref]]
    components: Optional[Components]
    externalDocs: Optional[ExternalDocs]
