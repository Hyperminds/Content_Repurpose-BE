from fastapi import HTTPException
from app.services.content_service import generate_text_content, get_batch_usage, _reset_batch
from app.services.image_service import generate_platform_images
from app.controllers.history_controller import add_history_entry
from app.services.moderation_service import check_content, flag_user, check_user_status
from app.services.ai_usage_service import log_generation, calculate_cost, AVAILABLE_MODELS
import time

DEFAULT_MODEL = "openai/gpt-4o-mini"
VALID_MODEL_IDS = {m["id"] for m in AVAILABLE_MODELS}


async def generate_content(request):
    body = await request.json()

    content = body.get("content")
    settings = body.get("settings", {})
    platform_prompts = body.get("platform_prompts", {})
    # Accept model from request — validate against allowed list
    requested_model = body.get("model", DEFAULT_MODEL)
    model_id = requested_model if requested_model in VALID_MODEL_IDS else DEFAULT_MODEL

    # Get user_id from auth header
    user_id = None
    organization_id = "default"
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            from app.utils.jwt_handler import decode_access_token
            token = auth_header.split(" ")[1]
            payload = decode_access_token(token)
            user_id = payload.get("user_id")
            organization_id = payload.get("organization_id") or payload.get("org_id") or user_id or "default"
        except Exception:
            pass

    if user_id:
        status = await check_user_status(user_id)
        if not status.get("allowed"):
            raise HTTPException(status_code=403, detail=status.get("reason", "Account restricted"))

    moderation_result = check_content(content)
    if not moderation_result["safe"]:
        flag_count = 0
        if user_id:
            flag_result = await flag_user(user_id, moderation_result["category"], content[:200])
            flag_count = flag_result.get("flag_count", 0)
            if flag_result["action"] == "suspended":
                raise HTTPException(status_code=403, detail="Your account has been suspended due to repeated policy violations.")
        raise HTTPException(status_code=400, detail=f"This request violates the platform's responsible content policy and cannot be processed.|{flag_count}")

    _reset_batch()
    start_time = time.time()
    generated_text = await generate_text_content(content, settings, platform_prompts, model_id=model_id)
    generation_time_ms = int((time.time() - start_time) * 1000)
    batch_usage = get_batch_usage()

    generated_images = await generate_platform_images(content)

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

    total_tokens = sum(u.get("total_tokens", 0) for u in batch_usage.values())
    total_cost = sum(
        calculate_cost(model_id, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        for u in batch_usage.values()
    )

    if user_id and batch_usage:
        history_id = history_entry.get("id") if history_entry else None
        for platform, usage in batch_usage.items():
            try:
                await log_generation(
                    user_id=user_id,
                    platform=platform,
                    model=model_id,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    generation_time_ms=generation_time_ms // 7,
                    content_preview=content[:80],
                    history_id=history_id,
                    organization_id=organization_id,
                )
            except Exception as e:
                print(f"Failed to log AI usage for {platform}: {e}")

    # Get model info for response
    model_info = next((m for m in AVAILABLE_MODELS if m["id"] == model_id), None)

    # ── Metering hook (additive, fail-safe) ──────────────────────────────────
    # Attach AI usage to the request so the global metering middleware records it.
    # Does not alter any business logic or the response.
    try:
        from app.utils.metering_utils import record_ai_usage
        _prompt = sum(u.get("prompt_tokens", 0) for u in batch_usage.values())
        _completion = sum(u.get("completion_tokens", 0) for u in batch_usage.values())
        record_ai_usage(request, model_id, _prompt, _completion, total_cost)
    except Exception:
        pass

    # In mock mode, simulate realistic usage data for display
    from app.config import USE_MOCK
    if USE_MOCK and total_tokens == 0:
        # Simulate typical token usage for 7 platforms
        total_tokens = 2840
        simulated_cost = calculate_cost(model_id, 1800, 1040)
        cost_display = f"~${simulated_cost:.5f} (mock)"
    else:
        simulated_cost = total_cost
        cost_display = f"${total_cost:.5f}" if total_cost > 0 else "$0.00000"

    return {
        "data": generated_text,
        "images": generated_images,
        "ai_usage": {
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(simulated_cost, 6),
            "estimated_cost_display": cost_display,
            "generation_time_ms": generation_time_ms,
            "model": model_id,
            "model_name": model_info["name"] if model_info else model_id,
            "is_mock": USE_MOCK,
            "by_platform": batch_usage,
        },
    }
