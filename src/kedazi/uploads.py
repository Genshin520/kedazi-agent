"""只用普通 OSS AccessKey：上传私有图片，生成临时访问链接。"""
import asyncio
import io
import uuid
import warnings

import oss2
from PIL import Image, ImageOps

MAX_BYTES = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16_000_000


def normalize_image(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("请选择5MB以内的图片")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("仅支持 JPEG、PNG、WEBP")
                original.load()
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((2000, 2000))
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=90)
                return buffer.getvalue()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("图片无法读取或像素过大") from exc


class ImageStorage:
    def __init__(self, settings):
        self.settings = settings

    def get_bucket(self):
        s = self.settings
        if not s.oss_bucket or not s.oss_access_key_id.get_secret_value() or not s.oss_access_key_secret.get_secret_value():
            raise ValueError("请先在 .env 填写 OSS_BUCKET、OSS_ACCESS_KEY_ID、OSS_ACCESS_KEY_SECRET")
        auth = oss2.Auth(s.oss_access_key_id.get_secret_value(), s.oss_access_key_secret.get_secret_value())
        return oss2.Bucket(auth, s.oss_endpoint, s.oss_bucket, connect_timeout=20)

    async def upload(self, data):
        clean_image = await asyncio.to_thread(normalize_image, data)
        object_key = f"kedazi/{uuid.uuid4().hex}.jpg"
        bucket = self.get_bucket()
        await asyncio.to_thread(
            bucket.put_object, object_key, clean_image,
            headers={"Content-Type": "image/jpeg", "x-oss-object-acl": "private"},
        )
        return object_key

    async def signed_url(self, object_key):
        # 私有图片不能直接用普通URL读取。这只是临时下载链接，不需要额外凭据。
        return await asyncio.to_thread(self.get_bucket().sign_url, "GET", object_key, 300, slash_safe=True)
