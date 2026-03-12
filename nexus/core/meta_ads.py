"""
Meta Marketing API Integration — Zoar Bathroom Rentals

Full-lifecycle ad management via the Facebook Business SDK:
  - Campaign, Ad Set, Ad, Creative CRUD (all created PAUSED by default)
  - Lead form retrieval and lead download
  - Image/video upload for creative assets
  - Insights pull: impressions, reach, clicks, leads, CPL, CTR, CPM, frequency,
    video views, hook rate, relevance diagnostics
  - Targeting presets for Zoar's core audiences (wedding SFV, quinceanera,
    corporate LA, broad Advantage+)
  - Automated rules engine: pause high-CPL ads, scale winners, fatigue alerts,
    low-CTR pause
  - Budget scaling with 20% max-increase safety rail

Config:   ~/.nexus/config.json
  Keys:   fb_app_id, fb_app_secret, fb_page_access_token,
          fb_ad_account_id, fb_pixel_id

All campaigns are created PAUSED — Kai manually activates after review.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# ── Facebook Business SDK (optional dependency) ─────────────────────────────

try:
    from facebook_business.api import FacebookAdsApi
    from facebook_business.adobjects.adaccount import AdAccount
    from facebook_business.adobjects.campaign import Campaign
    from facebook_business.adobjects.adset import AdSet
    from facebook_business.adobjects.ad import Ad
    from facebook_business.adobjects.adcreative import AdCreative
    from facebook_business.adobjects.adimage import AdImage
    from facebook_business.adobjects.advideo import AdVideo
    from facebook_business.adobjects.leadgenform import LeadgenForm
    HAS_FB_SDK = True
except ImportError:
    HAS_FB_SDK = False


# ── Logging ──────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"[MetaAds] {msg}")


# ── Targeting Presets ────────────────────────────────────────────────────────

TARGETING_PRESETS = {
    "wedding_sfv": {
        "geo_locations": {
            "location_types": ["home"],
            "cities": [{"key": "2421836", "name": "Los Angeles"}],
            "radius": 30,
            "distance_unit": "mile",
        },
        "age_min": 25,
        "age_max": 45,
        "genders": [2],
        "flexible_spec": [
            {
                "interests": [
                    {"id": "6003384285439", "name": "Wedding planning"},
                    {"id": "6003020834693", "name": "The Knot"},
                    {"id": "6003397425735", "name": "WeddingWire"},
                ]
            }
        ],
        "life_events": [{"id": "6002714398172", "name": "Newly engaged"}],
    },
    "quinceanera_sfv": {
        "geo_locations": {
            "location_types": ["home"],
            "cities": [{"key": "2421836", "name": "Los Angeles"}],
            "radius": 30,
            "distance_unit": "mile",
        },
        "age_min": 35,
        "age_max": 55,
        "locales": [28],
    },
    "corporate_la": {
        "geo_locations": {
            "location_types": ["home"],
            "cities": [{"key": "2421836", "name": "Los Angeles"}],
            "radius": 40,
            "distance_unit": "mile",
        },
        "age_min": 28,
        "age_max": 55,
    },
    "broad_advantage_plus": {
        "geo_locations": {
            "location_types": ["home"],
            "cities": [{"key": "2421836", "name": "Los Angeles"}],
            "radius": 35,
            "distance_unit": "mile",
        },
        "age_min": 24,
        "age_max": 55,
    },
}


# ── Automated Rules ─────────────────────────────────────────────────────────

AUTOMATED_RULES = {
    "pause_high_cpl": {
        "condition": "cpl > 60 AND impressions > 1500",
        "action": "pause_ad",
    },
    "scale_winner": {
        "condition": "cpl < 25 AND leads > 5 AND days >= 3",
        "action": "increase_budget_15pct",
        "max_budget": 10000,
    },
    "fatigue_alert": {
        "condition": "frequency > 3.0",
        "action": "alert",
    },
    "low_ctr_pause": {
        "condition": "ctr < 0.005 AND impressions > 2000",
        "action": "pause_ad",
    },
}


# ── Budget Scaling Safety ────────────────────────────────────────────────────

def scale_budget(current_budget: int, target_budget: int) -> int:
    """
    Enforce 20% max budget increase rule.

    Never increase more than 20% at once to avoid tripping Meta's learning
    phase reset and sudden spend spikes.

    Args:
        current_budget: Current daily budget in cents.
        target_budget:  Desired new daily budget in cents.

    Returns:
        Safe budget value in cents (capped at 120% of current if needed).
    """
    max_increase = current_budget * 1.20
    if target_budget > max_increase:
        _log(
            f"Budget scale capped: requested {target_budget} but max allowed "
            f"is {int(max_increase)} (20% rule from {current_budget})"
        )
        return int(max_increase)
    return int(target_budget)


# ═════════════════════════════════════════════════════════════════════════════
# MetaAdsManager
# ═════════════════════════════════════════════════════════════════════════════

class MetaAdsManager:
    """
    Full Meta Marketing API manager for Zoar Bathroom Rentals.

    All mutating operations (create campaign, create ad, etc.) default to
    PAUSED status.  Kai reviews in Ads Manager and manually activates.
    """

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else Path.home() / ".nexus" / "config.json"
        self.config: dict = {}
        self.ad_account_id: str = ""
        self.pixel_id: str = ""
        self._account: AdAccount | None = None
        self._init_api()

    # ── Initialization ───────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Load config from disk."""
        if not self.config_path.exists():
            _log(f"Config not found at {self.config_path}")
            return {}
        try:
            return json.loads(self.config_path.read_text())
        except Exception as exc:
            _log(f"Failed to load config: {exc}")
            return {}

    def _init_api(self):
        """Load config and initialize FacebookAdsApi singleton."""
        self.config = self._load_config()

        app_id = self.config.get("fb_app_id", "")
        app_secret = self.config.get("fb_app_secret", "")
        access_token = self.config.get("fb_page_access_token", "")
        self.ad_account_id = self.config.get("fb_ad_account_id", "")
        self.pixel_id = self.config.get("fb_pixel_id", "")

        if not self.ad_account_id.startswith("act_") and self.ad_account_id:
            self.ad_account_id = f"act_{self.ad_account_id}"

        if not HAS_FB_SDK:
            _log("facebook_business SDK not installed — API calls will fail")
            return

        if not access_token:
            _log("No fb_page_access_token in config — API calls will fail")
            return

        try:
            FacebookAdsApi.init(app_id, app_secret, access_token)
            self._account = AdAccount(self.ad_account_id)
            _log(f"API initialized for account {self.ad_account_id}")
        except Exception as exc:
            _log(f"API init failed: {exc}")
            traceback.print_exc()

    def _require_sdk(self):
        """Raise ImportError if the FB SDK is not available."""
        if not HAS_FB_SDK:
            raise ImportError(
                "facebook_business SDK is required but not installed. "
                "Run: pip install facebook_business"
            )

    def _require_account(self):
        """Ensure we have an initialized AdAccount."""
        self._require_sdk()
        if self._account is None:
            raise RuntimeError(
                "AdAccount not initialized — check fb_page_access_token and "
                "fb_ad_account_id in config"
            )

    # ── Account ──────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        """Fetch ad account metadata."""
        self._require_account()
        _log("Fetching account info")
        try:
            fields = [
                "name",
                "account_id",
                "account_status",
                "currency",
                "timezone_name",
                "balance",
                "amount_spent",
                "spend_cap",
                "business_name",
                "funding_source_details",
            ]
            acct = self._account.api_get(fields=fields)
            result = {
                "name": acct.get("name", ""),
                "account_id": acct.get("account_id", ""),
                "account_status": acct.get("account_status", ""),
                "currency": acct.get("currency", ""),
                "timezone": acct.get("timezone_name", ""),
                "balance": acct.get("balance", ""),
                "amount_spent": acct.get("amount_spent", ""),
                "spend_cap": acct.get("spend_cap", ""),
                "business_name": acct.get("business_name", ""),
            }
            _log(f"Account: {result['name']} ({result['account_id']})")
            return result
        except Exception as exc:
            _log(f"get_account_info error: {exc}")
            traceback.print_exc()
            return {"error": str(exc)}

    # ── Campaigns ────────────────────────────────────────────────────────

    def get_campaigns(self, status_filter: str | None = None) -> list[dict]:
        """
        List campaigns in the ad account.

        Args:
            status_filter: Optional — "ACTIVE", "PAUSED", "ARCHIVED", etc.

        Returns:
            List of campaign dicts.
        """
        self._require_account()
        _log(f"Fetching campaigns (filter={status_filter})")
        try:
            fields = [
                "id",
                "name",
                "status",
                "effective_status",
                "objective",
                "daily_budget",
                "lifetime_budget",
                "budget_remaining",
                "start_time",
                "stop_time",
                "created_time",
                "updated_time",
                "buying_type",
                "bid_strategy",
                "special_ad_categories",
            ]
            params = {}
            if status_filter:
                params["effective_status"] = [status_filter]

            campaigns = self._account.get_campaigns(fields=fields, params=params)
            results = []
            for c in campaigns:
                results.append({
                    "id": c.get("id", ""),
                    "name": c.get("name", ""),
                    "status": c.get("status", ""),
                    "effective_status": c.get("effective_status", ""),
                    "objective": c.get("objective", ""),
                    "daily_budget": c.get("daily_budget", ""),
                    "lifetime_budget": c.get("lifetime_budget", ""),
                    "budget_remaining": c.get("budget_remaining", ""),
                    "start_time": c.get("start_time", ""),
                    "stop_time": c.get("stop_time", ""),
                    "created_time": c.get("created_time", ""),
                    "updated_time": c.get("updated_time", ""),
                    "buying_type": c.get("buying_type", ""),
                    "bid_strategy": c.get("bid_strategy", ""),
                    "special_ad_categories": c.get("special_ad_categories", []),
                })
            _log(f"Found {len(results)} campaigns")
            return results
        except Exception as exc:
            _log(f"get_campaigns error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]

    # ── Ad Sets ──────────────────────────────────────────────────────────

    def get_ad_sets(self, campaign_id: str) -> list[dict]:
        """List ad sets under a campaign."""
        self._require_sdk()
        _log(f"Fetching ad sets for campaign {campaign_id}")
        try:
            campaign = Campaign(campaign_id)
            fields = [
                "id",
                "name",
                "status",
                "effective_status",
                "daily_budget",
                "lifetime_budget",
                "budget_remaining",
                "optimization_goal",
                "billing_event",
                "bid_strategy",
                "bid_amount",
                "targeting",
                "start_time",
                "end_time",
                "created_time",
            ]
            ad_sets = campaign.get_ad_sets(fields=fields)
            results = []
            for s in ad_sets:
                results.append({
                    "id": s.get("id", ""),
                    "name": s.get("name", ""),
                    "status": s.get("status", ""),
                    "effective_status": s.get("effective_status", ""),
                    "daily_budget": s.get("daily_budget", ""),
                    "lifetime_budget": s.get("lifetime_budget", ""),
                    "budget_remaining": s.get("budget_remaining", ""),
                    "optimization_goal": s.get("optimization_goal", ""),
                    "billing_event": s.get("billing_event", ""),
                    "bid_strategy": s.get("bid_strategy", ""),
                    "bid_amount": s.get("bid_amount", ""),
                    "targeting": dict(s.get("targeting", {})) if s.get("targeting") else {},
                    "start_time": s.get("start_time", ""),
                    "end_time": s.get("end_time", ""),
                    "created_time": s.get("created_time", ""),
                })
            _log(f"Found {len(results)} ad sets for campaign {campaign_id}")
            return results
        except Exception as exc:
            _log(f"get_ad_sets error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]

    # ── Ads ──────────────────────────────────────────────────────────────

    def get_ads(self, ad_set_id: str) -> list[dict]:
        """List ads under an ad set."""
        self._require_sdk()
        _log(f"Fetching ads for ad set {ad_set_id}")
        try:
            ad_set = AdSet(ad_set_id)
            fields = [
                "id",
                "name",
                "status",
                "effective_status",
                "creative",
                "created_time",
                "updated_time",
            ]
            ads = ad_set.get_ads(fields=fields)
            results = []
            for a in ads:
                creative_data = a.get("creative", {})
                results.append({
                    "id": a.get("id", ""),
                    "name": a.get("name", ""),
                    "status": a.get("status", ""),
                    "effective_status": a.get("effective_status", ""),
                    "creative_id": creative_data.get("id", "") if isinstance(creative_data, dict) else str(creative_data),
                    "created_time": a.get("created_time", ""),
                    "updated_time": a.get("updated_time", ""),
                })
            _log(f"Found {len(results)} ads for ad set {ad_set_id}")
            return results
        except Exception as exc:
            _log(f"get_ads error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]

    # ── Insights ─────────────────────────────────────────────────────────

    def get_ad_insights(
        self,
        object_id: str,
        level: str = "ad",
        date_range: dict | None = None,
    ) -> list[dict]:
        """
        Pull performance metrics for a campaign, ad set, or ad.

        Metrics returned: impressions, reach, clicks, leads, CPL, CTR, CPM,
        frequency, video views (3s), hook rate (thumb_stop_ratio).

        Args:
            object_id:  Campaign ID, Ad Set ID, or Ad ID.
            level:      "campaign", "adset", or "ad".
            date_range: Optional {"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}.
                        Defaults to last 7 days.

        Returns:
            List of insight row dicts.
        """
        self._require_sdk()
        _log(f"Fetching insights for {object_id} (level={level})")

        if date_range is None:
            today = datetime.now().strftime("%Y-%m-%d")
            seven_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            date_range = {"since": seven_ago, "until": today}

        try:
            # Resolve the right SDK object
            if level == "campaign":
                obj = Campaign(object_id)
            elif level == "adset":
                obj = AdSet(object_id)
            else:
                obj = Ad(object_id)

            fields = [
                "impressions",
                "reach",
                "clicks",
                "spend",
                "ctr",
                "cpm",
                "cpp",
                "frequency",
                "actions",
                "cost_per_action_type",
                "video_30_sec_watched_actions",
                "video_p25_watched_actions",
                "video_p50_watched_actions",
                "video_p75_watched_actions",
                "video_p100_watched_actions",
                "video_play_actions",
                "date_start",
                "date_stop",
                "campaign_id",
                "campaign_name",
                "adset_id",
                "adset_name",
                "ad_id",
                "ad_name",
            ]
            params = {
                "time_range": date_range,
                "level": level,
            }

            insights = obj.get_insights(fields=fields, params=params)
            results = []
            for row in insights:
                # Parse leads from actions
                actions = row.get("actions") or []
                leads = 0
                for action in actions:
                    if action.get("action_type") in (
                        "lead",
                        "onsite_conversion.lead_grouped",
                        "offsite_conversion.fb_pixel_lead",
                    ):
                        try:
                            leads += int(action.get("value", 0))
                        except (ValueError, TypeError):
                            pass

                # Parse video 3-second plays
                video_views_3s = 0
                video_plays = 0
                for action in (row.get("video_play_actions") or []):
                    try:
                        video_plays += int(action.get("value", 0))
                    except (ValueError, TypeError):
                        pass

                for action in actions:
                    if action.get("action_type") == "video_view":
                        try:
                            video_views_3s += int(action.get("value", 0))
                        except (ValueError, TypeError):
                            pass

                spend = float(row.get("spend", 0))
                impressions = int(row.get("impressions", 0))
                reach = int(row.get("reach", 0))
                clicks = int(row.get("clicks", 0))
                frequency = float(row.get("frequency", 0))
                ctr = float(row.get("ctr", 0))
                cpm = float(row.get("cpm", 0))
                cpl = round(spend / leads, 2) if leads > 0 else 0.0

                # Hook rate: video 3s views / impressions
                hook_rate = round(video_views_3s / impressions, 4) if impressions > 0 else 0.0

                results.append({
                    "date_start": row.get("date_start", ""),
                    "date_stop": row.get("date_stop", ""),
                    "campaign_id": row.get("campaign_id", ""),
                    "campaign_name": row.get("campaign_name", ""),
                    "adset_id": row.get("adset_id", ""),
                    "adset_name": row.get("adset_name", ""),
                    "ad_id": row.get("ad_id", ""),
                    "ad_name": row.get("ad_name", ""),
                    "impressions": impressions,
                    "reach": reach,
                    "clicks": clicks,
                    "spend": spend,
                    "leads": leads,
                    "cpl": cpl,
                    "ctr": ctr,
                    "cpm": cpm,
                    "frequency": frequency,
                    "video_views_3s": video_views_3s,
                    "video_plays": video_plays,
                    "hook_rate": hook_rate,
                    "actions_raw": actions,
                    "cost_per_action": row.get("cost_per_action_type") or [],
                })

            _log(f"Got {len(results)} insight rows for {object_id}")
            return results
        except Exception as exc:
            _log(f"get_ad_insights error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]

    # ── Lead Forms & Leads ───────────────────────────────────────────────

    def get_lead_forms(self) -> list[dict]:
        """List all lead gen forms on the ad account's associated page."""
        self._require_account()
        _log("Fetching lead gen forms")
        try:
            # Lead forms live on the Page, not the ad account — but the SDK
            # can reach them via the ad account's leadgen_forms edge.
            fields = [
                "id",
                "name",
                "status",
                "leads_count",
                "created_time",
                "locale",
            ]
            forms = self._account.get_lead_gen_forms(fields=fields)
            results = []
            for f in forms:
                results.append({
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "status": f.get("status", ""),
                    "leads_count": f.get("leads_count", 0),
                    "created_time": f.get("created_time", ""),
                    "locale": f.get("locale", ""),
                })
            _log(f"Found {len(results)} lead forms")
            return results
        except Exception as exc:
            _log(f"get_lead_forms error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]

    def get_leads(self, form_id: str, since: str | None = None) -> list[dict]:
        """
        Download leads from a specific lead gen form.

        Args:
            form_id: The lead form ID.
            since:   Optional ISO date string — only return leads created after
                     this date (e.g. "2025-01-15").

        Returns:
            List of lead dicts with field_data parsed into key-value pairs.
        """
        self._require_sdk()
        _log(f"Fetching leads from form {form_id} (since={since})")
        try:
            form = LeadgenForm(form_id)
            fields = ["id", "created_time", "field_data"]
            params = {}
            if since:
                try:
                    dt = datetime.strptime(since, "%Y-%m-%d")
                    params["filtering"] = [
                        {
                            "field": "time_created",
                            "operator": "GREATER_THAN",
                            "value": int(dt.timestamp()),
                        }
                    ]
                except ValueError:
                    _log(f"Invalid 'since' date format: {since} — ignoring filter")

            leads = form.get_leads(fields=fields, params=params)
            results = []
            for lead in leads:
                # Parse field_data array into a flat dict
                parsed_fields = {}
                for field in (lead.get("field_data") or []):
                    name = field.get("name", "")
                    values = field.get("values", [])
                    parsed_fields[name] = values[0] if len(values) == 1 else values

                results.append({
                    "id": lead.get("id", ""),
                    "created_time": lead.get("created_time", ""),
                    "fields": parsed_fields,
                })
            _log(f"Got {len(results)} leads from form {form_id}")
            return results
        except Exception as exc:
            _log(f"get_leads error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]

    # ── Creative Assets ──────────────────────────────────────────────────

    def upload_image(self, image_path: str) -> str:
        """
        Upload an image to the ad account.

        Args:
            image_path: Local filesystem path to the image file.

        Returns:
            Image hash string (used in creative creation).
        """
        self._require_account()
        _log(f"Uploading image: {image_path}")
        try:
            image = AdImage(parent_id=self.ad_account_id)
            image[AdImage.Field.filename] = image_path
            image.remote_create()
            image_hash = image[AdImage.Field.hash]
            _log(f"Image uploaded — hash: {image_hash}")
            return image_hash
        except Exception as exc:
            _log(f"upload_image error: {exc}")
            traceback.print_exc()
            raise

    def upload_video(self, video_path: str) -> str:
        """
        Upload a video to the ad account.

        Args:
            video_path: Local filesystem path to the video file.

        Returns:
            Video ID string (used in creative creation).
        """
        self._require_account()
        _log(f"Uploading video: {video_path}")
        try:
            video = AdVideo(parent_id=self.ad_account_id)
            video[AdVideo.Field.filepath] = video_path
            video.remote_create()
            video_id = video.get_id()
            _log(f"Video uploaded — ID: {video_id}")
            return str(video_id)
        except Exception as exc:
            _log(f"upload_video error: {exc}")
            traceback.print_exc()
            raise

    # ── Creative ─────────────────────────────────────────────────────────

    def create_creative(
        self,
        name: str,
        image_hash_or_video_id: str,
        primary_text: str,
        headline: str,
        description: str,
        cta: str,
        link: str,
        form_id: str | None = None,
    ) -> dict:
        """
        Create an ad creative (image or video).

        Detects video vs image based on the format of `image_hash_or_video_id`:
        - If all hex chars and 32 chars long, treat as image hash.
        - Otherwise, treat as video ID.

        Args:
            name:                  Creative name.
            image_hash_or_video_id: Image hash from upload_image() or video ID
                                    from upload_video().
            primary_text:          Main ad body text.
            headline:              Headline shown below the media.
            description:           Link description / subheadline.
            cta:                   CTA type — e.g. "LEARN_MORE", "SIGN_UP",
                                   "GET_QUOTE", "BOOK_TRAVEL".
            link:                  Destination URL.
            form_id:               Optional lead gen form ID for instant forms.

        Returns:
            Dict with creative id and details.
        """
        self._require_account()
        _log(f"Creating creative: {name}")
        try:
            # Detect whether this is an image hash or video ID
            is_image = (
                len(image_hash_or_video_id) == 32
                and all(c in "0123456789abcdef" for c in image_hash_or_video_id.lower())
            )

            # Build the object_story_spec link_data or video_data
            call_to_action = {"type": cta, "value": {"link": link}}
            if form_id:
                call_to_action["value"]["lead_gen_form_id"] = form_id

            if is_image:
                link_data = {
                    "message": primary_text,
                    "link": link,
                    "name": headline,
                    "description": description,
                    "image_hash": image_hash_or_video_id,
                    "call_to_action": call_to_action,
                }
                object_story_spec = {
                    "page_id": self.config.get("fb_page_id", ""),
                    "link_data": link_data,
                }
            else:
                video_data = {
                    "message": primary_text,
                    "video_id": image_hash_or_video_id,
                    "title": headline,
                    "link_description": description,
                    "call_to_action": call_to_action,
                }
                object_story_spec = {
                    "page_id": self.config.get("fb_page_id", ""),
                    "video_data": video_data,
                }

            creative = AdCreative(parent_id=self.ad_account_id)
            creative[AdCreative.Field.name] = name
            creative[AdCreative.Field.object_story_spec] = object_story_spec
            creative.remote_create()

            creative_id = creative.get_id()
            _log(f"Creative created — ID: {creative_id}")
            return {
                "id": str(creative_id),
                "name": name,
                "type": "image" if is_image else "video",
            }
        except Exception as exc:
            _log(f"create_creative error: {exc}")
            traceback.print_exc()
            return {"error": str(exc)}

    # ── Campaign Creation ────────────────────────────────────────────────

    def create_campaign(
        self,
        name: str,
        objective: str = "OUTCOME_LEADS",
        budget_cents: int = 5000,
        status: str = "PAUSED",
    ) -> dict:
        """
        Create a new campaign (PAUSED by default).

        Args:
            name:         Campaign name.
            objective:    "OUTCOME_LEADS", "OUTCOME_TRAFFIC", "OUTCOME_AWARENESS",
                          "OUTCOME_ENGAGEMENT", "OUTCOME_SALES".
            budget_cents: Daily budget in cents (default $50.00).
            status:       "PAUSED" or "ACTIVE" (always use PAUSED for safety).

        Returns:
            Dict with campaign id and details.
        """
        self._require_account()
        _log(f"Creating campaign: {name} (objective={objective}, budget={budget_cents}, status={status})")
        try:
            params = {
                Campaign.Field.name: name,
                Campaign.Field.objective: objective,
                Campaign.Field.status: status,
                Campaign.Field.daily_budget: budget_cents,
                Campaign.Field.special_ad_categories: [],
            }
            campaign = self._account.create_campaign(params=params)
            campaign_id = campaign.get_id()
            _log(f"Campaign created — ID: {campaign_id}")
            return {
                "id": str(campaign_id),
                "name": name,
                "objective": objective,
                "daily_budget_cents": budget_cents,
                "status": status,
            }
        except Exception as exc:
            _log(f"create_campaign error: {exc}")
            traceback.print_exc()
            return {"error": str(exc)}

    # ── Ad Set Creation ──────────────────────────────────────────────────

    def create_ad_set(
        self,
        campaign_id: str,
        name: str,
        targeting_preset: str | None = None,
        targeting: dict | None = None,
        budget_cents: int = 2000,
        optimization_goal: str = "LEAD_GENERATION",
        bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
    ) -> dict:
        """
        Create an ad set under a campaign (PAUSED by default).

        Provide either `targeting_preset` (key from TARGETING_PRESETS) or a
        raw `targeting` dict.  Preset takes precedence if both are given.

        Args:
            campaign_id:       Parent campaign ID.
            name:              Ad set name.
            targeting_preset:  Key from TARGETING_PRESETS dict.
            targeting:         Raw targeting dict (Meta format).
            budget_cents:      Daily budget in cents (default $20.00).
            optimization_goal: "LEAD_GENERATION", "LINK_CLICKS", "IMPRESSIONS",
                               "REACH", "LANDING_PAGE_VIEWS".
            bid_strategy:      "LOWEST_COST_WITHOUT_CAP", "LOWEST_COST_WITH_BID_CAP",
                               "COST_CAP".

        Returns:
            Dict with ad set id and details.
        """
        self._require_account()
        _log(f"Creating ad set: {name} (campaign={campaign_id})")

        # Resolve targeting
        if targeting_preset:
            if targeting_preset not in TARGETING_PRESETS:
                return {
                    "error": f"Unknown targeting preset: {targeting_preset}. "
                    f"Available: {list(TARGETING_PRESETS.keys())}"
                }
            resolved_targeting = TARGETING_PRESETS[targeting_preset].copy()
            _log(f"Using targeting preset: {targeting_preset}")
        elif targeting:
            resolved_targeting = targeting
            _log("Using custom targeting dict")
        else:
            # Fallback to broad Advantage+
            resolved_targeting = TARGETING_PRESETS["broad_advantage_plus"].copy()
            _log("No targeting specified — defaulting to broad_advantage_plus")

        try:
            params = {
                AdSet.Field.name: name,
                AdSet.Field.campaign_id: campaign_id,
                AdSet.Field.daily_budget: budget_cents,
                AdSet.Field.billing_event: "IMPRESSIONS",
                AdSet.Field.optimization_goal: optimization_goal,
                AdSet.Field.bid_strategy: bid_strategy,
                AdSet.Field.targeting: resolved_targeting,
                AdSet.Field.status: "PAUSED",
            }
            ad_set = self._account.create_ad_set(params=params)
            ad_set_id = ad_set.get_id()
            _log(f"Ad set created — ID: {ad_set_id}")
            return {
                "id": str(ad_set_id),
                "name": name,
                "campaign_id": campaign_id,
                "daily_budget_cents": budget_cents,
                "optimization_goal": optimization_goal,
                "bid_strategy": bid_strategy,
                "targeting_preset": targeting_preset or "custom",
                "status": "PAUSED",
            }
        except Exception as exc:
            _log(f"create_ad_set error: {exc}")
            traceback.print_exc()
            return {"error": str(exc)}

    # ── Ad Creation ──────────────────────────────────────────────────────

    def create_ad(
        self,
        ad_set_id: str,
        creative_id: str,
        name: str,
        status: str = "PAUSED",
    ) -> dict:
        """
        Create an ad under an ad set (PAUSED by default).

        Args:
            ad_set_id:   Parent ad set ID.
            creative_id: Creative ID from create_creative().
            name:        Ad name.
            status:      "PAUSED" or "ACTIVE".

        Returns:
            Dict with ad id and details.
        """
        self._require_account()
        _log(f"Creating ad: {name} (ad_set={ad_set_id}, creative={creative_id})")
        try:
            params = {
                Ad.Field.name: name,
                Ad.Field.adset_id: ad_set_id,
                Ad.Field.creative: {"creative_id": creative_id},
                Ad.Field.status: status,
            }
            ad = self._account.create_ad(params=params)
            ad_id = ad.get_id()
            _log(f"Ad created — ID: {ad_id}")
            return {
                "id": str(ad_id),
                "name": name,
                "ad_set_id": ad_set_id,
                "creative_id": creative_id,
                "status": status,
            }
        except Exception as exc:
            _log(f"create_ad error: {exc}")
            traceback.print_exc()
            return {"error": str(exc)}

    # ── Ad Management ────────────────────────────────────────────────────

    def pause_ad(self, ad_id: str) -> dict:
        """Pause a specific ad."""
        self._require_sdk()
        _log(f"Pausing ad {ad_id}")
        try:
            ad = Ad(ad_id)
            ad.api_update(params={Ad.Field.status: "PAUSED"})
            _log(f"Ad {ad_id} paused")
            return {"id": ad_id, "status": "PAUSED", "ok": True}
        except Exception as exc:
            _log(f"pause_ad error: {exc}")
            traceback.print_exc()
            return {"id": ad_id, "error": str(exc), "ok": False}

    def update_budget(
        self,
        object_id: str,
        level: str,
        new_budget_cents: int,
    ) -> dict:
        """
        Update the daily budget for a campaign or ad set.

        ENFORCES the 20% max increase safety rule via scale_budget().

        Args:
            object_id:       Campaign or Ad Set ID.
            level:           "campaign" or "adset".
            new_budget_cents: Desired new daily budget in cents.

        Returns:
            Dict with old/new budget and whether it was capped.
        """
        self._require_sdk()
        _log(f"Updating budget for {object_id} (level={level}, target={new_budget_cents})")
        try:
            if level == "campaign":
                obj = Campaign(object_id)
                obj_data = obj.api_get(fields=["daily_budget"])
                current_budget = int(obj_data.get("daily_budget", 0))
            elif level == "adset":
                obj = AdSet(object_id)
                obj_data = obj.api_get(fields=["daily_budget"])
                current_budget = int(obj_data.get("daily_budget", 0))
            else:
                return {"error": f"Invalid level: {level}. Must be 'campaign' or 'adset'."}

            # Enforce 20% max increase rule
            safe_budget = scale_budget(current_budget, new_budget_cents)
            was_capped = safe_budget < new_budget_cents

            obj.api_update(params={"daily_budget": safe_budget})

            result = {
                "id": object_id,
                "level": level,
                "old_budget_cents": current_budget,
                "requested_budget_cents": new_budget_cents,
                "actual_budget_cents": safe_budget,
                "was_capped": was_capped,
                "ok": True,
            }
            if was_capped:
                _log(
                    f"Budget capped by 20% rule: requested {new_budget_cents}, "
                    f"set to {safe_budget} (was {current_budget})"
                )
            else:
                _log(f"Budget updated: {current_budget} -> {safe_budget}")
            return result
        except Exception as exc:
            _log(f"update_budget error: {exc}")
            traceback.print_exc()
            return {"id": object_id, "error": str(exc), "ok": False}

    # ── Relevance Diagnostics ────────────────────────────────────────────

    def get_relevance_diagnostics(self, ad_id: str) -> dict:
        """
        Get the ad relevance diagnostics (quality ranking, engagement rate
        ranking, conversion rate ranking).

        Args:
            ad_id: The ad ID.

        Returns:
            Dict with quality_ranking, engagement_rate_ranking,
            conversion_rate_ranking.
        """
        self._require_sdk()
        _log(f"Fetching relevance diagnostics for ad {ad_id}")
        try:
            ad = Ad(ad_id)
            fields = [
                "quality_ranking",
                "engagement_rate_ranking",
                "conversion_rate_ranking",
            ]
            insights = ad.get_insights(fields=fields, params={"date_preset": "last_7d"})

            if not insights:
                return {
                    "ad_id": ad_id,
                    "quality_ranking": "UNKNOWN",
                    "engagement_rate_ranking": "UNKNOWN",
                    "conversion_rate_ranking": "UNKNOWN",
                    "note": "Insufficient data (need ~500 impressions)",
                }

            row = insights[0]
            result = {
                "ad_id": ad_id,
                "quality_ranking": row.get("quality_ranking", "UNKNOWN"),
                "engagement_rate_ranking": row.get("engagement_rate_ranking", "UNKNOWN"),
                "conversion_rate_ranking": row.get("conversion_rate_ranking", "UNKNOWN"),
            }
            _log(
                f"Relevance: quality={result['quality_ranking']}, "
                f"engagement={result['engagement_rate_ranking']}, "
                f"conversion={result['conversion_rate_ranking']}"
            )
            return result
        except Exception as exc:
            _log(f"get_relevance_diagnostics error: {exc}")
            traceback.print_exc()
            return {"ad_id": ad_id, "error": str(exc)}

    # ── Automated Rules Engine ───────────────────────────────────────────

    def evaluate_automated_rules(self) -> list[dict]:
        """
        Check all active ads against AUTOMATED_RULES.

        Pulls last-7-day insights for each active ad, evaluates each rule's
        conditions, and returns a list of recommended actions.  Does NOT
        execute the actions — Kai reviews and approves.

        Returns:
            List of dicts:
            [
                {
                    "rule": "pause_high_cpl",
                    "ad_id": "123",
                    "ad_name": "...",
                    "action": "pause_ad",
                    "reason": "CPL $72.50 > $60 threshold, 2100 impressions",
                    "metrics": {...},
                },
                ...
            ]
        """
        self._require_account()
        _log("Evaluating automated rules across all active ads")

        recommendations: list[dict] = []

        try:
            # Get all active campaigns
            campaigns = self.get_campaigns(status_filter="ACTIVE")
            if not campaigns or (len(campaigns) == 1 and "error" in campaigns[0]):
                _log("No active campaigns found")
                return []

            for campaign in campaigns:
                campaign_id = campaign.get("id", "")
                if not campaign_id or "error" in campaign:
                    continue

                # Get insights at ad level for this campaign
                insights = self.get_ad_insights(
                    object_id=campaign_id,
                    level="ad",
                    date_range=None,  # defaults to last 7 days
                )

                for row in insights:
                    if "error" in row:
                        continue

                    ad_id = row.get("ad_id", "")
                    ad_name = row.get("ad_name", "")
                    impressions = row.get("impressions", 0)
                    leads = row.get("leads", 0)
                    spend = row.get("spend", 0)
                    cpl = row.get("cpl", 0)
                    ctr = row.get("ctr", 0)
                    frequency = row.get("frequency", 0)
                    date_start = row.get("date_start", "")
                    date_stop = row.get("date_stop", "")

                    # Calculate days in the date range
                    days = 1
                    try:
                        if date_start and date_stop:
                            d1 = datetime.strptime(date_start, "%Y-%m-%d")
                            d2 = datetime.strptime(date_stop, "%Y-%m-%d")
                            days = max((d2 - d1).days, 1)
                    except ValueError:
                        pass

                    metrics = {
                        "impressions": impressions,
                        "leads": leads,
                        "spend": spend,
                        "cpl": cpl,
                        "ctr": ctr,
                        "frequency": frequency,
                        "days": days,
                    }

                    # ── Evaluate each rule ────────────────────────────
                    # pause_high_cpl: cpl > 60 AND impressions > 1500
                    if cpl > 60 and impressions > 1500:
                        recommendations.append({
                            "rule": "pause_high_cpl",
                            "ad_id": ad_id,
                            "ad_name": ad_name,
                            "campaign_id": campaign_id,
                            "action": "pause_ad",
                            "reason": (
                                f"CPL ${cpl:.2f} > $60 threshold, "
                                f"{impressions:,} impressions"
                            ),
                            "metrics": metrics,
                        })

                    # scale_winner: cpl < 25 AND leads > 5 AND days >= 3
                    if cpl > 0 and cpl < 25 and leads > 5 and days >= 3:
                        recommendations.append({
                            "rule": "scale_winner",
                            "ad_id": ad_id,
                            "ad_name": ad_name,
                            "campaign_id": campaign_id,
                            "action": "increase_budget_15pct",
                            "reason": (
                                f"Winner: CPL ${cpl:.2f} < $25, "
                                f"{leads} leads over {days} days"
                            ),
                            "metrics": metrics,
                            "max_budget": AUTOMATED_RULES["scale_winner"]["max_budget"],
                        })

                    # fatigue_alert: frequency > 3.0
                    if frequency > 3.0:
                        recommendations.append({
                            "rule": "fatigue_alert",
                            "ad_id": ad_id,
                            "ad_name": ad_name,
                            "campaign_id": campaign_id,
                            "action": "alert",
                            "reason": (
                                f"Frequency {frequency:.1f} > 3.0 — "
                                f"audience fatigue likely"
                            ),
                            "metrics": metrics,
                        })

                    # low_ctr_pause: ctr < 0.005 AND impressions > 2000
                    if ctr < 0.005 and impressions > 2000:
                        recommendations.append({
                            "rule": "low_ctr_pause",
                            "ad_id": ad_id,
                            "ad_name": ad_name,
                            "campaign_id": campaign_id,
                            "action": "pause_ad",
                            "reason": (
                                f"CTR {ctr:.4f} < 0.5% threshold, "
                                f"{impressions:,} impressions"
                            ),
                            "metrics": metrics,
                        })

            _log(f"Automated rules evaluation complete: {len(recommendations)} recommendations")
            return recommendations

        except Exception as exc:
            _log(f"evaluate_automated_rules error: {exc}")
            traceback.print_exc()
            return [{"error": str(exc)}]
