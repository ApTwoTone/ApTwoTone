"""
Zoar Bathroom Rentals — Static Ad Prompt Library

Pre-built, battle-tested prompts extracted from Kai's prompt engineering work.
Each prompt set targets a specific audience/concept and includes:
- Higgsfield generation prompt (scene-focused, ref image handles trailer)
- CTA overlay config (headline, button text, palette)
- Target audience metadata

These are used for Nano Banana Pro 9:16 2K unlimited mode.
The reference image of the actual trailer is uploaded separately.
"""
from __future__ import annotations

AD_CAMPAIGNS = [
    # ═══════════════════════════════════════════════════════════════
    # WEDDING / PRIVATE EVENT — Multiple concepts
    # ═══════════════════════════════════════════════════════════════
    {
        "id": "wedding_garden_golden",
        "category": "wedding",
        "concept": "Lifestyle Wedding Comfort",
        "prompt": (
            "Professional photograph of an upscale outdoor garden wedding reception at golden hour, "
            "luxury restroom trailer parked discreetly at the venue perimeter near mature trees, "
            "manicured lawn with white linen tables and string lights, warm amber natural sunlight, "
            "clean event-ready confidence mood, no people in frame, "
            "front-corner perspective with leading lines into trailer from eye-level camera, "
            "overcast soft light for clean detail, balanced atmospheric depth, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "natural lens depth behavior with realistic edge sharpness, "
            "shot on Canon EOS R5, premium ad-photography realism, vertical 9:16"
        ),
        "headline": "Your guests remember comfort.",
        "cta": "Check Date Availability",
        "subline": "",
        "palette": "forest_green",
        "target": "Wedding/event planners, brides",
    },
    {
        "id": "wedding_estate_editorial",
        "category": "wedding",
        "concept": "High-End Event Editorial",
        "prompt": (
            "Professional photograph at Pasadena historic estate fundraiser event service edge "
            "with mature trees, luxury restroom trailer positioned with clear distance from mansion "
            "front doors, front-corner perspective with leading lines into trailer, "
            "slightly low camera position to increase hero presence, "
            "overcast soft light for clean detail and accurate color, "
            "professional operational reliability mood, no people in frame, "
            "early arrival window with calm premium anticipation, "
            "natural lens depth behavior with realistic edge sharpness, "
            "fine micro-contrast in gravel and pavement surfaces, "
            "controlled highlight rolloff avoiding blown-out skies, "
            "subtle asymmetry in props and table placements for non-staged realism, "
            "shot on Canon EOS R5, vertical 9:16 social framing"
        ),
        "headline": "Private event ready",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Check Date Availability",
        "palette": "forest_green",
        "target": "Event planners, estate managers",
    },
    {
        "id": "wedding_malibu_bluehour",
        "category": "wedding",
        "concept": "Malibu Blue-Hour Premium",
        "prompt": (
            "Professional photograph at Malibu coastal bluff estate wedding perimeter with premium "
            "service access, luxury restroom trailer positioned away from mansion front doors, "
            "low-angle premium hero with strong sky separation, "
            "vertical 9:16 social framing with 35-50mm equivalent look, "
            "blue-hour ambiance with practical trailer lights visible, "
            "high-trust local business quality mood, no people in frame, "
            "midday prep period with organized vendor coordination, "
            "natural lens depth behavior with realistic edge sharpness, "
            "fine micro-contrast in gravel and grass surfaces, "
            "controlled highlight rolloff avoiding blown-out skies, "
            "preserve consistent perspective lines between trailer edges and background, "
            "shot on Canon EOS R5, premium ad-photography finish"
        ),
        "headline": "Private event ready",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Get Event Pricing",
        "palette": "forest_green",
        "target": "Malibu/coastal event planners",
    },
    {
        "id": "wedding_pacific_palisades",
        "category": "wedding",
        "concept": "Avoid Event Embarrassment",
        "prompt": (
            "Professional photograph at Pacific Palisades private garden reception service lane "
            "near parking edge, luxury restroom trailer positioned along logical logistics lane, "
            "slightly elevated angle for event-scale context and logistics realism, "
            "slight off-axis angle for depth without distortion, "
            "blue-hour ambiance with practical trailer lights visible, "
            "warm upscale but not over-stylized mood, no people in frame, "
            "45 minutes before guests arrive during final setup checks, "
            "true-scale wheel and axle geometry with believable load stance, "
            "natural fabric texture on nearby tables with believable folds, "
            "blend reflections with environment light sources to avoid cutout appearance, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16 social framing"
        ),
        "headline": "Elegant event restrooms",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Get Event Pricing",
        "palette": "stone_gray",
        "target": "Luxury event hosts, worried about logistics",
    },
    {
        "id": "wedding_hillside_minimalist",
        "category": "wedding",
        "concept": "Minimalist Offer-Led Conversion",
        "prompt": (
            "Professional photograph at luxury hillside estate event service edge with scenic backdrop, "
            "luxury restroom trailer positioned to leave open guest flow paths, "
            "clean side profile showing all doors and balanced spacing, "
            "slight off-axis angle for depth without distortion, "
            "late-afternoon warm light with realistic contrast falloff, "
            "premium practical realism mood, no people in frame, "
            "golden-hour transition when venue details are most flattering, "
            "accurate cast-shadow direction with physically plausible light falloff, "
            "visible brushed-metal highlights on rails and trim without over-sharpening, "
            "balanced atmospheric depth with clean distant detail, "
            "match scene color cast and white balance so trailer reacts naturally to ambient light, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16"
        ),
        "headline": "Private event ready",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Get Event Pricing",
        "palette": "deep_navy",
        "target": "Budget-conscious event planners",
    },
    {
        "id": "wedding_retreat_planner",
        "category": "wedding",
        "concept": "Planner-Focused Logistics Confidence",
        "prompt": (
            "Professional photograph at multi-day retreat venue perimeter with calm natural landscape, "
            "luxury restroom trailer positioned to leave open guest flow paths, "
            "wide hero exterior with strong depth layering and full context, "
            "closer crop that still preserves full trailer silhouette, "
            "golden hour exterior with soft directional shadows, "
            "premium practical realism mood, no people in frame, "
            "just after setup completion with everything event-ready, "
            "true-scale wheel and axle geometry with believable load stance, "
            "natural fabric texture on nearby tables with believable folds, "
            "blend reflections with environment light sources to avoid cutout appearance, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16"
        ),
        "headline": "Your guests remember comfort.",
        "cta": "Check Date Availability",
        "subline": "",
        "palette": "stone_gray",
        "target": "Event planners with tight timelines",
    },
    {
        "id": "wedding_beach_sunset",
        "category": "wedding",
        "concept": "Beach Sunset Showcase",
        "prompt": (
            "Professional photograph at sunset beach wedding venue perimeter with high-end setup, "
            "luxury restroom trailer placed at venue perimeter or service edge, "
            "3/4 front angle emphasizing stairs, handrails, and wheelbase from vertical 9:16 "
            "social framing with 35-50mm equivalent look, "
            "bright neutral daylight with controlled highlights and balanced atmospheric depth, "
            "clean event-ready confidence mood, no people in frame, "
            "calm premium environment with restrained visual noise in background, "
            "accurate cast-shadow direction with physically plausible light falloff, "
            "visible brushed-metal highlights on rails and trim without over-sharpening, "
            "natural micro-variation in grass and gravel pattern, "
            "match scene color cast and white balance so trailer paint reacts naturally to ambient light, "
            "include stairs, handrails, leveling jacks, and realistic ground contact, "
            "shot on Canon EOS R5, premium ad-photography finish"
        ),
        "headline": "Premium restrooms for weddings and private events",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Secure Your Setup Window",
        "palette": "deep_navy",
        "target": "Beach wedding planners",
    },

    # ═══════════════════════════════════════════════════════════════
    # CONSTRUCTION / CREW — Different audience
    # ═══════════════════════════════════════════════════════════════
    {
        "id": "construction_logistics",
        "category": "construction",
        "concept": "Planner-Focused Logistics Confidence",
        "prompt": (
            "Professional photograph at Burbank studio lot crew support yard during active production setup, "
            "luxury restroom trailer positioned to leave open guest flow paths and maintain believable "
            "event operations layout, front-corner perspective with leading lines into trailer, "
            "eye-level camera with balanced horizon for trust and clarity, "
            "overcast soft light for clean detail and accurate color, "
            "clean event-ready confidence mood, no people in frame, "
            "first morning light before multi-day event operations begin, "
            "true-scale wheel and axle geometry with believable load stance, "
            "gentle ambient haze only if scene distance supports it, "
            "slight practical setup irregularities while staying polished and professional, "
            "blend reflections with environment light sources to avoid cutout or pasted appearance, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16 social framing"
        ),
        "headline": "Your guests remember comfort.",
        "cta": "Get Event Pricing",
        "subline": "",
        "palette": "forest_green",
        "target": "Construction managers, studio lot PMs",
    },
    {
        "id": "construction_crew_comfort",
        "category": "construction",
        "concept": "Crew Comfort & Productivity",
        "prompt": (
            "Professional photograph at construction and industrial project site crew staging zone, "
            "luxury restroom trailer positioned along a logical logistics lane or service corridor, "
            "slightly elevated angle for event-scale context and logistics realism, "
            "eye-level camera with balanced horizon for trust and clarity, "
            "blue-hour ambiance with practical trailer lights visible, "
            "clean event-ready confidence mood, no people in frame, "
            "golden-hour transition when venue details are most flattering, "
            "natural lens depth behavior with realistic edge sharpness, "
            "fine micro-contrast in gravel and pavement surfaces, "
            "subtle asymmetry in props and table placements for non-staged realism, "
            "preserve consistent perspective lines between trailer edges and background, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16"
        ),
        "headline": "Crew comfort that works",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Plan Restrooms Early",
        "palette": "sunset_amber",
        "target": "Construction/film production crews",
    },
    {
        "id": "construction_minimalist",
        "category": "construction",
        "concept": "Minimalist Conversion — Construction",
        "prompt": (
            "Professional photograph at construction and industrial project site crew staging zone, "
            "luxury restroom trailer positioned along logical logistics lane or parking edge, "
            "slightly elevated angle for event-scale context and logistics realism, "
            "eye-level camera with balanced horizon for trust and clarity, "
            "late-afternoon warm light with realistic contrast falloff, "
            "clean event-ready confidence mood, no people in frame, "
            "early arrival window with calm premium anticipation, "
            "true-scale wheel and axle geometry with believable load stance, "
            "gentle ambient haze only if scene distance supports it, "
            "slight practical setup irregularities while staying polished, "
            "blend reflections with environment light sources to avoid cutout appearance, "
            "trailer occupies 60-75% of frame with realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16"
        ),
        "headline": "Crew comfort that works",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Book Your Date",
        "palette": "forest_green",
        "target": "Construction project managers",
    },

    # ═══════════════════════════════════════════════════════════════
    # CORPORATE / GENERAL
    # ═══════════════════════════════════════════════════════════════
    {
        "id": "corporate_trust",
        "category": "corporate",
        "concept": "Local Business Trust-Building",
        "prompt": (
            "Professional photograph at downtown corporate plaza event setup corridor, "
            "luxury restroom trailer positioned with clear distance from main entrance, "
            "just after setup completion with everything event-ready, "
            "slightly elevated angle for event-scale context and logistics realism, "
            "eye-level camera with balanced horizon for trust and clarity, "
            "overcast soft light for clean detail and accurate color, "
            "premium practical realism mood, no people in frame, "
            "high-end venue cues without cluttering the foreground, "
            "trailer occupies 60-75% of frame and remains the visual anchor, "
            "perspective lines remain consistent with no synthetic stretching, "
            "realistic matte-to-semi-gloss panel reflections with no mirror-like artifacts, "
            "natural sky tonality and realistic white balance, "
            "include stairs, handrails, leveling jacks, and realistic ground contact, "
            "shot on Canon EOS R5, vertical 9:16"
        ),
        "headline": "Corporate event comfort",
        "subline": "4-stall | Starting at $1,000",
        "cta": "Book Your Date",
        "palette": "deep_navy",
        "target": "Corporate event coordinators",
    },
    {
        "id": "general_urgency",
        "category": "general",
        "concept": "Before-Problem Avoidance Urgency",
        "prompt": (
            "Professional photograph at Pasadena historic estate fundraiser event service edge "
            "with mature trees, luxury restroom trailer positioned to maintain believable event "
            "operations layout, golden-hour transition when venue details are most flattering, "
            "3/4 front angle emphasizing stairs handrails and wheelbase, "
            "vertical 9:16 social framing with 35-50mm equivalent look, "
            "active but organized event setup details in depth background energy, "
            "golden hour exterior with soft directional shadows, "
            "professional operational reliability mood, no people in frame, "
            "trailer occupies 60-75% of frame and remains the visual anchor, "
            "realistic small shadow softness transitions near contact points, "
            "match scene grain and sharpness so trailer and environment read as one camera capture, "
            "include stairs, handrails, leveling jacks, and realistic ground contact, "
            "shot on Canon EOS R5, premium ad-photography finish"
        ),
        "headline": "4-Stall Luxury Restroom Trailer",
        "subline": "Starting at $1,000",
        "cta": "Check Date Availability",
        "palette": "deep_navy",
        "target": "Event hosts worried about planning",
    },

    # ═══════════════════════════════════════════════════════════════
    # FESTIVAL / SPECIAL EVENTS
    # ═══════════════════════════════════════════════════════════════
    {
        "id": "festival_vip",
        "category": "festival",
        "concept": "Festival VIP Operations Proof",
        "prompt": (
            "Professional photograph at music festival operations lane near VIP zone, "
            "capture just after setup completion with everything event-ready, "
            "strictly festival context, luxury restroom trailer placed at venue perimeter "
            "or service edge never center-stage, "
            "front-corner perspective with leading lines into trailer from slightly low camera, "
            "vertical 9:16 social framing with 35-50mm equivalent look, "
            "overcast soft light for clean detail and accurate color with balanced atmospheric depth, "
            "clean distant detail and warm upscale but not over-stylized mood, "
            "calm premium environment with restrained visual noise background, "
            "no people in frame, "
            "accurate cast-shadow direction with physically plausible light falloff, "
            "visible brushed-metal highlights on rails and trim without over-sharpening, "
            "natural micro-variation in grass and gravel pattern, "
            "show stairs, handrails, leveling setup, and believable event logistics, "
            "shot on Canon EOS R5, commercial-grade realism"
        ),
        "headline": "Plan Restrooms Early",
        "subline": "",
        "cta": "Plan Restrooms Early",
        "palette": "charcoal",
        "target": "Festival organizers, concert promoters",
    },
    {
        "id": "general_golden_arrival",
        "category": "general",
        "concept": "Golden-Hour Arrival Court",
        "prompt": (
            "Professional photograph at remote luxury property arrival court during event prep, "
            "capture golden-hour transition when venue details are most flattering, "
            "strictly general context, luxury restroom trailer at venue perimeter or service edge, "
            "3/4 front angle emphasizing stairs handrails and wheelbase from vertical 9:16 "
            "social framing with 35-50mm equivalent look, "
            "overcast soft light for clean detail with balanced atmospheric depth and clean distant detail, "
            "professional operational reliability mood, no people in frame, "
            "active but organized event setup details in depth background, "
            "accurate cast-shadow direction with physically plausible light falloff, "
            "visible brushed-metal highlights on rails and trim without over-sharpening, "
            "natural micro-variation in grass and gravel pattern, "
            "match scene color cast so trailer paint reacts naturally to ambient light, "
            "show stairs, handrails, leveling setup, and believable event logistics, "
            "shot on Canon EOS R5, premium commercial styling"
        ),
        "headline": "Starting at $1,000",
        "subline": "4-stall premium trailer",
        "cta": "Reserve Your Weekend",
        "palette": "deep_navy",
        "target": "General event hosts",
    },
]

