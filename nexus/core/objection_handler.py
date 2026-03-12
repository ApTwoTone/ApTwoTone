from __future__ import annotations
"""
AI-Powered Objection Handler — Zoar Bathroom Rentals
- Detects common sales objections in lead messages
- Suggests pre-written, conversion-optimized responses
- Fills dynamic placeholders ({name}, {date}, {per_guest}, etc.)
- Formats suggestions for Telegram approval workflow
"""
import re
from datetime import datetime

# ── Price & business constants ───────────────────────────────────────────────

BASE_PRICE = 1100.00  # $1,100 per event
DEFAULT_GUEST_COUNT = 150
DEPOSIT_AMOUNT = 250

# ── Objection Library ────────────────────────────────────────────────────────

OBJECTION_RESPONSES = {
    "too_expensive": {
        "keywords": ["expensive", "too much", "can't afford", "cheaper", "less", "discount",
                     "price", "cost", "budget", "pricey"],
        "responses": [
            (
                "Great question about pricing! For a {guest_count}-guest {event_type}, that comes "
                "out to about ${per_guest:.2f} per guest. Compare that to catering at $80-150/person "
                "or photography at $3,000-8,000 \u2014 this is actually one of the most affordable "
                "major upgrades that makes the biggest impression. And it includes everything: "
                "delivery, setup, and pickup! Would you like me to send some photos so you can "
                "see the value?"
            ),
            (
                "I totally understand! What I CAN do is include a complimentary amenity basket "
                "(hand lotion, mints, hairspray) and a fresh flower arrangement inside the trailer "
                "at no extra charge. That's usually a $150 add-on. Would that help?"
            ),
            (
                "I hear you \u2014 event costs add up fast! Just to put it in perspective, a "
                "standard porta potty rental runs $150-300 and your guests will notice (and not "
                "in a good way). For ${price}, you're getting a luxury 4-stall trailer with AC, "
                "running water, and real flushing toilets \u2014 it's the upgrade guests actually "
                "talk about. Want to see what it looks like inside?"
            ),
        ],
    },
    "thinking_about_it": {
        "keywords": ["think about it", "not sure", "maybe", "let me think", "i'll get back",
                     "need to discuss", "talk to my", "considering"],
        "responses": [
            (
                "Absolutely, take your time! Just so you know, {month} is one of our busiest "
                "months and we only have one trailer. I can't guarantee availability if someone "
                "else books {date}. Would it help if I penciled you in tentatively for 48 hours? "
                "No commitment \u2014 just holds your spot while you decide \U0001f60a"
            ),
            (
                "Of course! No pressure at all. I'll send over a quick info sheet with photos "
                "so you have everything in one place when you're ready. What's the best email "
                "for you?"
            ),
        ],
    },
    "event_far_away": {
        "keywords": ["far away", "not until", "months away", "next year", "long time",
                     "way out", "still early"],
        "responses": [
            (
                "Totally understand! The nice thing about booking early is you lock in today's "
                "price and guarantee your date. The ${deposit} deposit is fully transferable if "
                "your date changes, so there's no risk. Want me to hold it for you?"
            ),
            (
                "That's actually perfect timing! Our most organized clients book 3-6 months out. "
                "We do have a price increase coming up, so locking in now saves you money. And "
                "the ${deposit} deposit is fully refundable up to 30 days before your event."
            ),
        ],
    },
    "need_availability": {
        "keywords": ["available", "availability", "do you have", "open", "that date",
                     "free that day", "book for"],
        "responses": [
            (
                "Let me check! ... \u2705 Great news \u2014 {date} is currently available! "
                "These dates do fill up, especially for {month}. Would you like to lock it in "
                "with a ${deposit} deposit? I can send you a secure payment link right now \U0001f60a"
            ),
        ],
    },
    "can_you_do_less": {
        "keywords": ["negotiate", "deal", "lower", "better price", "for less", "wiggle room",
                     "best price", "match", "coupon", "promo"],
        "responses": [
            (
                "I appreciate you asking! Our pricing includes everything \u2014 the 4-stall "
                "trailer, delivery, setup, a full attendant, and pickup \u2014 so there's not "
                "much room to move on price. BUT what I CAN do is throw in some premium extras: "
                "a complimentary amenity basket, fresh flowers inside the trailer, and I can "
                "extend the rental window by an extra hour at no charge. Sound good?"
            ),
            (
                "I wish I could! We're already priced pretty competitively for what's included "
                "(delivery, setup, attendant, and pickup). What I CAN do is add our premium "
                "amenity upgrade for free \u2014 that's the basket with lotion, mints, hairspray, "
                "and fresh flowers inside. That's normally $150 extra. Would that work?"
            ),
        ],
    },
    "what_is_included": {
        "keywords": ["what's included", "what do i get", "what comes with", "includes",
                     "features", "what do you provide", "amenities"],
        "responses": [
            (
                "Great question! For ${price} you get our luxury 4-stall restroom trailer with:\n"
                "\u2705 4 private stalls with flushing porcelain toilets\n"
                "\u2705 AC in summer / heat in winter\n"
                "\u2705 Running hot & cold water\n"
                "\u2705 LED lighting & full-length mirrors\n"
                "\u2705 Bluetooth speaker for music\n"
                "\u2705 Premium hand soap & towels\n"
                "\u2705 Delivery, setup & pickup included\n\n"
                "Basically a 5-star bathroom experience for your guests! Want to see photos?"
            ),
        ],
    },
    "how_does_it_work": {
        "keywords": ["how does it work", "logistics", "setup", "delivery", "hookup", "power",
                     "water supply", "how long", "what do i need", "requirements"],
        "responses": [
            (
                "Super easy! We handle everything. We deliver the trailer 2-3 hours before your "
                "event, set everything up including water and power connections (we bring our own "
                "generator and water tank \u2014 no hookups needed on your end), and then pick up "
                "the next morning. All you need is a relatively flat surface we can access with "
                "our truck. Easy as that! \U0001f69a\u2728"
            ),
            (
                "Great question! Here's how it works:\n\n"
                "1\ufe0f\u20e3 You book with a ${deposit} deposit\n"
                "2\ufe0f\u20e3 We confirm your delivery window (usually 2-3 hrs before event)\n"
                "3\ufe0f\u20e3 We deliver, set up, and stock everything\n"
                "4\ufe0f\u20e3 Your guests enjoy luxury restrooms all night\n"
                "5\ufe0f\u20e3 We pick up the next morning\n\n"
                "No hookups needed \u2014 we bring our own water tank and generator. All you "
                "need is a flat-ish spot for the trailer. Want to lock in your date?"
            ),
        ],
    },
    "just_looking": {
        "keywords": ["just looking", "just browsing", "not ready", "researching", "comparing",
                     "shopping around", "getting quotes"],
        "responses": [
            (
                "No worries at all! Happy to be a resource. If it helps, I can send over some "
                "photos and our info sheet so you have everything when you're ready to decide. "
                "What's the best email? And feel free to reach out anytime with questions \u2014 "
                "no pressure at all \U0001f60a"
            ),
            (
                "Smart to do your research! We're actually the only luxury restroom trailer "
                "rental in {area} with 4 full private stalls, AC/heat, and hot running water "
                "at this price point. Happy to answer any questions as you compare. Would "
                "photos help?"
            ),
        ],
    },
    "do_you_serve_my_area": {
        "keywords": ["area", "location", "serve", "travel to", "deliver to", "how far",
                     "distance", "outside", "coverage"],
        "responses": [
            (
                "We serve all of Southern California! LA, Orange County, Inland Empire, Ventura, "
                "San Diego \u2014 you name it. Delivery is included in our standard pricing for "
                "most SoCal locations. Where's your event?"
            ),
        ],
    },
    "need_more_stalls": {
        "keywords": ["more stalls", "bigger", "larger", "how many stalls", "enough for",
                     "not enough", "more capacity", "two trailers"],
        "responses": [
            (
                "Our 4-stall trailer comfortably handles events up to 200+ guests. Each stall "
                "is a full private room (not a cramped porta potty!), so the flow is actually "
                "really smooth. For {guest_count} guests, you're perfectly covered. Want to "
                "see the floor plan?"
            ),
        ],
    },
}


