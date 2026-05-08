import os

from openai import AsyncOpenAI  # type:ignore
from dotenv import load_dotenv  # type:ignore

load_dotenv()

client = AsyncOpenAI(

    api_key=os.getenv("OPENROUTER_API_KEY"),

    base_url="https://openrouter.ai/api/v1"

)


async def generate_text_content(content):

    try:

        response = await client.chat.completions.create(

            model="openai/gpt-4o-mini",

            max_tokens=1000,

            messages=[

                {
                    "role": "system",

                    "content": """
You are a social media content writer.

Generate platform-native content.

Return ONLY plain text.

Do NOT use markdown.
"""
                },

                {
                    "role": "user",

                    "content":
                    f"""
Generate content for:

1. LinkedIn Post
2. Twitter/X Thread
3. Instagram Caption
4. Reddit Post

Topic:
{content}

Return EXACTLY in this format:

LinkedIn Post:
<content>

Twitter/X Thread:
<content>

Instagram Caption:
<content>

Reddit Post:
<content>
"""
                }

            ]

        )

        generated_text = (
            response.choices[0]
            .message
            .content
        )

        return generated_text

    except Exception as e:

        print("TEXT GENERATION ERROR:", e)

        return """
LinkedIn Post:
Error generating LinkedIn content.

Twitter/X Thread:
Error generating Twitter content.

Instagram Caption:
Error generating Instagram content.

Reddit Post:
Error generating Reddit content.
"""