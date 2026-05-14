from app.services.content_service import generate_text_content
from app.services.image_service import generate_platform_images
from app.controllers.history_controller import add_history_entry


async def generate_content(request):
    body = await request.json()

    content = body.get("content")
    settings = body.get("settings", {})
    platform_prompts = body.get("platform_prompts", {})

    # GENERATE TEXT (with settings and platform prompts)
    generated_text = await generate_text_content(content, settings, platform_prompts)

    # GENERATE IMAGES
    generated_images = await generate_platform_images(content)

    # SAVE TO HISTORY automatically
    try:
        await add_history_entry(
            input_text=content,
            generated_data=generated_text,
            images=generated_images,
            settings=settings,
        )
    except Exception as e:
        print(f"Failed to save history: {e}")

    return {
        "data": generated_text,
        "images": generated_images,
    }
