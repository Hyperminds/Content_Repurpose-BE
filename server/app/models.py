"""
MongoDB document schemas (for reference).
MongoDB is schema-less, but these define the expected structure.

Bookmark:
{
    "_id": ObjectId,
    "platform": "linkedin" | "twitter" | "instagram" | "reddit",
    "content": "The generated content text",
    "input_text": "Original user input",
    "image_url": "URL to generated image",
    "note": "User's note",
    "created_at": datetime
}

History:
{
    "_id": ObjectId,
    "input_text": "Original user input",
    "generated_data": {"linkedin": "...", "twitter": "...", ...},
    "images": {"linkedin": "url", "twitter": "url", ...},
    "settings_snapshot": {...settings at generation time...},
    "created_at": datetime
}
"""
