"""
Content generation service with AI usage tracking.
Captures token usage from every OpenAI API call.
In development mode (APP_ENV=development), returns mock content without any API calls.
"""

import os
import asyncio
import time
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv
from app.config import USE_MOCK
from app.mock_data.content_generation import get_mock_content

# Load .env from the app directory regardless of working directory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

MODEL = "openrouter/free"

# Global token accumulator for the current generation batch
_current_batch_usage = {}


def _reset_batch():
    global _current_batch_usage
    _current_batch_usage = {}


def _record_usage(platform: str, usage):
    """Record token usage for a platform call."""
    if usage:
        _current_batch_usage[platform] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }


def get_batch_usage() -> dict:
    """Get accumulated token usage for the current batch."""
    return dict(_current_batch_usage)


# ---------------- PROMPT BUILDER ---------------- #

def build_system_prompt(platform, settings, platform_prompts):
    """
    Converts frontend settings + platform-specific prompts into a system prompt.
    """
    tone = settings.get("tone", "Professional")
    length = settings.get("length", "Medium")
    seo = settings.get("seo", True)
    audience = settings.get("audience", "Developers")
    creativity = settings.get("creativity", 7)
    custom_instructions = settings.get("customInstructions", "")

    # Get the active platform-specific prompt (if any)
    platform_data = platform_prompts.get(platform, {})
    platform_custom_prompt = platform_data.get("active", "")

    # Map creativity to temperature description
    if int(creativity) <= 3:
        creativity_desc = "conservative and safe"
    elif int(creativity) <= 6:
        creativity_desc = "balanced between creative and conventional"
    else:
        creativity_desc = "highly creative and original"

    # Length guidance
    length_map = {
        "Short": "Keep it concise and brief. Prioritize impact over length.",
        "Medium": "Use a moderate length. Cover the key points without being too verbose.",
        "Long": "Be thorough and detailed. Expand on ideas with examples and depth.",
    }
    length_guidance = length_map.get(length, length_map["Medium"])

    # Build the system prompt
    system_parts = [
        f"You are a {platform} content creation expert.",
        f"Tone: {tone}.",
        f"Target audience: {audience}.",
        f"Length: {length_guidance}",
        f"Writing style: Be {creativity_desc}.",
        "CRITICAL FORMATTING RULE: Never use markdown formatting in your output. "
        "No asterisks (*), no hashtag headers (#), no bold (**), no italic (*), no bullet points with dashes. "
        "Write in plain text only. Use line breaks for structure. "
        "Hashtags for social media (like #AI #Tech) are fine, but never use # as a header marker.",
    ]

    if seo:
        system_parts.append(
            "SEO Optimization: Include relevant keywords naturally. "
            "Use hashtags strategically for discoverability."
        )

    if custom_instructions.strip():
        system_parts.append(f"Additional instructions: {custom_instructions}")

    if platform_custom_prompt.strip():
        system_parts.append(
            f"Platform-specific instructions from user: {platform_custom_prompt}"
        )

    return "\n".join(system_parts)


def get_temperature(creativity):
    """Map creativity (1-10) to OpenAI temperature (0.3 - 1.2)."""
    creativity = int(creativity)
    return round(0.3 + (creativity - 1) * (0.9 / 9), 2)


# ---------------- LINKEDIN ---------------- #

