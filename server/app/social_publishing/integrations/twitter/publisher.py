"""X (Twitter) publisher — publishes posts via the X API v2.

Implements the SocialPublisher protocol.
  - Text posts:  POST /2/tweets  {"text": "..."}
  - Media posts: upload each item via the X API v2 chunked media flow
                 (INIT → APPEND → FINALIZE → STATUS) to obtain media ids, then
                 POST /2/tweets with {"media": {"media_ids": [...]}}

The v2 media endpoint accepts the same OAuth 2.0 Bearer token used for posting.

Media handling is SSRF-safe: user-supplied media URLs are validated with
is_safe_url_async() before any outbound fetch, and the final URL is
re-validated after following redirects.

The publisher is stateless. It receives a user access token and a post.
"""

import asyncio

import httpx

from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.domain.models import PublishingResult, SocialPost
from app.social_publishing.integrations.twitter.constants import (
    CREATE_TWEET_URL,
    HTTP_TIMEOUT,
    MAX_MEDIA_ITEMS,
    MEDIA_CHUNK_SIZE,
    MEDIA_STATUS_MAX_WAIT,
    MEDIA_STATUS_POLL_INTERVAL,
    MEDIA_UPLOAD_URL,
    UPLOAD_TIMEOUT,
)
from app.social_publishing.integrations.twitter.errors import (
    classify_twitter_error,
    TwitterAPIError,
    TwitterErrorCode,
)
from app.social_publishing.integrations.twitter.media_validation import (
    validate_for_publishing,
)
from app.social_publishing.integrations.url_safety import is_safe_url_async
from app.services.logger import log


