"""
Mock content generation data.
Returns realistic platform-specific content without calling any AI API.
"""

import random

LINKEDIN_POSTS = [
    """The future of work isn't remote or in-office — it's async-first.

After 3 years of building distributed teams, here's what I've learned:

1. Documentation beats meetings every time
2. Async decisions move faster than sync ones
3. Trust is built through output, not presence

The companies winning in 2025 aren't the ones with the best office perks.
They're the ones with the clearest communication systems.

What's your take on async-first culture?

#FutureOfWork #Leadership #RemoteWork #Productivity #BuildInPublic""",

    """AI won't replace developers. Developers who use AI will replace those who don't.

I've been using AI coding assistants for 18 months. Here's the honest truth:

→ My output increased 3x
→ My debugging time dropped 60%
→ I ship features in hours that used to take days

But here's what AI can't do:
→ Understand your users
→ Make architectural decisions
→ Build relationships with your team

The skill isn't prompting. It's knowing what to build.

Are you using AI in your workflow yet?

#AI #SoftwareDevelopment #Productivity #Tech #Innovation""",

    """Most startups fail not because of bad products — but bad timing.

I've watched 40+ startups over the past 5 years. The pattern is clear:

The ones that survived:
✓ Launched before they were ready
✓ Talked to customers weekly
✓ Pivoted based on data, not ego

The ones that failed:
✗ Waited for perfection
✗ Built in isolation
✗ Ignored early signals

Your MVP doesn't need to be perfect. It needs to be in front of users.

What's holding you back from launching?

#Startups #Entrepreneurship #ProductDevelopment #Founder #Growth""",
]

TWITTER_POSTS = [
    "AI agents are the new SaaS. Instead of buying software, you'll hire agents. The shift is already happening. #AI #Startups",
    "Hot take: The best developers aren't the ones who write the most code. They're the ones who delete the most. #SoftwareEngineering #CleanCode",
    "The companies that will dominate the next decade are being built in someone's bedroom right now. #Startups #Entrepreneurship",
    "Unpopular opinion: Most meetings could be a Slack message. Most Slack messages could be documentation. #Productivity #RemoteWork",
    "GPT-5 isn't the story. The story is what developers build with it. #AI #OpenAI #BuildInPublic",
]

INSTAGRAM_CAPTIONS = [
    """The grind is real but so is the growth 📈

Building something from nothing is the hardest and most rewarding thing you'll ever do.

Three years ago I had an idea. Today it's a product used by thousands.

The secret? Consistency over intensity. Show up every single day.

Drop a 🔥 if you're building something right now.

#Entrepreneur #StartupLife #BuildInPublic #Hustle #Growth #Motivation #Founder #TechStartup #Innovation #DreamBig #WorkHard #Success #Mindset #Creator #ContentCreator #DigitalNomad #SideHustle #Productivity #Goals #Inspiration""",

    """POV: You just shipped a feature at 2am and it actually works ✨

The feeling is unmatched.

Building in public, learning in public, failing in public.

That's the only way to grow.

What are you building right now? Tell me in the comments 👇

#Developer #Coding #BuildInPublic #TechLife #Programming #SoftwareEngineer #Startup #Innovation #Tech #Code #WebDev #AppDev #IndieHacker #SideProject #Maker""",
]

REDDIT_POSTS = [
    """I've been building SaaS products for 5 years. Here's what nobody tells you about getting your first 100 customers.

Everyone talks about product-market fit like it's this magical moment. It's not. It's a slow grind of talking to users, iterating, and occasionally getting lucky.

Here's what actually worked for me:

**1. Cold outreach that doesn't feel cold**
I spent 2 hours personalizing each email. Not templates. Actual research on the person's company and a specific reason why my product would help them. Response rate went from 2% to 18%.

**2. Reddit and niche communities**
Not spamming. Actually contributing. I spent 3 months being genuinely helpful in subreddits related to my niche before ever mentioning my product. When I did, people were receptive.

**3. The "do things that don't scale" approach**
I personally onboarded every single one of my first 50 customers. Hopped on calls, helped them set up, asked for feedback. Painful but invaluable.

The honest truth: there's no hack. It's just relentless customer focus.

What's worked for you?

Best subreddits: r/startups, r/entrepreneur, r/SaaS""",
]

MEDIUM_POSTS = [
    """# The Quiet Revolution in AI Development Nobody Is Talking About

There's a shift happening in how software gets built, and most people are missing it entirely.

It's not about ChatGPT. It's not about image generation. It's about something far more fundamental: the democratization of software creation itself.

**The Old World**

For decades, building software required years of education, expensive tooling, and deep technical expertise. The barrier to entry was enormous. Ideas died in notebooks because the people who had them couldn't build them.

**What's Actually Changing**

The gap between "I have an idea" and "I have a working product" has collapsed from years to weeks. Sometimes days.

I've watched non-technical founders build functional MVPs in a weekend. Not toy projects — real products with real users.

This isn't hype. This is happening right now.

**The Implications Are Staggering**

When the cost of building drops to near zero, the constraint shifts entirely to ideas and distribution. The moat is no longer technical. It's creative.

The question isn't "can you build it?" anymore. It's "should you build it, and can you reach the people who need it?"

Tags: AI, Software Development, Startups, Technology, Future""",
]

QUORA_ANSWERS = [
    """**Question: What's the most important skill for a software developer in 2025?**

In my experience building software products for over a decade, the answer might surprise you: it's not a technical skill at all.

It's the ability to understand what problem you're actually solving.

I've worked with brilliant engineers who could write elegant code but couldn't explain why the feature they were building mattered. And I've worked with average coders who shipped products that users loved because they obsessed over the problem.

The technical skills are table stakes now. AI tools have compressed the gap between junior and senior developers significantly. What AI can't replicate is the judgment to know what to build.

**Practically speaking, here's what I'd focus on:**

1. **Systems thinking** — understanding how your code fits into the larger product and business
2. **Communication** — explaining technical concepts to non-technical stakeholders
3. **User empathy** — genuinely caring about the person using what you build
4. **Adaptability** — the tech stack you learn today will be obsolete in 5 years

The developers who will thrive aren't the ones who know the most frameworks. They're the ones who can learn any framework quickly and apply it to real problems.

The takeaway: invest as much in understanding users as you do in understanding code.""",
]


def get_mock_content(source_content: str, settings: dict = None, platform_prompts: dict = None) -> dict:
    """Return realistic mock content for all platforms."""
    return {
        "linkedin":  random.choice(LINKEDIN_POSTS),
        "twitter":   random.choice(TWITTER_POSTS),
        "instagram": random.choice(INSTAGRAM_CAPTIONS),
        "reddit":    random.choice(REDDIT_POSTS),
        "medium":    random.choice(MEDIUM_POSTS),
        "meta":      f"Sharing some thoughts on the future of AI and content creation. What do you think? Drop your perspective in the comments. #AI #ContentCreation #Tech",
        "quora":     random.choice(QUORA_ANSWERS),
    }