# ── Objection Handler ────────────────────────────────────────────────────────

class ObjectionHandler:
    """Detect sales objections in lead messages and suggest optimized responses."""

    def __init__(self):
        self.responses = OBJECTION_RESPONSES
        print(f"[Objections] Loaded {len(self.responses)} objection categories "
              f"with {sum(len(v['responses']) for v in self.responses.values())} total responses")

    def detect_objection(self, message: str) -> list[str]:
        """Scan a lead message for objection keywords.

        Returns a list of matched objection type names, sorted by confidence
        (number of keyword matches, descending).
        """
        if not message:
            return []

        message_lower = message.lower().strip()
        matches = []

        for objection_type, data in self.responses.items():
            hit_count = 0
            for keyword in data["keywords"]:
                if keyword.lower() in message_lower:
                    hit_count += 1
            if hit_count > 0:
                matches.append((objection_type, hit_count))

        # Sort by number of keyword hits (most specific match first)
        matches.sort(key=lambda x: x[1], reverse=True)
        result = [m[0] for m in matches]

        if result:
            print(f"[Objections] Detected: {', '.join(result)} in: \"{message[:80]}\"")
        return result

    def get_suggested_responses(self, message: str, lead_data: dict = None) -> list[dict]:
        """For a lead message, return suggested responses with placeholders filled.

        Args:
            message: The lead's message text
            lead_data: Optional dict with lead info for placeholder filling:
                - name, first_name, event_type, date, month,
                - guest_count, area, phone, email, etc.

        Returns:
            List of dicts: [{objection_type, response, confidence, keyword_hits}]
        """
        if not message:
            return []

        message_lower = message.lower().strip()
        lead_data = lead_data or {}
        suggestions = []

        for objection_type, data in self.responses.items():
            # Count keyword matches for confidence scoring
            hits = []
            for keyword in data["keywords"]:
                if keyword.lower() in message_lower:
                    hits.append(keyword)

            if not hits:
                continue

            # Confidence: more keyword hits = higher confidence
            confidence = min(len(hits) / max(len(data["keywords"]) * 0.3, 1), 1.0)
            confidence = round(confidence, 2)

            for response_template in data["responses"]:
                filled = self.fill_placeholders(response_template, lead_data)
                suggestions.append({
                    "objection_type": objection_type,
                    "response": filled,
                    "confidence": confidence,
                    "keyword_hits": hits,
                })

        # Sort by confidence (highest first)
        suggestions.sort(key=lambda x: x["confidence"], reverse=True)

        if suggestions:
            print(f"[Objections] Generated {len(suggestions)} suggestions for: \"{message[:60]}\"")
        else:
            print(f"[Objections] No objections detected in: \"{message[:60]}\"")

        return suggestions

    def fill_placeholders(self, template: str, lead_data: dict) -> str:
        """Fill response template placeholders with lead data.

        Supported placeholders:
            {name} / {first_name} - Lead's first name
            {event_type} - Type of event (wedding, party, etc.)
            {date} - Event date
            {month} - Event month name
            {guest_count} - Number of guests
            {per_guest} - Price per guest (calculated)
            {price} - Base price ($1,100)
            {deposit} - Deposit amount ($250)
            {area} - Service area
            {phone} - Lead phone
            {email} - Lead email
        """
        if not lead_data:
            lead_data = {}

        name = lead_data.get("name") or lead_data.get("first_name") or "there"
        event_type = lead_data.get("event_type") or "event"
        guest_count = lead_data.get("guest_count") or DEFAULT_GUEST_COUNT
        date = lead_data.get("date") or "your date"
        area = lead_data.get("area") or "Southern California"

        # Calculate per-guest cost
        try:
            guest_count_num = int(guest_count)
        except (ValueError, TypeError):
            guest_count_num = DEFAULT_GUEST_COUNT
        per_guest = BASE_PRICE / max(guest_count_num, 1)

        # Derive month from date if possible
        month = lead_data.get("month") or ""
        if not month and date and date != "your date":
            try:
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
                    try:
                        parsed = datetime.strptime(date, fmt)
                        month = parsed.strftime("%B")
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        if not month:
            month = "the upcoming season"

        # Build replacements dict
        replacements = {
            "name": name,
            "first_name": name,
            "event_type": event_type,
            "date": date,
            "month": month,
            "guest_count": str(guest_count_num),
            "per_guest": per_guest,
            "price": f"{BASE_PRICE:,.0f}",
            "deposit": f"{DEPOSIT_AMOUNT}",
            "area": area,
            "phone": lead_data.get("phone", ""),
            "email": lead_data.get("email", ""),
        }

        # Use str.format_map with a safe default
        class SafeDict(dict):
            def __missing__(self, key):
                return f"{{{key}}}"

        try:
            result = template.format_map(SafeDict(replacements))
        except Exception as e:
            print(f"[Objections] Placeholder fill error: {e}")
            result = template

        return result

    def format_telegram_suggestion(self, lead_name: str, original_message: str,
                                   suggestions: list[dict]) -> str:
        """Format objection suggestions for Telegram notification with approve context.

        Args:
            lead_name: Name of the lead
            original_message: The lead's original message
            suggestions: List of suggestion dicts from get_suggested_responses()

        Returns:
            Formatted Telegram message string (Markdown)
        """
        if not suggestions:
            return (
                f"\U0001f4e9 **{lead_name}** replied:\n"
                f"_{original_message[:300]}_\n\n"
                f"No objection pattern detected. Reply manually."
            )

        lines = [
            f"\U0001f4e9 **{lead_name}** replied:",
            f"_{original_message[:300]}_",
            "",
            f"\U0001f4a1 **Detected: {suggestions[0]['objection_type'].replace('_', ' ').title()}** "
            f"(confidence: {suggestions[0]['confidence']:.0%})",
            f"Keywords: {', '.join(suggestions[0].get('keyword_hits', [])[:5])}",
            "",
        ]

        # Show top 3 suggestions max
        shown = min(len(suggestions), 3)
        # De-duplicate by response text
        seen_responses = set()
        unique_suggestions = []
        for s in suggestions:
            text_key = s["response"][:100]
            if text_key not in seen_responses:
                seen_responses.add(text_key)
                unique_suggestions.append(s)
        unique_suggestions = unique_suggestions[:3]

        for i, s in enumerate(unique_suggestions, 1):
            objection_label = s["objection_type"].replace("_", " ").title()
            preview = s["response"][:250]
            if len(s["response"]) > 250:
                preview += "..."
            lines.append(f"**Option {i}** ({objection_label}):")
            lines.append(f"{preview}")
            lines.append("")

        lines.append(f"Reply with the option number to send, or type a custom response.")
        return "\n".join(lines)

    def get_all_categories(self) -> list[dict]:
        """List all objection categories with their keyword counts.

        Returns list of {type, keyword_count, response_count}.
        """
        return [
            {
                "type": obj_type,
                "keyword_count": len(data["keywords"]),
                "response_count": len(data["responses"]),
                "keywords_preview": ", ".join(data["keywords"][:5]),
            }
            for obj_type, data in self.responses.items()
        ]

    def add_response(self, objection_type: str, response: str) -> bool:
        """Add a new response template to an existing objection type.

        Returns True if added, False if objection type not found.
        """
        if objection_type not in self.responses:
            print(f"[Objections] Cannot add response: unknown type '{objection_type}'")
            return False
        self.responses[objection_type]["responses"].append(response)
        print(f"[Objections] Added response to '{objection_type}' "
              f"(now {len(self.responses[objection_type]['responses'])} total)")
        return True

    def add_objection_type(self, objection_type: str, keywords: list[str],
                           responses: list[str]) -> bool:
        """Register a new objection type.

        Args:
            objection_type: Snake_case name (e.g., "rain_concern")
            keywords: List of trigger keywords
            responses: List of response templates

        Returns True if added, False if already exists.
        """
        if objection_type in self.responses:
            print(f"[Objections] Type '{objection_type}' already exists. Use add_response() instead.")
            return False
        self.responses[objection_type] = {
            "keywords": keywords,
            "responses": responses,
        }
        print(f"[Objections] Added new type '{objection_type}' "
              f"with {len(keywords)} keywords and {len(responses)} responses")
        return True


# ── Module-level singleton ───────────────────────────────────────────────────

_handler: ObjectionHandler | None = None


def get_objection_handler() -> ObjectionHandler:
    """Get or create the singleton ObjectionHandler instance."""
    global _handler
    if _handler is None:
        _handler = ObjectionHandler()
    return _handler