async def generate_linkedin(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("linkedin", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the single most powerful insight.

Write a LinkedIn post that STOPS the scroll immediately.

STRUCTURE (follow exactly):
Line 1: A bold, provocative opening hook — ONE sentence that creates instant curiosity or challenges a common belief. No fluff. No "I want to share..." openers.
[blank line]
Lines 2-6: The core insight broken into short, punchy paragraphs. Each paragraph max 2 lines. Use numbered lists or arrows (→) where it adds clarity.
[blank line]
Final line: A direct, specific CTA — ask a question that invites real opinions.
[blank line]
Hashtags: 5-7 highly specific, trending hashtags relevant to the exact topic (NOT generic like #content #post #linkedin)

REQUIREMENTS:
- Hook must be the strongest sentence in the post
- Write like a real person, not a corporate account
- Short paragraphs — mobile readers skim
- Emotionally intelligent, not motivational-poster generic
- No "In today's world..." or "I'm excited to share..." openers ever

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=550
    )

    _record_usage("linkedin", response.usage)
    return response.choices[0].message.content


# ---------------- TWITTER/X ---------------- #

async def generate_twitter(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("twitter", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the single most shareable, provocative insight.

Write ONE high-performing tweet. Output ONLY the tweet text — no labels, no quotes, no explanation.

RULES:
- Maximum 260 characters total (including hashtags)
- First 8 words must be a scroll-stopping hook — use curiosity, controversy, or a bold claim
- Conversational and punchy — sounds like a real person, not a brand
- End with 3-5 trending, specific hashtags relevant to the exact topic
- Hashtags must be currently trending or highly searched (e.g. #AIAgents #GPT5 #BuildInPublic #StartupLife)
- NO generic hashtags like #tech #content #post #socialmedia
- NO "TWEET:" prefix, no numbering, no thread format
- If the insight is strong enough, a question format works well

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=150
    )

    tweet = response.choices[0].message.content.strip()
    _record_usage("twitter", response.usage)
    tweet = tweet.replace("TWEET:", "").replace("Tweet:", "").strip()
    # Strip surrounding quotes if model adds them
    if tweet.startswith('"') and tweet.endswith('"'):
        tweet = tweet[1:-1].strip()
    if len(tweet) > 280:
        tweet = tweet[:277] + "..."
    return tweet


# ---------------- INSTAGRAM ---------------- #

async def generate_instagram(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("instagram", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the most visually compelling, emotionally resonant angle.

Write an Instagram caption that stops the scroll and drives saves.

STRUCTURE:
Line 1: A powerful hook — one sentence that creates instant curiosity, emotion, or relatability. This is the most important line.
[blank line]
Lines 2-5: The story or insight in short, punchy sentences. Max 2 lines per paragraph.
[blank line]
CTA: End with a direct, specific question or action that invites engagement.
[blank line]
Hashtags: 20-25 hashtags — mix of high-volume (#AI #Entrepreneur) and niche (#AIStartups #ContentCreatorLife). All must be relevant to the actual topic.

REQUIREMENTS:
- Hook must create an emotional reaction in under 5 seconds
- Conversational, creator-style voice — not corporate
- Short paragraphs — Instagram is mobile-first
- The CTA must be specific, not generic ("Drop a comment" is weak — "Tell me your biggest challenge with X" is strong)

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=400
    )

    _record_usage("instagram", response.usage)
    return response.choices[0].message.content


# ---------------- REDDIT ---------------- #

async def generate_reddit(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("reddit", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the most discussion-worthy angle.

Write a Reddit post that gets upvoted and sparks real conversation.

STRUCTURE:
Title: Write a compelling, specific title that makes people want to click (max 120 chars). Use curiosity or a strong opinion.
[blank line]
Body: 150-250 words maximum. Reddit readers abandon long posts.

BODY REQUIREMENTS:
- Open with a strong hook — a surprising fact, personal experience, or bold opinion in the first 2 sentences
- Write like a real Reddit user — casual, honest, slightly imperfect
- Share a genuine perspective or experience, not a lecture
- Ask the community a specific question at the end to drive comments
- NO hashtags (Reddit does not use hashtags)
- NO corporate tone, NO "I'm excited to share"
- NO subreddit suggestions — do not mention r/ anything

AVOID:
- Walls of text
- Bullet point lists (feels like a blog post, not Reddit)
- Promotional language
- Subreddit recommendations

Output format:
Title: [your title here]

[body text here]

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=400
    )

    _record_usage("reddit", response.usage)
    content = response.choices[0].message.content
    # Strip any subreddit suggestions the model might still add
    import re
    content = re.sub(r'(?i)(best subreddits?|posted? (to|in|on)|r/\w+)[^\n]*', '', content).strip()
    return content


# ---------------- MEDIUM ---------------- #

async def generate_medium(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("medium", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the most intellectually compelling angle.

Write the opening section of a Medium article that makes readers unable to stop reading.

STRUCTURE:
Headline: A specific, curiosity-driven title (not clickbait — genuinely interesting)
[blank line]
Opening paragraph: 2-3 sentences that immediately challenge a common assumption or open with a surprising fact/story. This is your hook — make it impossible to ignore.
[blank line]
Body (2-3 short paragraphs): Develop the core idea with depth and nuance. Use a conversational but authoritative voice. Short paragraphs — Medium readers skim.
[blank line]
Cliffhanger: End with a thought-provoking question or statement that makes readers want to continue.
[blank line]
Tags: 4-5 specific tags (format: Tags: tag1, tag2, tag3)

REQUIREMENTS:
- The headline must be specific — "Why Most Developers Ignore the Most Important Skill" beats "The Importance of Skills"
- Opening hook must challenge something the reader believes or surprise them
- Intellectual depth without academic jargon
- No "In this article I will..." openers

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=600
    )

    _record_usage("medium", response.usage)
    return response.choices[0].message.content


# ---------------- META (Facebook) ---------------- #

async def generate_meta(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("meta", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the most relatable, discussion-worthy angle.

Write a Facebook post that sparks genuine conversation.

STRUCTURE:
Line 1: A hook that feels personal — a relatable observation, a bold opinion, or a surprising statement. Must make someone stop scrolling.
[blank line]
Lines 2-4: Develop the idea in 2-3 short paragraphs. Conversational, warm, community-focused.
[blank line]
Final line: A specific question that invites real responses — not "What do you think?" but something specific to the topic.
[blank line]
2-3 relevant hashtags (specific to the topic, not generic)

REQUIREMENTS:
- Feels like a real person sharing a genuine thought, not a brand post
- Short paragraphs — Facebook is mobile-first
- The question at the end must be specific enough that people feel compelled to answer
- No promotional language, no links, no "Check out my..."

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=400
    )

    _record_usage("meta", response.usage)
    return response.choices[0].message.content


# ---------------- QUORA ---------------- #

async def generate_quora(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("quora", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below and extract the most searchable, valuable insight.

Write a Quora answer that gets upvoted and ranks on Google.

STRUCTURE:
Question: Write a specific, searchable question that this content answers (max 100 chars). Should be something people actually Google.
[blank line]
Answer opening: Start with a direct, confident answer to the question in 1-2 sentences. Don't build up to the answer — give it immediately.
[blank line]
Body (3-4 short paragraphs): Expand with depth, personal experience framing, and specific examples. Use "In my experience..." or "I've found that..." naturally.
[blank line]
Takeaway: End with one clear, actionable insight the reader can apply today.

REQUIREMENTS:
- The opening must answer the question directly — Quora readers hate when answers bury the lead
- Use first-person authority voice — you are an expert sharing real experience
- Specific examples and data points increase credibility
- No hashtags (Quora doesn't use them)
- No subreddit or platform suggestions
- 200-350 words total for the answer

Source Content:
{source_content}
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature,
        max_tokens=500
    )

    _record_usage("quora", response.usage)
    return response.choices[0].message.content


# ---------------- MAIN FUNCTION ---------------- #

async def generate_text_content(source_content, settings=None, platform_prompts=None):

    if settings is None:
        settings = {}
    if platform_prompts is None:
        platform_prompts = {}

    # ── DEVELOPMENT MODE: return mock content instantly ──────────────────────
    if USE_MOCK:
        return get_mock_content(source_content, settings, platform_prompts)

    # ── PRODUCTION MODE: call real AI APIs ───────────────────────────────────
    # Generate all platforms in parallel
    try:
        linkedin, twitter, instagram, reddit, medium, meta, quora = await asyncio.gather(
            generate_linkedin(source_content, settings, platform_prompts),
            generate_twitter(source_content, settings, platform_prompts),
            generate_instagram(source_content, settings, platform_prompts),
            generate_reddit(source_content, settings, platform_prompts),
            generate_medium(source_content, settings, platform_prompts),
            generate_meta(source_content, settings, platform_prompts),
            generate_quora(source_content, settings, platform_prompts),
        )

        return {
            "linkedin": linkedin,
            "twitter": twitter,
            "instagram": instagram,
            "reddit": reddit,
            "medium": medium,
            "meta": meta,
            "quora": quora,
        }

    except Exception as e:

        print("========== TEXT GENERATION ERROR ==========")
        print(type(e))
        print(str(e))
        print("==========================================")

        return None
