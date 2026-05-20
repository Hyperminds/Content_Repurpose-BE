"""
Image service - generates consistent platform images.
Uses seeded picsum URLs so the same content always gets the same image.
This ensures the image shown on the card matches the one uploaded/downloaded.
"""

import hashlib


async def generate_platform_images(content):
    """
    Generate deterministic image URLs based on content hash.
    Same content = same images every time = no mismatch between display and upload.
    """
    # Create a hash seed from the content so images are consistent
    seed = int(hashlib.md5(content.encode()).hexdigest()[:8], 16)

    return {
        "linkedin": f"https://picsum.photos/seed/{seed + 1}/1200/630",
        "twitter": f"https://picsum.photos/seed/{seed + 2}/1200/675",
        "instagram": f"https://picsum.photos/seed/{seed + 3}/1080/1080",
        "reddit": f"https://picsum.photos/seed/{seed + 4}/1200/630",
        "medium": f"https://picsum.photos/seed/{seed + 5}/1200/630",
        "meta": f"https://picsum.photos/seed/{seed + 6}/1200/630",
        "quora": f"https://picsum.photos/seed/{seed + 7}/1200/630",
    }
