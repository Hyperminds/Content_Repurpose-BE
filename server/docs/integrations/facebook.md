# Facebook Publishing Integration

> Source of truth: [Meta Developer Documentation - Pages API](https://developers.facebook.com/docs/pages-api/getting-started)
> Last verified: August 2026 (Graph API v26.0)

## Critical Limitation: No Personal Profile Publishing

**Facebook does NOT support publishing to personal profiles via the API.**

The `publish_actions` permission was deprecated and removed. The Groups API was deprecated in v19. The only supported publishing target is **Facebook Pages**.

If a user connects their personal Facebook account but does not manage any Pages, Trendzzo must clearly indicate that publishing is unavailable for that connection.

## Supported Entities

| Entity | Publishing Supported | Notes |
|--------|---------------------|-------|
| Facebook Pages | Yes | Requires Page access token + CREATE_CONTENT task |
| Personal Profiles | No | API publishing removed; `publish_actions` deprecated |
| Groups | No | Groups API deprecated in Graph API v19 |
| Events | No | No publishing support |

## Authentication Flow

Facebook uses **Facebook Login for Business** (OAuth 2.0).

### Endpoints

| Purpose | URL |
|---------|-----|
| Authorization | `https://www.facebook.com/v26.0/dialog/oauth` |
| Token exchange | `https://graph.facebook.com/v26.0/oauth/access_token` |
| Long-lived token | `https://graph.facebook.com/v26.0/oauth/access_token` (with `grant_type=fb_exchange_token`) |
| Page tokens | `GET /v26.0/{user_id}/accounts` |
| Graph API base | `https://graph.facebook.com/v26.0` |

### OAuth Flow

1. User clicks connect in Trendzzo
2. Redirect to Facebook OAuth dialog with scopes
3. User authenticates and grants Page permissions
4. Facebook redirects back with authorization code
5. Trendzzo exchanges code for short-lived User access token
6. Exchange short-lived for long-lived User access token (60 days)
7. Retrieve Page access tokens via `/{user_id}/accounts`
8. Store encrypted Page access tokens (these do not expire if sourced from long-lived user token)

### Token Lifecycle

- **Short-lived User token:** ~1-2 hours
- **Long-lived User token:** 60 days
- **Page access token (from long-lived user token):** Does not expire
- **Page access token (from short-lived user token):** Same lifespan as source token

**Important:** Page tokens derived from a long-lived User access token are effectively permanent. However, they can be invalidated if the user revokes app access, changes password, or is removed from the Page.

## Required Permissions

| Permission | Purpose |
|------------|---------|
| `pages_manage_posts` | Create and manage Page posts |
| `pages_manage_metadata` | Manage Page settings and metadata |
| `pages_read_engagement` | Read Page post engagement data |
| `pages_show_list` | Show list of Pages user manages |

## Page Roles / Tasks Required

The authenticated user must be able to perform the **CREATE_CONTENT** task on the target Page. This requires one of:

- Page Admin
- Page Editor
- Content Creator role

The user's available tasks are returned in the `/{user_id}/accounts` response.

## Supported Post Types

| Type | Method | Notes |
|------|--------|-------|
| Text post | `POST /{page_id}/feed` with `message` | Plain text content |
| Link post | `POST /{page_id}/feed` with `message` + `link` | URL auto-scraped for preview |
| Photo post | `POST /{page_id}/photos` with `url` or file upload | Image published to Page |
| Video post | `POST /{page_id}/videos` | Resumable upload supported |
| Scheduled post | `POST /{page_id}/feed` with `scheduled_publish_time` | 10 min to 75 days in future |
| Unpublished post | `POST /{page_id}/feed` with `published=false` | For later promotion |

## Media Requirements

### Photos

- **Formats:** JPG, JPEG, GIF, PNG
- **Upload methods:** URL (`url` parameter) or binary file upload
- **No explicit size limit documented** but recommend < 10MB

### Videos

- **Upload:** Resumable Upload API for large files
- **Formats:** MP4 recommended
- **File upload endpoint:** `POST /{page_id}/videos`

### Links

- **Auto-scraped:** Facebook fetches Open Graph metadata from the URL
- **Custom preview:** Can override with `picture`, `name`, `description`
- **Link ownership:** Custom link images require link ownership verification

## Publishing Workflow

### Text/Link Post

```
POST /{page_id}/feed
  message: "Post content"
  link: "https://example.com" (optional)
  access_token: {page_access_token}
```

Returns: `{ "id": "{page_id}_{post_id}" }`

### Photo Post

```
POST /{page_id}/photos
  url: "https://example.com/image.jpg"
  caption: "Photo caption"
  access_token: {page_access_token}
```

Returns: `{ "id": "{photo_id}", "post_id": "{post_id}" }`

### Scheduled Post

```
POST /{page_id}/feed
  message: "Scheduled content"
  scheduled_publish_time: {unix_timestamp}
  published: false
  access_token: {page_access_token}
```

Schedule window: 10 minutes to 75 days from now.

## Known Limitations

1. **No personal profile publishing** — API publishing removed entirely
2. **No group publishing** — Groups API deprecated in v19
3. **Link ownership required** for custom link thumbnails
4. **~600 ranked posts per year** returned via read API
5. **Max 100 posts per read query** with `limit` field
6. **Expired posts** cannot be read via API
7. **Unpublished posts** only visible to Page admins
8. **Call-to-action buttons** require specific link structure
9. **Scheduled posts** must be 10 min to 75 days in the future

## Rate Limits

Facebook does not publish exact rate limits for the Pages API. General guidelines:

| Scope | Guidance |
|-------|----------|
| API calls per user per hour | ~200 (estimated, varies) |
| Posts per Page per day | No hard documented limit, but excessive posting may trigger review |
| Use system user tokens | Recommended to avoid per-user rate limiting |

Trendzzo should:
- Implement exponential backoff on 429/rate limit responses
- Enforce conservative client-side daily limits (e.g., 25 posts/Page/day)
- Use Page tokens (not user tokens) for publishing calls

## App Review Requirements

For production use with third-party users:

- `pages_manage_posts` — requires App Review
- `pages_manage_metadata` — requires App Review
- `pages_read_engagement` — requires App Review
- `pages_show_list` — requires App Review

All Page-related permissions require Meta App Review approval before use with non-app-role users.

## Token Requirements

| Requirement | Detail |
|-------------|--------|
| Token type | Page access token (derived from User token) |
| Source | `GET /{user_id}/accounts` after Facebook Login |
| Validity | Non-expiring (from long-lived User token) |
| Required task | CREATE_CONTENT on the Page |
| Storage | Encrypted at rest (Fernet) |

## Error Handling

| Facebook Error | HTTP Code | Trendzzo Mapping |
|----------------|-----------|-----------------|
| `OAuthException` | 401/403 | `AUTHENTICATION_REQUIRED` |
| `(#10) Application does not have permission` | 403 | `PERMISSION_DENIED` |
| User cannot perform CREATE_CONTENT | 403 | `ACCOUNT_NOT_ELIGIBLE` |
| No Pages available | N/A | `ACCOUNT_NOT_ELIGIBLE` |
| Invalid photo format | 400 | `INVALID_MEDIA` |
| Message too long / invalid | 400 | `INVALID_CONTENT` |
| Rate limit exceeded | 429 | `RATE_LIMITED` |
| Graph API unavailable | 500/503 | `PLATFORM_UNAVAILABLE` |
| Unknown error | 5xx | `UNKNOWN_PLATFORM_ERROR` |

## Account Eligibility Rules

A Facebook connection is eligible for Trendzzo publishing if ALL of the following are true:

1. User authenticated via Facebook Login with required permissions
2. User manages at least one Facebook Page
3. User has the **CREATE_CONTENT** task on that Page
4. A valid Page access token was retrieved and stored

**If the user has no Pages or cannot perform CREATE_CONTENT on any Page:**
- The account is connected (for other potential features)
- Publishing capability is NOT granted
- User sees a clear message: "Publishing requires a Facebook Page with content creation permissions"

## References

- [Pages API Getting Started](https://developers.facebook.com/docs/pages-api/getting-started)
- [Page Feed Reference](https://developers.facebook.com/docs/graph-api/reference/page/feed/)
- [Facebook Login for Business](https://developers.facebook.com/docs/facebook-login/guides/advanced/manual-flow)
- [Page Post Reference](https://developers.facebook.com/docs/graph-api/reference/pagepost)
- [Video API Publishing](https://developers.facebook.com/docs/video-api/guides/publishing/)

Content was rephrased for compliance with licensing restrictions.
