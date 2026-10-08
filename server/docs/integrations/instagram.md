# Instagram Publishing Integration

> Source of truth: [Meta Developer Documentation - Instagram Platform](https://developers.facebook.com/docs/instagram-platform/)
> Last verified: August 2026 (Graph API v26.0)

## Supported Account Types

Only **Instagram Professional accounts** are eligible for API publishing:

- **Business accounts** — connected to a Facebook Page
- **Creator accounts** — connected to a Facebook Page

Personal Instagram accounts **cannot** publish via the API. Trendzzo must detect account type during connection and clearly indicate when publishing is not available.

**Page Publishing Authorization (PPA):** An Instagram professional account connected to a Page that requires PPA cannot be published to until PPA has been completed. There is no API to determine if PPA is required — users should be advised to complete it proactively.

## Authentication Flow

Trendzzo uses **Business Login for Instagram** (Instagram API with Instagram Login) which provides a direct Instagram OAuth path without requiring Facebook Page tokens.

### Endpoints

| Purpose | URL |
|---------|-----|
| Authorization | `https://www.instagram.com/oauth/authorize` |
| Token exchange | `https://api.instagram.com/oauth/access_token` |
| Long-lived token exchange | `https://graph.instagram.com/access_token` |
| Token refresh | `https://graph.instagram.com/refresh_access_token` |
| Content publishing host | `https://graph.instagram.com` |

### OAuth Flow

1. User clicks connect in Trendzzo
2. Redirect to `https://www.instagram.com/oauth/authorize` with scopes
3. User authorizes on Instagram
4. Instagram redirects back with authorization code
5. Trendzzo exchanges code for short-lived token (1 hour)
6. Trendzzo exchanges short-lived token for long-lived token (60 days)
7. Token stored encrypted; refresh scheduled before expiry

### Token Lifecycle

- **Short-lived token:** Valid for 1 hour after exchange
- **Long-lived token:** Valid for 60 days
- **Refresh:** Can be refreshed if token is at least 24 hours old and not expired
- **Expiry:** Tokens not refreshed within 60 days expire permanently (re-auth required)

## Required Permissions (Scopes)

For Instagram API with Instagram Login:

- `instagram_business_basic` — required for account access
- `instagram_business_content_publish` — required for publishing

> The old scope values (`business_basic`, `business_content_publish`) were deprecated on January 27, 2025.

## Required App Configuration

1. Meta App with Instagram product added
2. Instagram App ID and App Secret configured in App Dashboard
3. Valid OAuth Redirect URIs registered
4. Business Login for Instagram enabled in App Dashboard
5. Webhooks server (recommended for status notifications)

## App Review Requirements

- **Standard Access:** For accounts you own/manage (added to app in Dashboard)
- **Advanced Access:** Required for serving third-party Instagram accounts (App Review required)

For production deployment serving arbitrary users, Trendzzo needs Advanced Access approval for:
- `instagram_business_basic`
- `instagram_business_content_publish`

## Supported Media Types

| Type | Parameter | Notes |
|------|-----------|-------|
| Single Image | `image_url` | JPEG only. Must be publicly accessible URL. |
| Single Video | `video_url` + `media_type=VIDEO` | MP4 recommended. Must be publicly accessible. |
| Reels | `video_url` + `media_type=REELS` | Short-form video for Reels tab. |
| Stories | `media_type=STORIES` | Image or video. 24-hour visibility. |
| Carousel | `media_type=CAROUSEL` + `children` | Up to 10 images/videos combined. |

### Image Requirements

- **Format:** JPEG only (MPO, JPS not supported)
- **Hosting:** Must be on a publicly accessible URL (Meta fetches via cURL)
- **Aspect ratio:** Carousel images cropped based on first image (default 1:1)

### Video Requirements

- **Format:** MP4 recommended
- **Hosting:** Public URL or resumable upload via `rupload.facebook.com`
- **Processing:** Videos go through an IN_PROGRESS state before FINISHED

### Unsupported

- Shopping tags
- Filters
- Extended JPEG formats (MPO, JPS)

## Publishing Workflow

Instagram uses a **two-step container-based** publishing process:

### Step 1: Create Media Container

```
POST /{ig_user_id}/media
  image_url or video_url
  caption (optional)
  media_type (for video/reels/stories/carousel)
```

Returns: `{ "id": "<container_id>" }`

### Step 2: Publish Container

```
POST /{ig_user_id}/media_publish
  creation_id = <container_id>
```

Returns: `{ "id": "<media_id>" }`

### Container Status Checking

For videos, poll the container status before publishing:

```
GET /{container_id}?fields=status_code
```

Status values: `EXPIRED`, `ERROR`, `FINISHED`, `IN_PROGRESS`, `PUBLISHED`

Recommendation: Poll once per minute, max 5 minutes.

## Known Limitations

1. **Professional accounts only** — personal accounts cannot publish via API
2. **JPEG only** for images — no PNG, WebP, GIF support
3. **Public media URLs required** — Meta fetches media server-side
4. **No text-only posts** — Instagram requires at least one image or video
5. **No scheduled publishing via API** — scheduling must be managed by Trendzzo
6. **No direct file upload for images** — images must be hosted on public URL
7. **Page Publishing Authorization** may block publishing with no API detection
8. **Container expiry** — unpublished containers expire after 24 hours
9. **Carousel crop** — all carousel images cropped to match first image aspect ratio

## Rate Limits

| Limit | Value |
|-------|-------|
| API-published posts per 24h (Instagram Login) | 100 |
| API-published posts per 24h (Graph API) | 25-50 (varies) |
| Carousel counts as | 1 post |

Check current usage: `GET /{ig_user_id}/content_publishing_limit`

Trendzzo should enforce rate limits client-side before attempting to publish.

## Token Requirements

| Requirement | Detail |
|-------------|--------|
| Token type | Instagram User access token (long-lived) |
| Required scope | `instagram_business_content_publish` |
| Validity | 60 days (refreshable) |
| Refresh window | After 24h, before 60 days |
| Storage | Encrypted at rest (Fernet) |

## Error Handling

| Meta Error | Trendzzo Mapping |
|------------|-----------------|
| `OAuthException` / 401 | `AUTHENTICATION_REQUIRED` |
| `(#10) Permission denied` | `PERMISSION_DENIED` |
| Account not professional | `ACCOUNT_NOT_ELIGIBLE` |
| Invalid image format | `INVALID_MEDIA` |
| Container EXPIRED/ERROR | `INVALID_MEDIA` |
| Content policy violation | `INVALID_CONTENT` |
| 429 / rate limit | `RATE_LIMITED` |
| 503 / service unavailable | `PLATFORM_UNAVAILABLE` |
| Unknown 5xx | `UNKNOWN_PLATFORM_ERROR` |

## Account Eligibility Rules

An Instagram account is eligible for Trendzzo publishing if ALL of the following are true:

1. Account is a **Professional account** (Business or Creator)
2. User has granted `instagram_business_content_publish` permission
3. Account does not require incomplete Page Publishing Authorization
4. Token is valid and not expired

Trendzzo must NOT assume all connected Instagram accounts can publish. The connection flow must:
- Verify account type
- Check granted permissions
- Store detected capabilities
- Display clear status to the user

## References

- [Content Publishing Guide (Instagram Login)](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing)
- [Business Login for Instagram](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login)
- [Instagram Platform Overview](https://developers.facebook.com/docs/instagram-platform/)
- [Instagram Platform Changelog](https://developers.facebook.com/docs/instagram-platform/changelog)

Content was rephrased for compliance with licensing restrictions.
