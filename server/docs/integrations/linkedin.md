# LinkedIn Publishing Integration

> Source of truth: [LinkedIn Developer Documentation](https://learn.microsoft.com/en-us/linkedin/)
> Last verified: August 2026 (Posts API, versioned YYYYMM format)

## Supported Account Types

- **Members (personal profiles)** — can publish posts on their own behalf
- **Organizations (Company Pages)** — can publish posts when the authenticated member holds an eligible page role

### Organization Roles Required for Publishing

The authenticated member must hold one of these roles on the Organization:

| Role | Can Publish |
|------|-------------|
| ADMINISTRATOR | Yes |
| DIRECT_SPONSORED_CONTENT_POSTER | Yes |
| CONTENT_ADMIN | Yes |
| Other roles | No |

Trendzzo must verify the member's role on any organization before offering organization publishing capability.

## Authentication Flow

LinkedIn uses standard **OAuth 2.0 Authorization Code Flow** (3-legged OAuth).

### Endpoints

| Purpose | URL |
|---------|-----|
| Authorization | `https://www.linkedin.com/oauth/v2/authorization` |
| Token exchange | `https://www.linkedin.com/oauth/v2/accessToken` |
| API base | `https://api.linkedin.com/rest/` |

### OAuth Flow

1. User clicks connect in Trendzzo
2. Redirect to `https://www.linkedin.com/oauth/v2/authorization` with scopes + state
3. User authenticates and authorizes on LinkedIn
4. LinkedIn redirects back with authorization code (valid 30 minutes)
5. Trendzzo exchanges code for access token + refresh token
6. Tokens stored encrypted

### Token Lifecycle

- **Access token:** Valid for 60 days
- **Refresh token:** Longer lifespan (varies by app approval)
- **Programmatic refresh:** Available for approved partners via `grant_type=refresh_token`
- **Fallback refresh:** Re-run OAuth flow (bypasses consent screen if still logged in)

## Required Permissions (Scopes)

| Scope | Purpose |
|-------|---------|
| `openid` | OpenID Connect authentication |
| `profile` | Basic profile information |
| `email` | Email address |
| `w_member_social` | Post on behalf of the authenticated member |
| `w_organization_social` | Post on behalf of an organization (requires page role) |
| `r_organization_social` | Read organization posts (for verification) |

For Trendzzo member publishing, minimum scopes: `openid profile email w_member_social`

For organization publishing, additionally: `w_organization_social`

## Required App Configuration

1. LinkedIn Developer Application registered at developer.linkedin.com
2. OAuth 2.0 redirect URIs configured (HTTPS, absolute, no fragments)
3. Products enabled: "Share on LinkedIn" and/or "Sign In with LinkedIn using OpenID Connect"
4. For organization posting: "Community Management API" or "Marketing Developer Platform" access

## App Review Requirements

- **Share on LinkedIn** product: self-serve, grants `w_member_social`
- **Community Management API**: requires application review, grants `w_organization_social`
- **Marketing Developer Platform**: requires partnership approval for advanced features

## Supported Post Types (Posts API)

| Type | Member Publishing | Organization Publishing |
|------|-------------------|------------------------|
| Text only | Yes | Yes |
| Single image | Yes | Yes |
| Single video | Yes | Yes |
| Document (PDF) | Yes | Yes |
| Article (URL + thumbnail) | Yes | Yes |
| Multi-image | Yes (organic) | Yes (organic) |
| Poll | Yes (organic) | Yes (organic) |
| Carousel | No (sponsored only) | No (sponsored only) |

## Media Requirements

### Images (Images API)

- **Formats:** JPG, PNG, GIF (GIF up to 250 frames)
- **Size limit:** Less than 36,152,320 pixels
- **Upload flow:** Initialize upload → Upload binary → Reference image URN in post
- **Owner:** Must match post author (`urn:li:person:{id}` or `urn:li:organization:{id}`)

### Videos (Videos API)

- **Upload flow:** Initialize upload → Upload binary (potentially chunked) → Reference video URN
- **Processing:** Async — video goes through PROCESSING state before AVAILABLE

## Publishing Workflow (Posts API)

### Required Headers

```
Linkedin-Version: {YYYYMM}  (e.g., 202608)
X-Restli-Protocol-Version: 2.0.0
Authorization: Bearer {access_token}
```

### Text Post

```json
POST https://api.linkedin.com/rest/posts
{
  "author": "urn:li:person:{member_id}",
  "commentary": "Post content here",
  "visibility": "PUBLIC",
  "distribution": {
    "feedDistribution": "MAIN_FEED",
    "targetEntities": [],
    "thirdPartyDistributionChannels": []
  },
  "lifecycleState": "PUBLISHED",
  "isReshareDisabledByAuthor": false
}
```

### Image Post

1. Initialize image upload: `POST /rest/images?action=initializeUpload`
2. Upload binary to the returned `uploadUrl`
3. Create post with `content.media.id` = returned image URN

### Organization Post

Same as member post but `author` = `urn:li:organization:{org_id}`

Requires `w_organization_social` scope AND eligible page role.

## Known Limitations

1. **Carousel posts** are sponsored-only (not available for organic publishing)
2. **Rate limits** are per-application and per-member, reset at midnight UTC daily
3. **Exact rate limit numbers** are not publicly documented — discovered via 429 responses
4. **Authorization codes** expire after 30 minutes
5. **Access tokens** are ~500 characters (plan for up to 1000)
6. **CSRF state validation** is mandatory per LinkedIn security requirements
7. **Versioned API** — all requests require `Linkedin-Version` header in YYYYMM format
8. **Organization posting** requires separate scope AND verified page role
9. **Multi-scope consent** — user must accept all requested scopes (no partial grants)
10. **Image upload** requires initialize → upload binary flow (no URL-based upload)

## Rate Limits

| Scope | Limit |
|-------|-------|
| Application daily calls | ~100,000 (varies by product) |
| Member daily calls | Not publicly documented |
| 429 response | Retry after backoff; resets at midnight UTC |

LinkedIn does not publish exact per-endpoint rate limits. Trendzzo should:
- Implement exponential backoff on 429 responses
- Track per-member publishing frequency
- Enforce a conservative client-side daily limit (e.g., 20 posts/member/day)

## Token Requirements

| Requirement | Detail |
|-------------|--------|
| Token type | OAuth 2.0 Bearer |
| Required scope (member) | `w_member_social` |
| Required scope (org) | `w_organization_social` |
| Access token validity | 60 days |
| Refresh token | Available for approved apps |
| Storage | Encrypted at rest (Fernet) |

## Error Handling

| LinkedIn Error | HTTP Code | Trendzzo Mapping |
|----------------|-----------|-----------------|
| `EMPTY_ACCESS_TOKEN` | 401 | `AUTHENTICATION_REQUIRED` |
| `ACCESS_DENIED` | 403 | `PERMISSION_DENIED` |
| Insufficient org role | 403 | `ACCOUNT_NOT_ELIGIBLE` |
| `FIELD_LENGTH_TOO_LONG` | 400 | `INVALID_CONTENT` |
| `INVALID_URN_TYPE` | 400 | `INVALID_CONTENT` |
| Invalid image format | 400 | `INVALID_MEDIA` |
| `TOO_MANY_REQUESTS` | 429 | `RATE_LIMITED` |
| `SERVICE_UNAVAILABLE` | 503 | `PLATFORM_UNAVAILABLE` |
| `INTERNAL_SERVER_ERROR` | 500 | `UNKNOWN_PLATFORM_ERROR` |

## Account Eligibility Rules

A LinkedIn account is eligible for Trendzzo publishing if:

**For member publishing:**
1. User has granted `w_member_social` permission
2. Access token is valid and not expired

**For organization publishing (additional):**
3. User has granted `w_organization_social` permission
4. User holds ADMINISTRATOR, DIRECT_SPONSORED_CONTENT_POSTER, or CONTENT_ADMIN role on the target organization

Trendzzo must NOT assume that connecting a LinkedIn account grants organization publishing capability.

## References

- [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
- [Images API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api)
- [3-Legged OAuth Flow](https://learn.microsoft.com/en-us/linkedin/shared/authentication/authorization-code-flow)
- [Getting Access](https://learn.microsoft.com/en-us/linkedin/shared/authentication/getting-access)
- [Organization Access Control](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/organizations)

Content was rephrased for compliance with licensing restrictions.