class TwitterPublisher:
    """Publishes content to X (Twitter) via the X API v2."""

    @property
    def platform_name(self) -> str:
        return SocialPlatform.TWITTER.value

    async def publish(self, post: SocialPost, access_token: str) -> PublishingResult:
        """
        Publish a post to X.

        Text-only posts are allowed. If media_urls are present, each is fetched
        (SSRF-checked) and uploaded to obtain media_ids attached to the post.
        """
        validation_errors = await self.validate_content(post)
        if validation_errors:
            return PublishingResult(
                success=False,
                error_message=f"Validation failed: {'; '.join(validation_errors)}",
                retryable=False,
            )

        try:
            media_ids: list[str] = []
            if post.media_urls:
                media_ids = await self._upload_all_media(
                    post.media_urls[:MAX_MEDIA_ITEMS], access_token
                )

            tweet_id = await self._create_tweet(post.content, media_ids, access_token)
            return PublishingResult(success=True, platform_post_id=tweet_id)

        except TwitterAPIError as e:
            return PublishingResult(
                success=False,
                error_message=e.message,
                retryable=e.retryable,
            )

    async def validate_content(self, post: SocialPost) -> list[str]:
        """Validate post content meets X requirements."""
        return validate_for_publishing(post.content, post.media_urls)

    # ── Create tweet ───────────────────────────────────────────────────────────

    async def _create_tweet(
        self, text: str, media_ids: list[str], access_token: str
    ) -> str:
        """POST /2/tweets. Returns the created tweet ID."""
        payload: dict = {}
        if text and text.strip():
            payload["text"] = text
        if media_ids:
            payload["media"] = {"media_ids": media_ids}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                CREATE_TWEET_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

        if response.status_code in (200, 201):
            data = response.json().get("data", {})
            tweet_id = data.get("id", "")
            if tweet_id:
                return tweet_id
            raise TwitterAPIError(
                code=TwitterErrorCode.UNKNOWN_PLATFORM_ERROR,
                message="X returned success but no tweet ID",
                retryable=True,
            )

        # Diagnostic: surface X's raw rejection reason (no auth header logged;
        # the response body carries X's own error detail, not our token).
        body = _safe_json(response)
        log.warning(
            "X tweet-create rejected",
            http_status=response.status_code,
            x_response=str(body)[:500],
            had_media=bool(media_ids),
        )
        raise classify_twitter_error(response.status_code, body)

    # ── Media upload ───────────────────────────────────────────────────────────

    async def _upload_all_media(
        self, media_urls: list[str], access_token: str
    ) -> list[str]:
        """Fetch and upload each media URL, returning the media_ids."""
        media_ids: list[str] = []
        for url in media_urls:
            media_id = await self._upload_media(url, access_token)
            media_ids.append(media_id)
        return media_ids

    async def _upload_media(self, media_url: str, access_token: str) -> str:
        """
        Fetch a media URL (SSRF-checked) and upload it via the X API v2 chunked
        media flow (INIT → APPEND → FINALIZE → STATUS), returning the media id.

        The v2 media endpoint accepts the same OAuth 2.0 Bearer token used for
        posting, so no separate OAuth 1.0a credentials are required.
        """
        media_bytes, content_type = await self._fetch_media(media_url)
        media_category = _media_category(content_type, media_url)

        # Step 1: INIT — declare total size and type, receive a media id
        media_id = await self._media_init(
            access_token, len(media_bytes), content_type, media_category
        )

        # Step 2: APPEND — upload the bytes in ordered segments
        await self._media_append(access_token, media_id, media_bytes)

        # Step 3: FINALIZE — signals upload complete; may return processing_info
        processing = await self._media_finalize(access_token, media_id)

        # Step 4: STATUS — poll only if X is processing the asset asynchronously
        if processing:
            await self._await_media_processing(access_token, media_id)

        return media_id

    async def _fetch_media(self, media_url: str) -> tuple[bytes, str]:
        """
        SSRF-checked fetch of the media bytes.

        Follows redirects (CDNs like Cloudinary/S3 commonly 302) but
        re-validates the FINAL resolved URL so a redirect cannot bypass the
        SSRF guard. Returns (bytes, content_type).
        """
        if not await is_safe_url_async(media_url):
            raise TwitterAPIError(
                code=TwitterErrorCode.INVALID_MEDIA,
                message="Unsafe media URL",
                retryable=False,
            )

        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(media_url)

        final_url = str(resp.url)
        if final_url != media_url and not await is_safe_url_async(final_url):
            raise TwitterAPIError(
                code=TwitterErrorCode.INVALID_MEDIA,
                message="Media URL redirected to an unsafe location",
                retryable=False,
            )

        if resp.status_code != 200:
            raise TwitterAPIError(
                code=TwitterErrorCode.INVALID_MEDIA,
                message=f"Could not fetch media (HTTP {resp.status_code})",
                retryable=False,
            )

        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        return resp.content, content_type

    # ── v2 chunked upload steps ─────────────────────────────────────────────────

    async def _media_init(
        self, access_token: str, total_bytes: int, content_type: str, category: str
    ) -> str:
        """INIT: reserve a media id for a chunked upload."""
        payload = {
            "command": "INIT",
            "total_bytes": total_bytes,
            "media_type": content_type or "application/octet-stream",
            "media_category": category,
        }
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                MEDIA_UPLOAD_URL,
                data=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code not in (200, 201, 202):
            body = _safe_json(resp)
            log.warning(
                "X media INIT rejected",
                http_status=resp.status_code,
                x_response=str(body)[:500],
                media_type=content_type,
                media_category=category,
            )
            raise classify_twitter_error(resp.status_code, body)

        media_id = _extract_media_id(_safe_json(resp))
        if not media_id:
            raise TwitterAPIError(
                code=TwitterErrorCode.INVALID_MEDIA,
                message="Media INIT returned no media id",
                retryable=True,
            )
        return media_id

    async def _media_append(
        self, access_token: str, media_id: str, media_bytes: bytes
    ) -> None:
        """APPEND: upload the media bytes as ordered segments."""
        segment = 0
        async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
            for start in range(0, len(media_bytes), MEDIA_CHUNK_SIZE):
                chunk = media_bytes[start : start + MEDIA_CHUNK_SIZE]
                resp = await client.post(
                    MEDIA_UPLOAD_URL,
                    data={
                        "command": "APPEND",
                        "media_id": media_id,
                        "segment_index": segment,
                    },
                    files={"media": chunk},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code not in (200, 201, 202, 204):
                    raise classify_twitter_error(resp.status_code, _safe_json(resp))
                segment += 1

    async def _media_finalize(self, access_token: str, media_id: str) -> bool:
        """
        FINALIZE: mark the upload complete.

        Returns True if X reports asynchronous processing_info (STATUS must be
        polled), False if the media is immediately usable.
        """
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                MEDIA_UPLOAD_URL,
                data={"command": "FINALIZE", "media_id": media_id},
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code not in (200, 201, 202):
            raise classify_twitter_error(resp.status_code, _safe_json(resp))

        data = _safe_json(resp)
        payload = data.get("data", data)
        return "processing_info" in payload

    async def _await_media_processing(self, access_token: str, media_id: str) -> None:
        """STATUS: poll until processing succeeds, or raise on failure/timeout."""
        elapsed = 0
        params = {"command": "STATUS", "media_id": media_id}
        while elapsed < MEDIA_STATUS_MAX_WAIT:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(
                    MEDIA_UPLOAD_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                )

            if resp.status_code == 200:
                data = _safe_json(resp)
                payload = data.get("data", data)
                info = payload.get("processing_info", {})
                state = info.get("state", "")
                if state == "succeeded":
                    return
                if state == "failed":
                    raise TwitterAPIError(
                        code=TwitterErrorCode.INVALID_MEDIA,
                        message="X failed to process the uploaded media",
                        retryable=False,
                    )

            await asyncio.sleep(MEDIA_STATUS_POLL_INTERVAL)
            elapsed += MEDIA_STATUS_POLL_INTERVAL

        raise TwitterAPIError(
            code=TwitterErrorCode.PLATFORM_UNAVAILABLE,
            message="Timed out waiting for X to process media",
            retryable=True,
        )


def _safe_json(response: httpx.Response) -> dict:
    """Best-effort JSON decode; falls back to a truncated text message."""
    try:
        return response.json()
    except Exception:
        return {"error": {"message": response.text[:300]}}


def _extract_media_id(body: dict) -> str:
    """Pull the media id from an INIT response (v2 nests it under 'data')."""
    payload = body.get("data", body) if isinstance(body, dict) else {}
    return (
        payload.get("id")
        or payload.get("media_id_string")
        or (str(payload.get("media_id")) if payload.get("media_id") else "")
    )


def _media_category(content_type: str, media_url: str) -> str:
    """
    Determine the X media_category for INIT.

    X expects one of tweet_image / tweet_video / tweet_gif for post media.
    """
    ct = (content_type or "").lower()
    url = (media_url or "").lower()
    if "gif" in ct or url.endswith(".gif"):
        return "tweet_gif"
    if ct.startswith("video/") or url.endswith((".mp4", ".mov", ".m4v")):
        return "tweet_video"
    return "tweet_image"
