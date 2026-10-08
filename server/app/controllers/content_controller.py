from fastapi import HTTPException
from app.services.content_service import generate_text_content, regenerate_platform, get_batch_usage, _reset_batch
from app.services.image_service import generate_platform_images
from app.controllers.history_controller import add_history_entry
from app.services.moderation_service import check_content, flag_user, check_user_status
from app.services.ai_usage_service import log_generation, calculate_cost, AVAILABLE_MODELS
import time

DEFAULT_MODEL = "openai/gpt-4o-mini"
VALID_MODEL_IDS = {m["id"] for m in AVAILABLE_MODELS}


def resolve_model_id(requested_model):
    """Resolve the model ID to use for generation.

    - Known built-in models are used as-is.
    - Custom agent models (user-supplied) are allowed through when they look
      like a valid provider/model identifier (e.g. "openai/gpt-4o"), so users
      can integrate their own models via the AI provider (OpenRouter).
    - Anything else falls back to the default model.
    """
    if not requested_model or not isinstance(requested_model, str):
        return DEFAULT_MODEL
    requested_model = requested_model.strip()
    if requested_model in VALID_MODEL_IDS:
        return requested_model
    # Plausible OpenRouter-style id: "provider/model", no spaces, reasonable length.
    if (
        "/" in requested_model
        and " " not in requested_model
        and 3 <= len(requested_model) <= 128
        and not requested_model.startswith("agent_")  # our local custom-agent local id, never a real model
    ):
        return requested_model
    return DEFAULT_MODEL


async def generate_content(request):
    body = await request.json()

    content = body.get("content")
    settings = body.get("settings", {})
    platform_prompts = body.get("platform_prompts", {})
    # Accept model from request — allow built-in + custom agent models
    requested_model = body.get("model", DEFAULT_MODEL)
    model_id = resolve_model_id(requested_model)

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
    from app.config import get_use_mock
    USE_MOCK = get_use_mock()
    if USE_MOCK and total_tokens == 0:
        # Simulate typical token usage for 8 platforms
        total_tokens = 3240
        simulated_cost = calculate_cost(model_id, 2050, 1190)
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


VALID_PLATFORMS = {"linkedin", "twitter", "instagram", "reddit", "medium", "meta", "quora", "threads"}


async def regenerate_platform_content(request):
    """Regenerate content for a SINGLE platform (used by the per-card Regenerate action)."""
    body = await request.json()

    content = body.get("content")
    platform = body.get("platform")
    settings = body.get("settings", {}) or {}
    platform_prompts = body.get("platform_prompts", {})
    requested_model = body.get("model", DEFAULT_MODEL)
    model_id = resolve_model_id(requested_model)

    # Optional per-regenerate instruction from the user (e.g. "make it funnier").
    # Merge it into settings.customInstructions so the prompt builder picks it up.
    custom_instruction = (body.get("custom_instruction") or "").strip()
    if custom_instruction:
        existing = (settings.get("customInstructions") or "").strip()
        settings = {
            **settings,
            "customInstructions": f"{existing}\n{custom_instruction}".strip() if existing else custom_instruction,
            "regenerateInstruction": custom_instruction,
        }

    if not content or not platform:
        raise HTTPException(status_code=400, detail="content and platform are required.")
    if platform not in VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

    # Auth (optional — mirrors /generate; used for user status + moderation)
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            from app.utils.jwt_handler import decode_access_token
            payload = decode_access_token(auth_header.split(" ")[1])
            user_id = payload.get("user_id")
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
    new_content = await regenerate_platform(platform, content, settings, platform_prompts, model_id=model_id)
    generation_time_ms = int((time.time() - start_time) * 1000)

    if new_content is None:
        raise HTTPException(status_code=502, detail="Regeneration failed. Please try again.")

    # Log usage for this single platform (best-effort)
    if user_id:
        try:
            batch_usage = get_batch_usage()
            usage = batch_usage.get(platform, {})
            await log_generation(
                user_id=user_id,
                platform=platform,
                model=model_id,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                generation_time_ms=generation_time_ms,
                content_preview=content[:80],
            )
        except Exception as e:
            print(f"Failed to log regenerate usage for {platform}: {e}")

    return {"platform": platform, "content": new_content}
