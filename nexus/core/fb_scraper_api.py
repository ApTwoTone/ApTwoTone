"""
FB Vendor Prospects — CRUD operations, duplicate detection, dashboard.
Stores vendors discovered by the Facebook group scraper.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, date, timezone

DB_PATH = Path.home() / ".nexus" / "memory.db"

VALID_STATUSES = {"new", "contacted", "responded", "on_referral_list", "not_interested", "do_not_contact"}
VALID_CATEGORIES = {
    "tent_rental", "party_rental", "event_planner", "caterer", "dj",
    "photographer", "bounce_house", "lighting", "florist", "photo_booth",
    "generator_rental", "dance_floor", "wedding_planner", "venue",
    "decorator", "other",
}
VALID_POST_TYPES = {"vendor_promotion", "event_announcement"}
UPDATABLE_FIELDS = {"status", "contact_method", "notes", "follow_up_date", "category", "city", "business_name", "phone", "email", "website", "referral_score", "activity_level"}
# Fields that affect scoring — trigger rescore on update
_SCORE_AFFECTING_FIELDS = {"category", "phone", "email", "website", "city", "business_name", "about"}


def _conn(db_path=None):
    c = sqlite3.connect(str(db_path or DB_PATH))
    c.row_factory = sqlite3.Row
    return c


# ── Create / Upsert ─────────────────────────────────────────────────────────

def upsert_vendor(data: dict, db_path=None) -> dict:
    """Insert a new vendor or update existing (by profile_url).
    Returns {"action": "created"|"updated"|"error", "id": int, ...}
    """
    conn = _conn(db_path)
    profile_url = (data.get("profile_url") or "").strip()
    if not profile_url:
        conn.close()
        return {"action": "error", "error": "profile_url is required"}

    existing = conn.execute(
        "SELECT * FROM fb_vendor_prospects WHERE profile_url = ?", (profile_url,)
    ).fetchone()

    if existing:
        existing = dict(existing)
        # Update fields that were previously empty
        updates = {}
        for field in ("business_name", "phone", "email", "website", "city", "category", "about"):
            new_val = (data.get(field) or "").strip()
            old_val = (existing.get(field) or "").strip()
            if new_val and not old_val:
                updates[field] = new_val
        # Always update post content if from a newer post
        if data.get("post_content"):
            updates["post_content"] = data["post_content"]
            updates["post_date"] = data.get("post_date", "")
            updates["post_url"] = data.get("post_url", "")
            updates["group_name"] = data.get("group_name", "")
            updates["group_url"] = data.get("group_url", "")
        if updates:
            # Rescore if any score-affecting fields changed
            if updates.keys() & _SCORE_AFFECTING_FIELDS:
                from core.vendor_scoring import score_prospect
                merged = {**existing, **updates}
                scores = score_prospect(merged)
                updates["referral_score"] = scores["referral_score"]
                updates["activity_level"] = scores["activity_level"]
            updates["updated_at"] = datetime.now(timezone.utc).isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE fb_vendor_prospects SET {set_clause} WHERE id = ?",
                list(updates.values()) + [existing["id"]],
            )
            conn.commit()
        conn.close()
        return {"action": "updated", "id": existing["id"]}

    # New insert
    conn.execute("""
        INSERT INTO fb_vendor_prospects
        (poster_name, profile_url, business_name, phone, email, website,
         city, category, about, post_content, post_date, post_url,
         group_name, group_url, post_type, image_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("poster_name", ""),
        profile_url,
        data.get("business_name", ""),
        data.get("phone", ""),
        data.get("email", ""),
        data.get("website", ""),
        data.get("city", ""),
        data.get("category", ""),
        data.get("about", ""),
        data.get("post_content", ""),
        data.get("post_date", ""),
        data.get("post_url", ""),
        data.get("group_name", ""),
        data.get("group_url", ""),
        data.get("post_type", "vendor_promotion"),
        data.get("image_count", 0),
    ))
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Compute and store lead scores
    from core.vendor_scoring import score_prospect
    scores = score_prospect(data)
    conn.execute(
        "UPDATE fb_vendor_prospects SET referral_score = ?, activity_level = ? WHERE id = ?",
        (scores["referral_score"], scores["activity_level"], new_id),
    )
    conn.commit()
    conn.close()
    return {"action": "created", "id": new_id}


