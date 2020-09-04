from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Extra, Field


class S3Model(BaseModel):
    response_metadata: Dict[str, Any] = Field(None, alias='ResponseMetadata')


class Owner(S3Model):
    display_name: Optional[str] = Field(None, alias='DisplayName')
    id: Optional[str] = Field(None, alias='ID')


class Contents(S3Model):
    key: Optional[str] = Field(None, alias='Key')
    last_modified: Optional[datetime] = Field(None, alias='LastModified')
    etag: Optional[str] = Field(None, alias='ETag')
    size: Optional[int] = Field(None, alias='Size')
    storage_class: Optional[str] = Field(None, alias='StorageClass')
    owner: Owner = Field(None, alias='Owner')


class ListObjects(S3Model):
    is_truncated: Optional[bool] = Field(None, alias='IsTruncated')
    contents: Optional[List[Contents]] = Field(None, alias='Contents')
    name: Optional[str] = Field(None, alias='Name')
    prefix: Optional[str] = Field(None, alias='Prefix')
    delimiter: Optional[str] = Field(None, alias='Delimiter')
    max_keys: Optional[int] = Field(None, alias='maxKeys')
    encoding_type: Optional[str] = Field(None, alias='EncodingType')
    key_count: Optional[int] = Field(None, alias='KeyCount')

    class Config:
        extra = Extra.ignore


class CopyObjectResult(S3Model):
    etag: Optional[str] = Field(None, alias='ETag')
    last_modified: Optional[datetime] = Field(None, alias='LastModified')


class CopyObject(S3Model):
    copy_object_result: CopyObjectResult = Field(
        None, alias='CopyObjectResult'
    )
    expiration: Optional[str] = Field(None, alias='Expiration')
    copy_source_version_id: Optional[str] = Field(
        None, alias='CopySourceVersionId'
    )
    version_id: Optional[str] = Field(None, alias='VersionId')
    server_side_encryption: Optional[str] = Field(
        None, alias='ServerSideEncryption'
    )
    sse_customer_algorithm: Optional[str] = Field(
        None, alias='SSECustomerAlgorithm'
    )
    sse_customer_key_md5: Optional[str] = Field(
        None, alias='SSECustomerKeyMD5'
    )
    sse_kms_key_id: Optional[str] = Field(None, alias='SSEKMSKeyId')
    sse_kms_encryption_context: Optional[str] = Field(
        None, alias='SSEKMSEncryptionContext'
    )
    request_charged: Optional[str] = Field(None, alias='RequestCharged')

    class Config:
        extra = Extra.ignore


class DeleteObject(S3Model):
    delete_marker: Optional[str] = Field(None, alias='DeleteMarker')
    version_id: Optional[str] = Field(None, alias='VersionId')
    request_charged: Optional[str] = Field(None, alias='RequestCharged')

    class Config:
        extra = Extra.ignore


class GetObject(S3Model):
    body: bytes
    delete_marker: Optional[str] = Field(None, alias='DeleteMarker')
    accept_ranges: Optional[str] = Field(None, alias='AcceptRanges')
    expiration: Optional[str] = Field(None, alias='Expiration')
    restore: Optional[str] = Field(None, alias='Restore')
    last_modified: Optional[datetime] = Field(None, alias='LastModified')
    size: Optional[int] = Field(None, alias='ContentLength')
    etag: Optional[str] = Field(None, alias='ETag')
    missing_meta: Optional[str] = Field(None, alias='MissingMeta')
    version_id: Optional[str] = Field(None, alias='VersionId')
    cache_control: Optional[str] = Field(None, alias='CacheControl')
    content_disposition: Optional[str] = Field(
        None, alias='ContentDisposition'
    )
    content_encoding: Optional[str] = Field(None, alias='ContentEncoding')
    content_language: Optional[str] = Field(None, alias='ContentLanguage')
    content_range: Optional[str] = Field(None, alias='ContentRange')
    content_type: Optional[str] = Field(None, alias='ContentType')
    expires: Optional[str] = Field(None, alias='Expires')
    website_redirect_location: Optional[str] = Field(
        None, alias='WebsiteRedirectLocation'
    )
    server_side_encryption: Optional[str] = Field(
        None, alias='ServerSideEncryption'
    )
    metadata: Dict[str, str] = Field(None, alias='Metadata')
    sse_customer_algorithm: Optional[str] = Field(
        None, alias='SSECustomerAlgorithm'
    )
    sse_customer_key_md5: Optional[str] = Field(
        None, alias='SSECustomerKeyMD5'
    )
    sse_kms_key_id: Optional[str] = Field(None, alias='SSEKMSKeyId')
    storage_class: Optional[str] = Field(None, alias='StorageClass')
    request_charged: Optional[str] = Field(None, alias='RequestCharged')
    replication_status: Optional[str] = Field(None, alias='ReplicationStatus')
    parts_count: Optional[str] = Field(None, alias='PartsCount')
    tag_count: Optional[str] = Field(None, alias='TagCount')
    object_lock_mode: Optional[str] = Field(None, alias='ObjectLockMode')
    object_lock_retain_until_date: Optional[str] = Field(
        None, alias='ObjectLockRetainUntilDate'
    )
    object_lock_legal_hold_status: Optional[str] = Field(
        None, alias='ObjectLockLegalHoldStatus'
    )
    bucket_name: str
    object_name: str

    class Config:
        extra = Extra.ignore


class Bucket(S3Model):
    name: Optional[str] = Field(None, alias='Name')
    creation_date: Optional[datetime] = Field(None, alias='CreationDate')

    class Config:
        extra = Extra.ignore
