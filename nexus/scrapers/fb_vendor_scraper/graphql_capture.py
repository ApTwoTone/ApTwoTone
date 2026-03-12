"""
FB Vendor Scraper — GraphQL Response Interceptor.

Instead of parsing Facebook's DOM (which changes constantly and fails on many
post types), this module intercepts the structured JSON data that Facebook's
own frontend fetches from its GraphQL backend.

Usage:
    captured = GraphQLCapture()
    page.on("response", captured.handle_response)
    # ... navigate and scroll ...
    posts = captured.get_posts()  # Returns list of structured post dicts

Each captured post contains: author_name, author_url, text, timestamp,
reaction_count, comment_count, share_count — all extracted from Facebook's
own API responses.
"""
import json
import re
import logging

log = logging.getLogger("fb_scraper")


class GraphQLCapture:
    """Captures post data from Facebook's GraphQL API responses."""

    def __init__(self):
        self.posts = []
        self._seen_ids = set()

    def reset(self):
        self.posts = []
        self._seen_ids = set()

    async def handle_response(self, response):
        """Playwright response handler — attach with page.on('response', ...)."""
        url = response.url
        if "/api/graphql" not in url:
            return

        try:
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type and "text" not in content_type:
                return

            body = await response.text()

            # Facebook often returns multiple JSON objects on separate lines
            for line in body.split("\n"):
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                    self._extract_posts(data)
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass  # Response body may not be available for redirects etc.

    def _extract_posts(self, data: dict):
        """Walk the GraphQL response tree to find post/story nodes."""
        self._walk(data, depth=0)

    def _walk(self, obj, depth=0):
        if depth > 25 or not isinstance(obj, dict):
            return

        typename = obj.get("__typename", "")

        # Facebook wraps posts in Story, Post, or GroupPost nodes
        if typename in ("Story", "Post", "GroupPost"):
            post = self._parse_story_node(obj)
            if post and post.get("author_name"):
                post_id = post.get("id") or f"{post['author_name']}:{post.get('text', '')[:50]}"
                if post_id not in self._seen_ids:
                    self._seen_ids.add(post_id)
                    self.posts.append(post)

        # Recurse into all dict values and list items
        for v in obj.values():
            if isinstance(v, dict):
                self._walk(v, depth + 1)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        self._walk(item, depth + 1)

    def _parse_story_node(self, obj: dict) -> dict:
        """Extract structured data from a Facebook Story/Post GraphQL node."""
        post = {
            "id": obj.get("id") or obj.get("post_id", ""),
            "text": "",
            "author_name": "",
            "author_url": "",
            "author_id": "",
            "timestamp": None,
            "reaction_count": 0,
            "comment_count": 0,
            "share_count": 0,
        }

        # Extract message text — multiple possible locations
        msg = obj.get("message")
        if isinstance(msg, dict):
            post["text"] = msg.get("text", "")
        elif isinstance(msg, str):
            post["text"] = msg

        # Try comet_sections.content.story.message (newer format)
        comet = obj.get("comet_sections", {})
        if isinstance(comet, dict):
            content = comet.get("content", {})
            if isinstance(content, dict):
                story = content.get("story", {})
                if isinstance(story, dict):
                    inner_msg = story.get("message", {})
                    if isinstance(inner_msg, dict) and inner_msg.get("text"):
                        post["text"] = inner_msg["text"]

        # Extract creation time
        post["timestamp"] = (
            obj.get("creation_time")
            or obj.get("created_time")
            or obj.get("timestamp")
        )

        # Extract author info — try multiple locations
        for actor_key in ("actors", "author", "creator", "owner"):
            actors = obj.get(actor_key)
            if actors is None:
                continue
            if isinstance(actors, dict):
                actors = [actors]
            if isinstance(actors, list) and actors:
                actor = actors[0]
                if isinstance(actor, dict):
                    post["author_name"] = actor.get("name", "")
                    post["author_url"] = actor.get("url", "")
                    post["author_id"] = actor.get("id", "")
                    break

        # Try comet_sections for author (newer format)
        if not post["author_name"] and isinstance(comet, dict):
            context = comet.get("context_layout", {})
            if isinstance(context, dict):
                story = context.get("story", {})
                if isinstance(story, dict):
                    actors = story.get("actors", [])
                    if actors and isinstance(actors[0], dict):
                        post["author_name"] = actors[0].get("name", "")
                        post["author_url"] = actors[0].get("url", "")

        # Extract engagement metrics
        feedback = obj.get("feedback") or {}
        if isinstance(feedback, dict):
            rc = feedback.get("reaction_count") or feedback.get("reactors", {})
            if isinstance(rc, dict):
                post["reaction_count"] = rc.get("count", 0)
            cc = feedback.get("comment_count") or feedback.get("total_comment_count")
            if isinstance(cc, dict):
                post["comment_count"] = cc.get("total_count", 0) or cc.get("count", 0)
            elif isinstance(cc, int):
                post["comment_count"] = cc
            sc = feedback.get("share_count")
            if isinstance(sc, dict):
                post["share_count"] = sc.get("count", 0)

        return post

    def get_posts(self) -> list:
        """Return all captured posts as standardized dicts."""
        return list(self.posts)

    def get_post_count(self) -> int:
        return len(self.posts)
