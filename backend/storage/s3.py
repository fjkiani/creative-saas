"""
AWS S3 storage backend.
Activate via: STORAGE_BACKEND=s3
"""
import structlog
import boto3
from botocore.exceptions import ClientError
from backend.storage.base import StorageBackend
from backend.config import settings

log = structlog.get_logger(__name__)


class S3StorageBackend(StorageBackend):

    def __init__(self):
        self._s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        self._bucket = settings.s3_bucket_name

    def name(self) -> str:
        return f"s3/{self._bucket}"

    async def save(self, path: str, data: bytes, content_type: str = "image/png") -> str:
        log.info("s3.upload", bucket=self._bucket, path=path)
        self._s3.put_object(
            Bucket=self._bucket,
            Key=path,
            Body=data,
            ContentType=content_type,
            ACL="public-read",
        )
        return self.public_url(path)

    async def load(self, path: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=path)
        return response["Body"].read()

    async def exists(self, path: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=path)
            return True
        except ClientError:
            return False

    def public_url(self, path: str) -> str:
        return f"https://{self._bucket}.s3.{settings.aws_region}.amazonaws.com/{path}"