# Color palettes for CTA overlays
PALETTES = {
    "forest_green": {
        "headline_bg": (34, 87, 55, 200),      # dark forest green, semi-transparent
        "headline_text": (245, 241, 230),        # muted ivory
        "cta_bg": (34, 87, 55, 240),             # solid forest green
        "cta_text": (245, 241, 230),
        "subline_text": (245, 241, 230, 200),
    },
    "deep_navy": {
        "headline_bg": (18, 25, 48, 200),        # deep navy
        "headline_text": (212, 175, 85),          # warm gold
        "cta_bg": (212, 175, 85, 240),            # gold button
        "cta_text": (18, 25, 48),                 # navy text on gold
        "subline_text": (255, 255, 255, 200),
    },
    "stone_gray": {
        "headline_bg": (60, 60, 60, 200),         # stone gray
        "headline_text": (255, 255, 255),          # clean white
        "cta_bg": (185, 155, 80, 240),             # brass accent
        "cta_text": (35, 35, 35),
        "subline_text": (255, 255, 255, 200),
    },
    "sunset_amber": {
        "headline_bg": (45, 35, 30, 200),          # warm dark
        "headline_text": (245, 190, 80),            # sunset amber
        "cta_bg": (245, 190, 80, 240),
        "cta_text": (35, 25, 20),
        "subline_text": (255, 255, 255, 200),
    },
    "charcoal": {
        "headline_bg": (35, 35, 35, 200),
        "headline_text": (240, 230, 210),            # soft cream
        "cta_bg": (240, 230, 210, 240),
        "cta_text": (35, 35, 35),
        "subline_text": (240, 230, 210, 180),
    },
}

# Negative prompts / rules to append to every prompt
NEGATIVE_RULES = (
    "No warped geometry. No extra trailers. No dirty or damaged trailer. "
    "No plastic or cartoon rendering look. No tiny distant AI-looking people. "
    "No glossy mannequin skin or distorted human anatomy. "
    "No cluttered compositions or tiny unreadable copy. "
    "No text, logos, decals, labels, or phone numbers on trailer body or doors."
)


def get_campaigns_by_category(category: str = None) -> list:
    """Get ad campaigns, optionally filtered by category."""
    if category:
        return [c for c in AD_CAMPAIGNS if c["category"] == category]
    return list(AD_CAMPAIGNS)


def get_campaign_by_id(campaign_id: str) -> dict | None:
    """Get a single campaign by ID."""
    for c in AD_CAMPAIGNS:
        if c["id"] == campaign_id:
            return c
    return None


def get_categories() -> list:
    """Get unique category names."""
    return sorted(set(c["category"] for c in AD_CAMPAIGNS))


def build_full_prompt(campaign: dict) -> str:
    """Build the complete Higgsfield prompt from a campaign config."""
    return f"{campaign['prompt']}. {NEGATIVE_RULES}"