# ── Read ─────────────────────────────────────────────────────────────────────

def get_prospect(prospect_id: int, db_path=None):
    conn = _conn(db_path)
    row = conn.execute("SELECT * FROM fb_vendor_prospects WHERE id = ?", (prospect_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_prospects(db_path=None, **filters) -> list[dict]:
    """Get prospects with optional filters: status, category, city, post_type."""
    conn = _conn(db_path)
    query = "SELECT * FROM fb_vendor_prospects WHERE 1=1"
    params = []

    if filters.get("status"):
        query += " AND status = ?"
        params.append(filters["status"])
    if filters.get("category"):
        query += " AND category = ?"
        params.append(filters["category"])
    if filters.get("city"):
        query += " AND city LIKE ?"
        params.append(f"%{filters['city']}%")
    if filters.get("post_type"):
        query += " AND post_type = ?"
        params.append(filters["post_type"])

    query += " ORDER BY scraped_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_today_prospects(db_path=None) -> list[dict]:
    """Get all prospects scraped today."""
    conn = _conn(db_path)
    today = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM fb_vendor_prospects WHERE DATE(scraped_at) = ? ORDER BY scraped_at DESC",
        (today,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _referral_leads_as_prospects(conn) -> list:
    """Read referral_leads and map columns to fb_vendor_prospects schema."""
    try:
        rows = conn.execute(
            "SELECT id, name, business_name, job_title, city, phone, email, "
            "website, profile_url, qualification_score, outreach_status, "
            "facebook_group, facebook_group_url, post_text, post_date, "
            "post_url, notes, created_at FROM referral_leads"
        ).fetchall()
    except Exception:
        return []
    results = []
    for r in rows:
        r = dict(r)
        results.append({
            "id": -(r["id"]),  # negative IDs to avoid collisions with fb_vendor_prospects
            "poster_name": r.get("name") or "",
            "business_name": r.get("business_name") or "",
            "category": r.get("job_title") or "",
            "city": r.get("city") or "",
            "phone": r.get("phone") or "",
            "email": r.get("email") or "",
            "website": r.get("website") or "",
            "profile_url": r.get("profile_url") or "",
            "referral_score": r.get("qualification_score") or 0,
            "activity_level": 0,
            "status": r.get("outreach_status") or "new",
            "post_type": "vendor_promotion",
            "post_content": r.get("post_text") or "",
            "post_date": r.get("post_date") or "",
            "post_url": r.get("post_url") or "",
            "group_name": r.get("facebook_group") or "",
            "group_url": r.get("facebook_group_url") or "",
            "about": "",
            "notes": r.get("notes") or "",
            "contact_method": "",
            "follow_up_date": "",
            "image_count": 0,
            "scraped_at": r.get("created_at") or "",
            "updated_at": "",
            "_source": "referral_leads",
        })
    return results


def get_prospects_scored(db_path=None, **filters) -> list[dict]:
    """Get prospects with scoring support: min_score, sort, limit, offset + standard filters.
    Merges fb_vendor_prospects AND referral_leads into one view.
    """
    conn = _conn(db_path)
    query = "SELECT * FROM fb_vendor_prospects WHERE 1=1"
    params = []

    if filters.get("status"):
        query += " AND status = ?"
        params.append(filters["status"])
    if filters.get("category"):
        query += " AND category = ?"
        params.append(filters["category"])
    if filters.get("city"):
        query += " AND city LIKE ?"
        params.append(f"%{filters['city']}%")
    if filters.get("post_type"):
        query += " AND post_type = ?"
        params.append(filters["post_type"])
    if filters.get("min_score"):
        query += " AND referral_score >= ?"
        params.append(int(filters["min_score"]))
    if filters.get("search"):
        query += " AND (poster_name LIKE ? OR business_name LIKE ? OR about LIKE ?)"
        s = f"%{filters['search']}%"
        params.extend([s, s, s])

    rows = conn.execute(query, params).fetchall()
    fb_results = [dict(r) for r in rows]

    # Merge referral_leads (mapped to same schema)
    ref_results = _referral_leads_as_prospects(conn)
    conn.close()

    # Apply same filters to referral_leads results
    if filters.get("status"):
        ref_results = [r for r in ref_results if r["status"] == filters["status"]]
    if filters.get("category"):
        ref_results = [r for r in ref_results if r["category"] == filters["category"]]
    if filters.get("city"):
        city_q = filters["city"].lower()
        ref_results = [r for r in ref_results if city_q in (r.get("city") or "").lower()]
    if filters.get("min_score"):
        ms = int(filters["min_score"])
        ref_results = [r for r in ref_results if (r.get("referral_score") or 0) >= ms]
    if filters.get("search"):
        sq = filters["search"].lower()
        ref_results = [r for r in ref_results if (
            sq in (r.get("poster_name") or "").lower()
            or sq in (r.get("business_name") or "").lower()
            or sq in (r.get("about") or "").lower()
        )]

    combined = fb_results + ref_results

    # Sort
    sort_col = filters.get("sort", "referral_score")
    if sort_col not in ("referral_score", "activity_level", "scraped_at", "poster_name", "category"):
        sort_col = "referral_score"
    reverse = filters.get("sort_dir", "desc").lower() != "asc"
    if sort_col in ("referral_score", "activity_level"):
        combined.sort(key=lambda r: r.get(sort_col) or 0, reverse=reverse)
    else:
        combined.sort(key=lambda r: str(r.get(sort_col) or ""), reverse=reverse)

    # Pagination
    if filters.get("limit"):
        offset = int(filters.get("offset", 0))
        limit = int(filters["limit"])
        combined = combined[offset:offset + limit]

    return combined


def get_stats_scored(db_path=None) -> dict:
    """Enhanced stats with score distribution across both tables."""
    conn = _conn(db_path)
    base = get_stats(db_path)

    # fb_vendor_prospects scores
    fb_high = conn.execute("SELECT COUNT(*) FROM fb_vendor_prospects WHERE referral_score >= 70").fetchone()[0]
    fb_med = conn.execute("SELECT COUNT(*) FROM fb_vendor_prospects WHERE referral_score >= 40 AND referral_score < 70").fetchone()[0]
    fb_low = conn.execute("SELECT COUNT(*) FROM fb_vendor_prospects WHERE referral_score < 40").fetchone()[0]
    fb_avg = conn.execute("SELECT AVG(referral_score) FROM fb_vendor_prospects").fetchone()[0] or 0

    # referral_leads scores
    try:
        rl_total = conn.execute("SELECT COUNT(*) FROM referral_leads").fetchone()[0]
        rl_high = conn.execute("SELECT COUNT(*) FROM referral_leads WHERE qualification_score >= 70").fetchone()[0]
        rl_med = conn.execute("SELECT COUNT(*) FROM referral_leads WHERE qualification_score >= 40 AND qualification_score < 70").fetchone()[0]
        rl_low = conn.execute("SELECT COUNT(*) FROM referral_leads WHERE qualification_score < 40").fetchone()[0]
        rl_avg = conn.execute("SELECT AVG(qualification_score) FROM referral_leads").fetchone()[0] or 0
        rl_today = conn.execute(
            "SELECT COUNT(*) FROM referral_leads WHERE DATE(created_at) = ?",
            (date.today().isoformat(),),
        ).fetchone()[0]
    except Exception:
        rl_total = rl_high = rl_med = rl_low = rl_today = 0
        rl_avg = 0

    conn.close()

    total_all = base["total"] + rl_total
    high = fb_high + rl_high
    medium = fb_med + rl_med
    low = fb_low + rl_low
    avg_score = round((fb_avg * base["total"] + rl_avg * rl_total) / max(total_all, 1), 1)

    base["total"] = total_all
    base["today"] = base.get("today", 0) + rl_today
    return {**base, "high_fit": high, "medium_fit": medium, "low_fit": low, "score_avg": avg_score}


def rescore_all(db_path=None) -> int:
    """Recompute scores for all prospects. Returns count updated."""
    from core.vendor_scoring import score_prospect
    conn = _conn(db_path)
    rows = conn.execute("SELECT * FROM fb_vendor_prospects").fetchall()
    count = 0
    for row in rows:
        p = dict(row)
        scores = score_prospect(p)
        conn.execute(
            "UPDATE fb_vendor_prospects SET referral_score = ?, activity_level = ? WHERE id = ?",
            (scores["referral_score"], scores["activity_level"], p["id"]),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


# ── Update ───────────────────────────────────────────────────────────────────

def update_prospect(prospect_id: int, updates: dict, db_path=None) -> bool:
    """Update allowed fields on a prospect. Returns True if updated."""
    allowed = {k: v for k, v in updates.items() if k in UPDATABLE_FIELDS}
    if not allowed:
        return False
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    conn = _conn(db_path)
    set_clause = ", ".join(f"{k} = ?" for k in allowed)
    conn.execute(
        f"UPDATE fb_vendor_prospects SET {set_clause} WHERE id = ?",
        list(allowed.values()) + [prospect_id],
    )
    conn.commit()
    conn.close()
    return True


# ── Stats ────────────────────────────────────────────────────────────────────

def get_stats(db_path=None) -> dict:
    conn = _conn(db_path)
    total = conn.execute("SELECT COUNT(*) FROM fb_vendor_prospects").fetchone()[0]

    by_status = {}
    for row in conn.execute("SELECT status, COUNT(*) as cnt FROM fb_vendor_prospects GROUP BY status"):
        by_status[row["status"]] = row["cnt"]

    by_category = {}
    for row in conn.execute("SELECT category, COUNT(*) as cnt FROM fb_vendor_prospects WHERE category != '' GROUP BY category ORDER BY cnt DESC"):
        by_category[row["category"]] = row["cnt"]

    by_post_type = {}
    for row in conn.execute("SELECT post_type, COUNT(*) as cnt FROM fb_vendor_prospects GROUP BY post_type"):
        by_post_type[row["post_type"]] = row["cnt"]

    today_count = conn.execute(
        "SELECT COUNT(*) FROM fb_vendor_prospects WHERE DATE(scraped_at) = ?",
        (date.today().isoformat(),),
    ).fetchone()[0]

    conn.close()
    return {
        "total": total,
        "today": today_count,
        "by_status": by_status,
        "by_category": by_category,
        "by_post_type": by_post_type,
    }


# ── Dashboard HTML ───────────────────────────────────────────────────────────

STATUS_COLORS = {
    "new": "#2196F3",
    "contacted": "#e6a817",
    "responded": "#4CAF50",
    "on_referral_list": "#C5A55A",
    "not_interested": "#888888",
    "do_not_contact": "#e53935",
}


def _esc(text: str) -> str:
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def render_dashboard(prospects: list[dict], stats: dict) -> str:
    stats_html = f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px;">
        <div class="stat-card"><div class="stat-num">{stats.get('total',0)}</div><div class="stat-label">Total</div></div>
        <div class="stat-card"><div class="stat-num">{stats.get('today',0)}</div><div class="stat-label">Today</div></div>
        <div class="stat-card"><div class="stat-num">{stats.get('by_status',{}).get('new',0)}</div><div class="stat-label">New</div></div>
        <div class="stat-card"><div class="stat-num">{stats.get('by_status',{}).get('contacted',0)}</div><div class="stat-label">Contacted</div></div>
        <div class="stat-card"><div class="stat-num">{stats.get('by_status',{}).get('responded',0)}</div><div class="stat-label">Responded</div></div>
        <div class="stat-card"><div class="stat-num">{stats.get('by_status',{}).get('on_referral_list',0)}</div><div class="stat-label">On List</div></div>
    </div>
    """

    rows_html = ""
    for p in prospects:
        status = p.get("status", "new")
        status_color = STATUS_COLORS.get(status, "#888")
        phone = _esc(p.get("phone", "")) or "—"
        website_raw = p.get("website", "") or ""
        website_escaped = _esc(website_raw)
        if website_raw and website_raw.startswith("http"):
            website_display = f'<a href="{website_escaped}" target="_blank" rel="noopener" style="color:#C5A55A;">{website_escaped[:30]}</a>'
        elif website_raw:
            website_display = f'<a href="https://{website_escaped}" target="_blank" rel="noopener" style="color:#C5A55A;">{website_escaped[:30]}</a>'
        else:
            website_display = "—"

        found_date = _esc((p.get("scraped_at", "") or "")[:10])
        post_type_label = "Vendor" if p.get("post_type") == "vendor_promotion" else "Event Lead"
        pid = int(p.get("id", 0))

        rows_html += f"""
        <tr data-id="{pid}" style="cursor:pointer;">
            <td>{pid}</td>
            <td><strong>{_esc(p.get('poster_name',''))}</strong></td>
            <td>{_esc(p.get('business_name','')) or '—'}</td>
            <td>{_esc(p.get('category','')) or '—'}</td>
            <td>{_esc(p.get('city','')) or '—'}</td>
            <td>{phone}</td>
            <td>{website_display}</td>
            <td><span style="background:{status_color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8em;">{_esc(status)}</span></td>
            <td>{post_type_label}</td>
            <td>{found_date}</td>
            <td>{_esc(p.get('group_name',''))[:20]}</td>
        </tr>
        """

    # Build prospect data as JSON for the detail modal (server-escaped, safe)
    import json
    prospects_json = json.dumps([
        {k: _esc(str(v)) if isinstance(v, str) else v for k, v in p.items()}
        for p in prospects
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FB Vendor Prospects — Nexus</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,sans-serif; background:#1a1a2e; color:#f8f6f0; padding:24px; }}
  h1 {{ color:#C5A55A; margin-bottom:8px; }}
  .stat-card {{ background:#2a2a4e; border-radius:8px; padding:16px 24px; min-width:100px; text-align:center; }}
  .stat-num {{ font-size:28px; font-weight:700; color:#C5A55A; }}
  .stat-label {{ font-size:12px; color:#aaa; margin-top:4px; }}
  .filters {{ display:flex; gap:12px; margin-bottom:16px; flex-wrap:wrap; }}
  .filters select {{ background:#2a2a4e; color:#f8f6f0; border:1px solid #444; padding:6px 12px; border-radius:4px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th {{ background:#2a2a4e; padding:10px 8px; text-align:left; color:#C5A55A; position:sticky; top:0; }}
  td {{ padding:8px; border-bottom:1px solid #333; }}
  tr:hover {{ background:#2a2a4e; }}
  #detail-modal {{ display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.8); z-index:100; overflow-y:auto; }}
  .modal-content {{ background:#1a1a2e; max-width:700px; margin:40px auto; padding:24px; border-radius:8px; border:1px solid #C5A55A; }}
  .modal-content h2 {{ color:#C5A55A; margin-bottom:12px; }}
  .modal-content pre {{ background:#2a2a4e; padding:12px; border-radius:4px; white-space:pre-wrap; word-wrap:break-word; font-size:13px; margin:8px 0; }}
  .field {{ margin:6px 0; }} .label {{ color:#888; display:inline-block; min-width:100px; }}
  .close-btn {{ float:right; cursor:pointer; color:#888; font-size:24px; }} .close-btn:hover {{ color:#fff; }}
</style>
</head>
<body>
<h1>FB Vendor Prospects</h1>
<p style="color:#888;margin-bottom:16px;">Facebook group vendor discovery pipeline</p>
{stats_html}
<div class="filters">
  <select id="f-status" onchange="applyFilters()"><option value="">All Statuses</option>
    <option value="new">New</option><option value="contacted">Contacted</option>
    <option value="responded">Responded</option><option value="on_referral_list">On Referral List</option>
    <option value="not_interested">Not Interested</option><option value="do_not_contact">Do Not Contact</option>
  </select>
  <select id="f-type" onchange="applyFilters()"><option value="">All Types</option>
    <option value="vendor_promotion">Vendor Promotion</option><option value="event_announcement">Event Lead</option>
  </select>
</div>
<table>
<thead><tr>
  <th>ID</th><th>Name</th><th>Business</th><th>Category</th><th>City</th>
  <th>Phone</th><th>Website</th><th>Status</th><th>Type</th><th>Found</th><th>Group</th>
</tr></thead>
<tbody id="prospect-rows">{rows_html}</tbody>
</table>
<div id="detail-modal">
  <div class="modal-content" id="detail-body"></div>
</div>
<script>
// Pre-escaped prospect data from server
var _prospects = {prospects_json};
var _byId = {{}};
_prospects.forEach(function(p) {{ _byId[p.id] = p; }});

function applyFilters() {{
  var status = document.getElementById('f-status').value;
  var ptype = document.getElementById('f-type').value;
  var params = [];
  if (status) params.push('status=' + encodeURIComponent(status));
  if (ptype) params.push('post_type=' + encodeURIComponent(ptype));
  window.location.href = '/api/fb/dashboard' + (params.length ? '?' + params.join('&') : '');
}}

function showDetail(id) {{
  var p = _byId[id];
  if (!p) return;
  var modal = document.getElementById('detail-body');
  // Clear previous content safely
  while (modal.firstChild) modal.removeChild(modal.firstChild);

  // Close button
  var closeBtn = document.createElement('span');
  closeBtn.className = 'close-btn';
  closeBtn.textContent = '\\u00d7';
  closeBtn.onclick = function() {{ document.getElementById('detail-modal').style.display = 'none'; }};
  modal.appendChild(closeBtn);

  // Title
  var h2 = document.createElement('h2');
  h2.textContent = '#' + p.id + ' \\u2014 ' + (p.poster_name || '');
  modal.appendChild(h2);

  var fields = [
    ['Business', p.business_name], ['Category', p.category], ['City', p.city],
    ['Phone', p.phone], ['Email', p.email], ['Status', p.status],
    ['Contact Method', p.contact_method], ['Notes', p.notes],
    ['Follow-up', p.follow_up_date], ['Group', p.group_name],
    ['Post Type', p.post_type === 'vendor_promotion' ? 'Vendor Promotion' : 'Event Lead'],
    ['Images', String(p.image_count || 0)], ['Scraped', p.scraped_at]
  ];
  fields.forEach(function(pair) {{
    var div = document.createElement('div');
    div.className = 'field';
    var lbl = document.createElement('span');
    lbl.className = 'label';
    lbl.textContent = pair[0] + ': ';
    div.appendChild(lbl);
    var val = document.createTextNode(pair[1] || '\\u2014');
    div.appendChild(val);
    modal.appendChild(div);
  }});

  // Website link
  if (p.website) {{
    var wDiv = document.createElement('div');
    wDiv.className = 'field';
    var wLbl = document.createElement('span');
    wLbl.className = 'label';
    wLbl.textContent = 'Website: ';
    wDiv.appendChild(wLbl);
    var wLink = document.createElement('a');
    wLink.href = p.website.indexOf('http') === 0 ? p.website : 'https://' + p.website;
    wLink.target = '_blank';
    wLink.rel = 'noopener';
    wLink.textContent = p.website;
    wLink.style.color = '#C5A55A';
    wDiv.appendChild(wLink);
    modal.appendChild(wDiv);
  }}

  // Profile link
  if (p.profile_url) {{
    var pDiv = document.createElement('div');
    pDiv.className = 'field';
    var pLbl = document.createElement('span');
    pLbl.className = 'label';
    pLbl.textContent = 'Profile: ';
    pDiv.appendChild(pLbl);
    var pLink = document.createElement('a');
    pLink.href = p.profile_url;
    pLink.target = '_blank';
    pLink.rel = 'noopener';
    pLink.textContent = p.profile_url;
    pLink.style.color = '#C5A55A';
    pDiv.appendChild(pLink);
    modal.appendChild(pDiv);
  }}

  // About (pre block)
  if (p.about) {{
    var aLbl = document.createElement('div');
    aLbl.className = 'field';
    var aSpan = document.createElement('span');
    aSpan.className = 'label';
    aSpan.textContent = 'About:';
    aLbl.appendChild(aSpan);
    modal.appendChild(aLbl);
    var aPre = document.createElement('pre');
    aPre.textContent = p.about;
    modal.appendChild(aPre);
  }}

  // Post content (pre block)
  if (p.post_content) {{
    var pcLbl = document.createElement('div');
    pcLbl.className = 'field';
    var pcSpan = document.createElement('span');
    pcSpan.className = 'label';
    pcSpan.textContent = 'Post Content:';
    pcLbl.appendChild(pcSpan);
    modal.appendChild(pcLbl);
    var pcPre = document.createElement('pre');
    pcPre.textContent = p.post_content;
    modal.appendChild(pcPre);
  }}

  document.getElementById('detail-modal').style.display = 'block';
}}

// Click handler for table rows
document.getElementById('prospect-rows').addEventListener('click', function(e) {{
  var row = e.target.closest('tr[data-id]');
  if (row) showDetail(parseInt(row.getAttribute('data-id'), 10));
}});

// Close modal on background click
document.getElementById('detail-modal').addEventListener('click', function(e) {{
  if (e.target.id === 'detail-modal') e.target.style.display = 'none';
}});
</script>
</body></html>"""
