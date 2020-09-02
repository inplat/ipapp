from pydantic.main import BaseModel, Extra
from typing import Optional, List
from datetime import datetime


class RespounsListObjetsOwner(BaseModel):
    DisplayName: str
    ID: str


class RespounsListObjetsContents(BaseModel):
    Key: str
    LastModified: Optional[datetime]
    ETag: str
    Size: int
    StorageClass: str
    Owner: RespounsListObjetsOwner


class RespounsListObjets(BaseModel):
    IsTruncated: bool
    Contents: List[RespounsListObjetsContents]
    Name: str
    Prefix: str
    Delimiter: str
    MaxKeys: int
    EncodingType: str
    KeyCount: int

    class Config:
        extra = Extra.ignore
