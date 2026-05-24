"""
Campaign AI Chat Service.
Context-aware AI assistant that modifies selected campaign day content.
Understands campaign goals, pillars, tone, platform, and history.
"""

import json
from datetime import datetime, timezone
from bson import ObjectId
from openai import AsyncOpenAI
import os
from pathlib import Path
from dotenv import load_dotenv
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)
MODEL = "openrouter/free"

campaign_messages_collection = db["campaign_messages"]
campaign_content_collection  = db["campaign_content"]
campaign_days_collection     = db["campaign_days"]

PLATFORM_RULES = {
    "linkedin":  "Professional tone. No markdown. Short paragraphs. End with 5-8 relevant hashtags.",
    "twitter":   "Under 220 characters. Punchy. No markdown. 1-2 hashtags max.",
    "instagram": "Engaging caption. Emoji-friendly. End with 15-20 hashtags.",
    "reddit":    "Conversational. No hashtags. Authentic. Suggest 2 subreddits at end.",
    "medium":    "Article excerpt. Strong hook. Storytelling. End with 3-5 tags.",
    "meta":      "Conversational. Question-driven. 2-4 hashtags.",
    "quora":     "Expert answer format. First-person. No hashtags. Authoritative.",
}


async def chat_with_campaign_ai(
    user_id: str,
    campaign_id: str,
    day_id: str,
    user_message: str,
    campaign: dict,
    day: dict,
    current_content: str,
) -> dict:
    """
    Process a chat message and return AI response + optionally modified content.
    The AI understands full campaign context and only modifies the selected day.
    """
    from app.services.campaign_memory_service import get_memory, get_memory_context, record_chat_modification
    memory = await get_memory(user_id)
    memory_context = get_memory_context(memory)

    # Record the chat modification prompt
    await record_chat_modification(user_id, user_message)
    platform = day.get("platform", "linkedin")
    rules    = PLATFORM_RULES.get(platform, "")

    # Get recent chat history for context
    history = await get_chat_history(campaign_id, day_id, limit=6)
    history_text = ""
    for msg in history[-6:]:
        role = "User" if msg["role"] == "user" else "AI"
        history_text += f"{role}: {msg['content']}\n"

    system_prompt = f"""You are an expert campaign content strategist and AI writing assistant.
You are working inside Campaign Studio, helping optimize content for a specific campaign day.

CAMPAIGN CONTEXT:
- Campaign: {campaign['campaign_name']}
- Goal: {campaign['campaign_goal']}
- Type: {campaign['campaign_type']}
- Target Audience: {campaign['target_audience']}
- Tone: {campaign['tone']}
- CTA Goal: {campaign.get('cta_goal', '')}

CURRENT DAY CONTEXT:
- Platform: {platform}
- Content Type: {day.get('content_type', '')}
- Content Pillar: {day.get('content_pillar', '')}
- Purpose: {day.get('purpose', '')}
- Target Emotion: {day.get('target_emotion', '')}
- Day: {day.get('day_number', 1)} of {campaign.get('duration', 30)}

PLATFORM RULES: {rules}

{memory_context}

CURRENT CONTENT:
{current_content or "(no content yet)"}

RECENT CONVERSATION:
{history_text or "(no previous messages)"}

INSTRUCTIONS:
- You ONLY modify the selected day's content. Never regenerate the entire campaign.
- When the user asks to modify content, return the modified version.
- Keep all modifications campaign-context-aware.
- Maintain the platform rules and character limits.
- Be concise in explanations (1-2 sentences max).
- If the user asks a question, answer it directly.
- If the user asks to modify content, provide the modified content.

Return a JSON object:
{{
  "response": "Your conversational response to the user (1-3 sentences)",
  "modified_content": "The modified post content if user asked for changes, otherwise null",
  "action_taken": "Brief description of what you changed, or null if no change",
  "suggestions": ["Quick follow-up suggestion 1", "Quick follow-up suggestion 2"]
}}

Return ONLY valid JSON."""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.7,
        max_tokens=600,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    result = json.loads(raw)

    # Save user message
    await save_message(user_id, campaign_id, day_id, "user", user_message)

    # Save AI response
    await save_message(user_id, campaign_id, day_id, "assistant", result.get("response", ""), {
        "modified_content": result.get("modified_content"),
        "action_taken": result.get("action_taken"),
    })

    # If content was modified, update it in the database
    if result.get("modified_content"):
        await campaign_content_collection.update_one(
            {"day_id": day_id},
            {"$set": {
                "content": result["modified_content"],
                "updated_at": datetime.now(timezone.utc),
                "last_modified_by": "chat",
            }},
        )

    return {
        "response": result.get("response", ""),
        "modified_content": result.get("modified_content"),
        "action_taken": result.get("action_taken"),
        "suggestions": result.get("suggestions", []),
    }


async def save_message(user_id: str, campaign_id: str, day_id: str, role: str, content: str, metadata: dict = None):
    """Save a chat message to campaign_messages collection."""
    await campaign_messages_collection.insert_one({
        "user_id": user_id,
        "campaign_id": campaign_id,
        "day_id": day_id,
        "role": role,
        "content": content,
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc),
    })


async def get_chat_history(campaign_id: str, day_id: str, limit: int = 20) -> list:
    """Get chat history for a specific campaign day."""
    cursor = campaign_messages_collection.find({
        "campaign_id": campaign_id,
        "day_id": day_id,
    }).sort("created_at", 1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [
        {
            "id": str(d["_id"]),
            "role": d.get("role"),
            "content": d.get("content"),
            "metadata": d.get("metadata", {}),
            "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
        }
        for d in docs
    ]


async def clear_chat_history(campaign_id: str, day_id: str, user_id: str):
    """Clear chat history for a specific day."""
    await campaign_messages_collection.delete_many({
        "campaign_id": campaign_id,
        "day_id": day_id,
        "user_id": user_id,
    })
