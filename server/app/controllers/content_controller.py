from content_repuposer_BE.server.app.services.content_service import (
    generate_text_content
)

from content_repuposer_BE.server.app.services.image_service import (
    generate_platform_images
)


async def generate_content(request):

    body = await request.json()

    content = body.get("content")

    # TEXT

    generated_text = await generate_text_content(
        content
    )

    # IMAGES

    generated_images = await generate_platform_images(
        content
    )

    return {

        "message":
        "Content Generated Successfully",

        "data": {

            "output": generated_text,

            "images": generated_images

        }

    }