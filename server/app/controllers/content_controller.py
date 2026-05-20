from fastapi import HTTPException
from app.services.content_service import generate_text_content, get_batch_usage, _reset_batch
from app.services.image_service import generate_platform_images
from app.controllers.history_controller import add_history_entry
from app.services.moderation_service import check_content, flag_user, check_user_status
from app.services.ai_usage_service import log_generation, calculate_cost
import time


async def generate_content(request):
    body = await request.json()

    content = body.get("content")
    settings = body.get("settings", {})
    platform_prompts = body.get("platform_prompts", {})

    # Get user_id from auth header (optional - generation currently doesn't require auth)
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            from app.utils.jwt_handler import decode_access_token
            token = auth_header.split(" ")[1]
            payload = decode_access_token(token)
            user_id = payload.get("user_id")
        except Exception:
            pass

    # MODERATION CHECK: verify user isn't suspended
    if user_id:
        status = await check_user_status(user_id)
        if not status.get("allowed"):
            raise HTTPException(status_code=403, detail=status.get("reason", "Account restricted"))

    # MODERATION CHECK: scan input content
    moderation_result = check_content(content)
    if not moderation_result["safe"]:
        # Flag the user if authenticated
        flag_count = 0
        if user_id:
            flag_result = await flag_user(user_id, moderation_result["category"], content[:200])
            flag_count = flag_result.get("flag_count", 0)
            if flag_result["action"] == "suspended":
                raise HTTPException(
                    status_code=403,
                    detail="Your account has been suspended due to repeated policy violations.",
                )
        raise HTTPException(
            status_code=400,
            detail=f"This request violates the platform's responsible content policy and cannot be processed.|{flag_count}",
        )

    # GENERATE TEXT (with settings and platform prompts)
    _reset_batch()
    start_time = time.time()
    generated_text = await generate_text_content(content, settings, platform_prompts)
    generation_time_ms = int((time.time() - start_time) * 1000)
    batch_usage = get_batch_usage()

    # GENERATE IMAGES
    generated_images = await generate_platform_images(content)

    # SAVE TO HISTORY automatically
    history_entry = None
    try:
        history_entry = await add_history_entry(
            input_text=content,
            generated_data=generated_text,
            images=generated_images,
            settings=settings,
            user_id=user_id,
        )
    except Exception as e:
        print(f"Failed to save history: {e}")

    # LOG AI USAGE per platform
    if user_id and batch_usage:
        history_id = history_entry.get("id") if history_entry else None
        for platform, usage in batch_usage.items():
            try:
                await log_generation(
                    user_id=user_id,
                    platform=platform,
                    model="openai/gpt-4o-mini",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    generation_time_ms=generation_time_ms // 7,  # Approximate per-platform
                    content_preview=content[:80],
                    history_id=history_id,
                )
            except Exception as e:
                print(f"Failed to log AI usage for {platform}: {e}")

    # Build usage summary for response
    total_tokens = sum(u.get("total_tokens", 0) for u in batch_usage.values())
    total_cost = sum(
        calculate_cost("openai/gpt-4o-mini", u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        for u in batch_usage.values()
    )

    return {
        "data": generated_text,
        "images": generated_images,
        "ai_usage": {
            "total_tokens": total_tokens,
            "estimated_cost": round(total_cost, 6),
            "generation_time_ms": generation_time_ms,
            "model": "openai/gpt-4o-mini",
            "by_platform": batch_usage,
        },
    }
