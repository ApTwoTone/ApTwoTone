"""
Content Generation Agent — Creates social media posts, comments, and DM templates.
Generates content queue that can be auto-posted or reviewed first.
Uses Haiku for quick comments, Sonnet for longer posts.
"""
import json
import random
from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent
from agents.config import AGENT_DEFAULTS, BUSINESS, AGENT_STATE_DIR


CONTENT_QUEUE_FILE = AGENT_STATE_DIR / "content_queue.json"


class ContentAgent(BaseAgent):
    """Generates social media content for Zoar Bathroom Rentals."""

    def __init__(self):
        super().__init__("content_gen", "content_generator")

    async def generate_comment(self, post_context: str, platform: str = "facebook",
                                intent_level: str = "medium") -> str:
        """
        Generate a natural comment for a social media post.
        intent_level: "high" (directly relevant), "medium" (tangentially relevant), "general"
        """
        system = f"""You are a person who works in the SoCal event industry.
You're commenting on a {platform} post. Be genuinely helpful.

RULES:
- Write 1-3 sentences MAX
- Be conversational and natural — this is social media, not an email
- If intent is HIGH (someone asking about restrooms/bathrooms for events):
  Share your experience, mention that luxury restroom trailers exist as an option.
  Only mention Zoar if it feels natural (not forced).
- If intent is MEDIUM (general event planning):
  Give helpful advice. Do NOT mention Zoar or restrooms unless asked.
- If intent is GENERAL:
  Just be helpful and friendly. No promotion at all.
- NEVER be salesy, NEVER use corporate language
- Sound like a real person, not a brand account
- Use casual language, maybe an emoji or two but don't overdo it
- NEVER say "I work for" or "check out our"
"""

        prompt = f"""Intent level: {intent_level}
Platform: {platform}
Post content: {post_context}

Write a natural, helpful comment:"""

        return await self.think(prompt, system=system, max_tokens=200)

    async def generate_dm(self, recipient_context: str, reason: str) -> str:
        """Generate a personalized DM for outreach."""
        system = f"""You are reaching out on behalf of Zoar Bathroom Rentals.
Write a short, personal DM. NOT salesy. Like a real person would message.
{BUSINESS['contact']['website']} | {BUSINESS['contact']['phone']}

RULES:
- Max 3-4 sentences
- Start with something specific about THEIR post/event
- Casually mention you do luxury restroom rentals IF relevant
- End with a soft CTA (not "book now" — more like "happy to answer any questions")
- Sound human, warm, approachable"""

        prompt = f"""Recipient context: {recipient_context}
Reason for reaching out: {reason}

Write the DM:"""

        return await self.think(prompt, system=system, max_tokens=250)

    async def generate_post(self, topic: str, platform: str = "facebook",
                            post_type: str = "value") -> dict:
        """
        Generate a social media post.
        post_type: "value" (helpful tip), "showcase" (show work), "engagement" (question/poll)
        """
        system = f"""You are the social media manager for Zoar Bathroom Rentals.
Write a {platform} post. Product: {BUSINESS['product']}.
Service areas: {', '.join(BUSINESS['service_areas'])}.

POST TYPES:
- "value": Share a genuinely useful tip about event planning. Mention restrooms naturally if relevant.
- "showcase": Show off the trailer — describe a recent setup (create realistic scenario). Professional but warm.
- "engagement": Ask a question to drive comments. Event planning related.

RULES:
- Keep under 200 words for Facebook
- Use line breaks for readability
- 1-3 relevant hashtags max (not spammy)
- Include a soft CTA only for "showcase" posts
- Sound like a real person, not a brand
- For "value" posts, the value should come FIRST, product mention is secondary or absent"""

        prompt = f"""Topic: {topic}
Platform: {platform}
Post type: {post_type}

Write the post:"""

        text = await self.think(
            prompt, system=system,
            model=AGENT_DEFAULTS["smart_model"],
            max_tokens=400
        )

        return {
            "text": text,
            "platform": platform,
            "post_type": post_type,
            "topic": topic,
            "generated_at": datetime.now().isoformat(),
            "status": "pending_review",
        }

    async def generate_content_batch(self, count: int = 7) -> list:
        """Generate a week's worth of content (mix of types).
        Consults the shared brain for what's working and what's not."""

        # Check brain for content recommendations
        brain_recs = self.brain.read_recommendations(category="content", limit=5)
        brain_context = self.brain.get_context_for_agent(self.agent_id, limit=5)

        # Check knowledge base for content learnings
        content_knowledge = self.brain.find_solution("content engagement what works")

        topics_value = [
            "outdoor wedding planning checklist for SoCal",
            "things people forget when planning a backyard party",
            "how to handle restroom logistics at an outdoor event",
            "why luxury restroom trailers are trending for LA weddings",
            "5 tips for planning a corporate outdoor event in LA",
            "how to make your outdoor event feel premium",
            "wedding vendor tips: what brides wish they knew earlier",
        ]
        topics_showcase = [
            "weekend wedding setup in Malibu",
            "corporate event at a Santa Monica rooftop",
            "ranch wedding in Santa Clarita Valley",
            "film set amenities in Burbank",
        ]
        topics_engagement = [
            "what's the one thing guests always notice at outdoor events?",
            "outdoor wedding or indoor wedding — which do you prefer?",
            "what's the most underrated vendor at a wedding?",
        ]

        posts = []
        schedule = [
            ("value", topics_value),
            ("showcase", topics_showcase),
            ("value", topics_value),
            ("engagement", topics_engagement),
            ("value", topics_value),
            ("showcase", topics_showcase),
            ("engagement", topics_engagement),
        ]

        for i in range(min(count, len(schedule))):
            post_type, topic_list = schedule[i]
            topic = random.choice(topic_list)
            post = await self.generate_post(topic, post_type=post_type)
            posts.append(post)
            self.log(f"Generated {post_type} post: {topic[:50]}")

        # Save to content queue
        self._save_to_queue(posts)

        # Write to shared brain
        types_generated = [p.get("post_type", "post") for p in posts]
        self.brain.write_insight(
            content=f"Generated {len(posts)} posts: {', '.join(set(types_generated))}",
            insight_type="activity",
            category="content",
            confidence=1.0,
            data={"count": len(posts), "types": types_generated},
        )
        self.brain.log_activity("content_generated",
                                f"Generated {len(posts)} posts", "content")
        self.log_to_crm("content_generated", f"Generated {len(posts)} posts")
        return posts

    def _save_to_queue(self, posts: list):
        """Save generated posts to the content queue file."""
        existing = []
        if CONTENT_QUEUE_FILE.exists():
            try:
                existing = json.loads(CONTENT_QUEUE_FILE.read_text())
            except Exception:
                pass
        existing.extend(posts)
        CONTENT_QUEUE_FILE.write_text(json.dumps(existing, indent=2))
        self.log(f"Saved {len(posts)} posts to content queue")

    def get_queue(self) -> list:
        """Get all queued content."""
        if CONTENT_QUEUE_FILE.exists():
            return json.loads(CONTENT_QUEUE_FILE.read_text())
        return []

    def approve_post(self, index: int):
        """Mark a queued post as approved for publishing."""
        queue = self.get_queue()
        if 0 <= index < len(queue):
            queue[index]["status"] = "approved"
            CONTENT_QUEUE_FILE.write_text(json.dumps(queue, indent=2))
