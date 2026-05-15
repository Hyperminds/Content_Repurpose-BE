import os
import asyncio
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load .env from the app directory regardless of working directory
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

MODEL = "openai/gpt-4o-mini"


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
Analyze the source content below.
Extract the most valuable insight.

Generate a LinkedIn post optimized for:
- authority and trust
- engagement and profile visits
- meaningful comments

Requirements:
- professional storytelling
- emotionally intelligent writing
- natural human tone
- mobile-friendly formatting (short paragraphs)
- subtle CTA
- At the end, include 5-8 content-specific hashtags that are relevant to the actual topic discussed (not generic ones like #content or #post)

Avoid:
- robotic AI phrasing
- corporate jargon
- fake motivation

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

    return response.choices[0].message.content


# ---------------- TWITTER/X ---------------- #

async def generate_twitter(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("twitter", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below.
Extract the most interesting and shareable insight.

Generate:
1 viral tweet
AND
1 Twitter/X thread (max 5 tweets).

Requirements:
- curiosity-driven hooks
- punchy writing
- conversational tone
- emotionally engaging
- highly shareable
- optimized for replies and quote tweets
- natural flow with short impactful sentences
- Include 2-4 content-specific hashtags relevant to the actual topic (not generic ones)

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
        max_tokens=450
    )

    return response.choices[0].message.content


# ---------------- INSTAGRAM ---------------- #

async def generate_instagram(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("instagram", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below.

Generate:
1 Instagram caption.

Requirements:
- emotionally engaging
- creator-style tone
- emoji friendly
- relatable storytelling
- CTA at the end
- Include 15-25 content-specific hashtags at the very end, relevant to the actual topic (mix of high-volume and niche hashtags)

Optimize for:
- saves
- shares
- emotional engagement

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
        max_tokens=350
    )

    return response.choices[0].message.content


# ---------------- REDDIT ---------------- #

async def generate_reddit(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("reddit", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below.

Generate:
1 detailed Reddit post.

Requirements:
- conversational tone
- honest human writing
- value-driven
- authentic discussion style
- no hashtags (Reddit doesn't use them)
- no corporate tone
- slightly imperfect natural writing
- At the end, suggest 2-3 relevant subreddits where this post would fit well (format: "Best subreddits: r/example, r/example2")

Optimize for:
- trust
- comments
- relatability

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
        max_tokens=700
    )

    return response.choices[0].message.content


# ---------------- MEDIUM ---------------- #

async def generate_medium(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("medium", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below.

Generate:
1 Medium article excerpt (opening section that hooks readers to continue reading).

Requirements:
- compelling headline
- strong opening hook (first 2 sentences must grab attention)
- storytelling approach
- intellectual depth
- conversational yet authoritative
- formatted for readability (short paragraphs, clear flow)
- end with a thought-provoking question or cliffhanger to encourage reading more
- Include 3-5 content-specific tags at the end (format: Tags: tag1, tag2, tag3)

Optimize for:
- read time engagement
- claps and highlights
- follower conversion

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

    return response.choices[0].message.content


# ---------------- META (Facebook) ---------------- #

async def generate_meta(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("meta", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below.

Generate:
1 Facebook post optimized for engagement.

Requirements:
- conversational and relatable tone
- designed to spark comments and shares
- use a question or opinion to drive discussion
- emotionally resonant
- mobile-friendly (short paragraphs)
- include a clear call-to-action (ask a question, invite opinions)
- 2-4 relevant content-specific hashtags

Optimize for:
- comments and shares
- community engagement
- algorithm reach (early engagement signals)

Avoid:
- overly promotional language
- link-heavy posts (algorithm penalizes)
- generic motivational quotes

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

    return response.choices[0].message.content


# ---------------- QUORA ---------------- #

async def generate_quora(source_content, settings, platform_prompts):

    system_prompt = build_system_prompt("quora", settings, platform_prompts)
    temperature = get_temperature(settings.get("creativity", 7))

    prompt = f"""
Analyze the source content below.

Generate:
1 Quora answer format: First write a relevant question, then provide a detailed answer.

Requirements:
- start with a clear, searchable question that the content answers
- answer in first-person, authoritative but approachable tone
- provide real value and depth
- use personal experience framing ("In my experience...", "I've found that...")
- structure with clear paragraphs
- end with a concise takeaway or actionable insight
- no hashtags (Quora doesn't use them)
- slightly academic but accessible tone

Optimize for:
- upvotes
- shares
- "credibility signals" (specific examples, data points)
- SEO (Quora answers rank on Google)

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

    return response.choices[0].message.content


# ---------------- MAIN FUNCTION ---------------- #

async def generate_text_content(source_content, settings=None, platform_prompts=None):

    if settings is None:
        settings = {}
    if platform_prompts is None:
        platform_prompts = {}

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
