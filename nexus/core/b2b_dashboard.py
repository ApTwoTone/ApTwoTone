"""
B2B Outreach Dashboard — HTML table with color-coded statuses.
Uses Zoar brand colors: Gold #C5A55A, Dark #1a1a2e, Light #f8f6f0.
"""
from __future__ import annotations

STATUS_COLORS = {
    "not_sent": "#888888",
    "pending_approval": "#e6a817",
    "approved": "#2196F3",
    "sent": "#4CAF50",
    "replied": "#9C27B0",
}

RATING_COLORS = {
    "HOT": "#e53935",
    "WARM": "#FB8C00",
    "COOL": "#42A5F5",
}


def _esc(text: str) -> str:
    """Escape HTML special characters to prevent XSS."""
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


def render_dashboard(leads: list[dict], stats: dict) -> str:
    """Render the B2B dashboard as a full HTML page."""
    # Stats bar
    stats_html = f"""
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px;">
        <div class="stat-card"><div class="stat-num">{int(stats.get('total',0))}</div><div class="stat-label">Total Leads</div></div>
        <div class="stat-card"><div class="stat-num">{int(stats.get('sent',0))}</div><div class="stat-label">Emails Sent</div></div>
        <div class="stat-card"><div class="stat-num">{int(stats.get('replied',0))}</div><div class="stat-label">Replies</div></div>
        <div class="stat-card"><div class="stat-num">{int(stats.get('pending_approval',0))}</div><div class="stat-label">Pending</div></div>
        <div class="stat-card"><div class="stat-num">{int(stats.get('eligible_next_batch',0))}</div><div class="stat-label">Eligible</div></div>
        <div class="stat-card"><div class="stat-num">{int(stats.get('no_email',0))}</div><div class="stat-label">No Email</div></div>
    </div>
    """

    # Table rows
    rows_html = ""
    for lead in leads:
        status = lead.get("email_status", "not_sent")
        status_color = STATUS_COLORS.get(status, "#888")
        rating = _esc(lead.get("rating", ""))
        rating_color = RATING_COLORS.get(lead.get("rating", ""), "#888")

        email_display = _esc(lead.get("email", "")) or "<em>none</em>"
        phone_display = _esc(lead.get("phone", ""))
        website_raw = lead.get("website", "") or ""
        website_escaped = _esc(website_raw)
        if website_raw and not website_raw.startswith(("http", "Via ", "Instagram")):
            website_display = f'<a href="https://{website_escaped}" target="_blank" rel="noopener" style="color:#C5A55A;">{website_escaped}</a>'
        else:
            website_display = website_escaped

        sent_date = _esc((lead.get("email_sent_at") or "")[:10])

        lead_id = int(lead.get("id", 0))
        rows_html += f"""
        <tr>
            <td>{int(lead.get('lead_number',0))}</td>
            <td><strong>{_esc(lead.get('business_name',''))}</strong></td>
            <td>{_esc(lead.get('category',''))}</td>
            <td>{_esc(lead.get('city',''))}</td>
            <td>T{int(lead.get('pricing_tier',0))}</td>
            <td><span style="color:{rating_color};font-weight:600;">{rating}</span></td>
            <td>{email_display}</td>
            <td>{phone_display}</td>
            <td>{website_display}</td>
            <td><span style="background:{status_color};color:#fff;padding:2px 8px;border-radius:12px;font-size:12px;">{_esc(status)}</span></td>
            <td>{sent_date}</td>
            <td><button onclick="viewLead({lead_id})" style="background:#C5A55A;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px;">View</button></td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>B2B Outreach Dashboard — Zoar Bathroom Rentals</title>
<style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family:'Helvetica Neue',Helvetica,Arial,sans-serif; background:#f8f6f0; color:#333; }}
    .header {{ background:#1a1a2e; padding:20px 32px; }}
    .header h1 {{ color:#C5A55A; font-size:20px; letter-spacing:1px; }}
    .header p {{ color:#aaa; font-size:12px; letter-spacing:2px; margin-top:4px; }}
    .container {{ max-width:1400px; margin:0 auto; padding:24px; }}
    .stat-card {{ background:#fff; border-radius:8px; padding:16px 24px; box-shadow:0 1px 4px rgba(0,0,0,0.08); min-width:120px; text-align:center; }}
    .stat-num {{ font-size:28px; font-weight:700; color:#1a1a2e; }}
    .stat-label {{ font-size:12px; color:#777; margin-top:4px; text-transform:uppercase; letter-spacing:1px; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,0.08); }}
    th {{ background:#1a1a2e; color:#C5A55A; padding:12px 10px; text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:1px; }}
    td {{ padding:10px; border-bottom:1px solid #eee; font-size:13px; }}
    tr:hover {{ background:#f5f3ee; }}
    .modal {{ display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:1000; }}
    .modal-content {{ background:#fff; max-width:600px; margin:60px auto; border-radius:12px; padding:32px; max-height:80vh; overflow-y:auto; }}
    .modal-close {{ float:right; cursor:pointer; font-size:24px; color:#888; }}
</style>
</head>
<body>
<div class="header">
    <h1>ZOAR — B2B OUTREACH DASHBOARD</h1>
    <p>LUXURY RESTROOM TRAILER PARTNERSHIPS</p>
</div>
<div class="container">
    {stats_html}
    <table>
        <thead>
            <tr>
                <th>#</th><th>Business</th><th>Category</th><th>City</th><th>Tier</th>
                <th>Rating</th><th>Email</th><th>Phone</th><th>Website</th>
                <th>Status</th><th>Sent</th><th></th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</div>

<div class="modal" id="leadModal">
    <div class="modal-content">
        <span class="modal-close" onclick="document.getElementById('leadModal').style.display='none'">&times;</span>
        <div id="leadDetail">Loading...</div>
    </div>
</div>

<script>
function escapeHtml(str) {{
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}}

async function viewLead(id) {{
    const modal = document.getElementById('leadModal');
    const detail = document.getElementById('leadDetail');
    modal.style.display = 'block';
    detail.textContent = 'Loading...';
    try {{
        const res = await fetch('/api/b2b/leads/' + encodeURIComponent(id));
        const l = await res.json();
        if (l.error) {{ detail.textContent = l.error; return; }}

        // Build detail view safely using DOM methods
        detail.textContent = '';

        const h2 = document.createElement('h2');
        h2.textContent = l.business_name || '';
        detail.appendChild(h2);

        const fields = [
            ['Category', l.category + ' | City: ' + (l.city || '') + ', ' + (l.state || '')],
            ['Distance', (l.distance_miles || 0) + ' mi (Tier ' + (l.pricing_tier || '') + ') | Rating: ' + (l.rating || '')],
            ['Contact', l.contact_name || 'N/A'],
            ['Email', l.email || 'N/A'],
            ['Phone', l.phone || 'N/A'],
            ['Website', l.website || 'N/A'],
            ['Email Status', l.email_status || ''],
            ['Outcome', l.outcome || ''],
            ['Sent', l.email_sent_at || 'Not yet'],
        ];
        fields.forEach(function(f) {{
            const p = document.createElement('p');
            const strong = document.createElement('strong');
            strong.textContent = f[0] + ': ';
            p.appendChild(strong);
            p.appendChild(document.createTextNode(f[1]));
            detail.appendChild(p);
        }});

        if (l.email_content) {{
            const h3 = document.createElement('h3');
            h3.textContent = 'Email Sent:';
            h3.style.marginTop = '12px';
            detail.appendChild(h3);
            const pre = document.createElement('pre');
            pre.style.cssText = 'white-space:pre-wrap;background:#f5f3ee;padding:12px;border-radius:6px;font-size:13px;';
            pre.textContent = l.email_content;
            detail.appendChild(pre);
        }}
        if (l.reply_content) {{
            const h3r = document.createElement('h3');
            h3r.textContent = 'Reply:';
            h3r.style.marginTop = '12px';
            detail.appendChild(h3r);
            const prer = document.createElement('pre');
            prer.style.cssText = 'white-space:pre-wrap;background:#e8f5e9;padding:12px;border-radius:6px;font-size:13px;';
            prer.textContent = l.reply_content;
            detail.appendChild(prer);
        }}
        if (l.notes) {{
            const pn = document.createElement('p');
            pn.style.marginTop = '12px';
            const sn = document.createElement('strong');
            sn.textContent = 'Notes: ';
            pn.appendChild(sn);
            pn.appendChild(document.createTextNode(l.notes));
            detail.appendChild(pn);
        }}
    }} catch(e) {{
        detail.textContent = 'Error loading lead: ' + e.message;
    }}
}}
document.getElementById('leadModal').addEventListener('click', function(e) {{
    if (e.target === this) this.style.display = 'none';
}});
</script>
</body>
</html>"""
