from pydantic import BaseModel, Extra, Field
from typing import Optional, List, Dict
from datetime import datetime


class Owner(BaseModel):
    display_name: str = Field(None, alias='DisplayName')
    id: str = Field(None, alias='ID')


class Contents(BaseModel):
    key: str = Field(None, alias='Key')
    last_modified: Optional[datetime] = Field(None, alias='LastModified')
    e_tag: str = Field(None, alias='ETag')
    size: int = Field(None, alias='Size')
    storage_class: str = Field(None, alias='StorageClass')
    owner: Owner = Field(None, alias='Owner')


class ListObjects(BaseModel):
    isTruncated: bool = Field(None, alias='IsTruncated')
    contents: List[Contents] = Field(None, alias='Contents')
    name: str = Field(None, alias='Name')
    prefix: str = Field(None, alias='Prefix')
    delimiter: str = Field(None, alias='Delimiter')
    max_keys: int = Field(None, alias='maxKeys')
    encoding_type: str = Field(None, alias='EncodingType')
    key_count: int = Field(None, alias='KeyCount')

    class Config:
        extra = Extra.ignore


class CopyObjectResult(BaseModel):
    etag: str = Field(None, alias='ETag')
    last_modified: Optional[datetime] = Field(None, alias='LastModified')


class CopyObject(BaseModel):
    copy_object_result: CopyObjectResult = Field(
        None, alias='CopyObjectResult'
    )
    expiration: str = Field(None, alias="Expiration")
    copy_source_version_id: str = Field(None, alias="CopySourceVersionId")
    version_id: str = Field(None, alias="VersionId")
    server_side_encryption: str = Field(None, alias="ServerSideEncryption")
    sse_customer_algorithm: str = Field(None, alias="SSECustomerAlgorithm")
    sse_customer_key_md5: str = Field(None, alias="SSECustomerKeyMD5")
    sse_kms_key_id: str = Field(None, alias="SSEKMSKeyId")
    sse_kms_encryption_context: str = Field(
        None, alias="SSEKMSEncryptionContext"
    )
    request_charged: str = Field(None, alias="RequestCharged")

    class Config:
        extra = Extra.ignore


class DeleteObject(BaseModel):
    delete_marker: str = Field(None, alias="DeleteMarker")
    version_id: str = Field(None, alias="VersionId")
    request_charged: str = Field(None, alias="RequestCharged")

    class Config:
        extra = Extra.ignore


class GetObject(BaseModel):
    body: bytes
    delete_marker: str = Field(None, alias="DeleteMarker")
    accept_ranges: str = Field(None, alias="AcceptRanges")
    expiration: str = Field(None, alias="Expiration")
    restore: str = Field(None, alias="Restore")
    last_modified: Optional[datetime] = Field(None, alias='LastModified')
    size: int = Field(None, alias="ContentLength")
    etag: str = Field(None, alias="ETag")
    missing_meta: str = Field(None, alias="MissingMeta")
    version_id: str = Field(None, alias="VersionId")
    cache_control: str = Field(None, alias="CacheControl")
    content_disposition: str = Field(None, alias="ContentDisposition")
    content_encoding: str = Field(None, alias="ContentEncoding")
    content_language: str = Field(None, alias="ContentLanguage")
    content_range: str = Field(None, alias="ContentRange")
    content_type: str = Field(None, alias="ContentType")
    expires: str = Field(None, alias="Expires")
    website_redirect_location: str = Field(
        None, alias="WebsiteRedirectLocation"
    )
    server_side_encryption: str = Field(None, alias="ServerSideEncryption")
    meta_data: Dict[str, str] = Field(None, alias="Metadata")
    sse_customer_algorithm: str = Field(None, alias="SSECustomerAlgorithm")
    sse_customer_key_md5: str = Field(None, alias="SSECustomerKeyMD5")
    sse_kms_key_id: str = Field(None, alias="SSEKMSKeyId")
    storage_class: str = Field(None, alias="StorageClass")
    request_charged: str = Field(None, alias="RequestCharged")
    replication_status: str = Field(None, alias="ReplicationStatus")
    parts_count: str = Field(None, alias="PartsCount")
    tag_count: str = Field(None, alias="TagCount")
    object_lock_mode: str = Field(None, alias="ObjectLockMode")
    object_lock_retain_until_date: str = Field(
        None, alias="ObjectLockRetainUntilDate"
    )
    object_lock_legal_hold_status: str = Field(
        None, alias="ObjectLockLegalHoldStatus"
    )
    bucket_name: str
    object_name: str

    class Config:
        extra = Extra.ignore
