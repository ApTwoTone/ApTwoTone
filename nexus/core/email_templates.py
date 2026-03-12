"""
Professional HTML Email Templates for Zoar Bathroom Rentals.

Provides branded, mobile-responsive email templates for:
- Initial outreach
- Follow-up sequences
- Quote/pricing emails
- Booking confirmation
- General communication

All templates use inline CSS for maximum email client compatibility.
No external images or tracking — everything self-contained.
"""

# Brand colors
BRAND_GOLD = "#C5A55A"
BRAND_DARK = "#1a1a2e"
BRAND_LIGHT = "#f8f6f0"
BRAND_TEXT = "#333333"
BRAND_MUTED = "#777777"


def _base_template(body_html: str, preheader: str = "") -> str:
    """Wrap content in the branded email layout."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Zoar Bathroom Rentals</title>
</head>
<body style="margin:0;padding:0;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;background-color:{BRAND_LIGHT};color:{BRAND_TEXT};">
<!-- Preheader -->
<span style="display:none;font-size:1px;color:{BRAND_LIGHT};line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
{preheader}
</span>
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:{BRAND_LIGHT};padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
<!-- Header -->
<tr>
<td style="background:{BRAND_DARK};padding:24px 32px;text-align:center;">
<h1 style="margin:0;color:{BRAND_GOLD};font-size:22px;font-weight:600;letter-spacing:1px;">ZOAR BATHROOM RENTALS</h1>
<p style="margin:6px 0 0;color:#aaaaaa;font-size:12px;letter-spacing:2px;">LUXURY RESTROOM TRAILERS</p>
</td>
</tr>
<!-- Body -->
<tr>
<td style="padding:32px 32px 24px;">
{body_html}
</td>
</tr>
<!-- Footer -->
<tr>
<td style="background:#f5f5f5;padding:24px 32px;border-top:1px solid #e8e8e8;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td width="80" style="vertical-align:top;padding-right:12px;">
<img src="cid:zoar_logo" alt="Zoar Bathroom Rentals" width="70" style="border-radius:4px;">
</td>
<td style="vertical-align:top;font-size:13px;color:{BRAND_MUTED};line-height:1.6;">
<strong style="color:{BRAND_TEXT};">Zoar Bathroom Rentals</strong><br>
Luxury Restroom Trailers for Any Occasion<br>
<a href="tel:+14242358979" style="color:{BRAND_GOLD};text-decoration:none;">(424) 235-8979</a> &nbsp;|&nbsp;
<a href="mailto:zoarbathrooms@gmail.com" style="color:{BRAND_GOLD};text-decoration:none;">zoarbathrooms@gmail.com</a><br>
<a href="https://zoarbathroomrental.com" style="color:{BRAND_GOLD};text-decoration:none;">zoarbathroomrental.com</a>
</td>
</tr>
<tr>
<td colspan="2" style="padding-top:16px;font-size:11px;color:#aaaaaa;">
Serving Los Angeles, San Fernando Valley, Santa Clarita, Ventura, Oxnard &amp; Santa Monica
</td>
</tr>
</table>
</td>
</tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def initial_outreach(first_name: str, body_text: str, subject: str = "") -> tuple[str, str]:
    """
    Generate branded HTML for initial outreach email.
    Returns (html_body, plain_text_fallback)
    """
    # Convert plain text body to HTML paragraphs
    paragraphs = body_text.strip().split("\n\n")
    body_html_parts = []
    for p in paragraphs:
        lines = p.strip().split("\n")
        text = "<br>".join(lines)
        body_html_parts.append(f'<p style="margin:0 0 16px;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">{text}</p>')

    body_html = "\n".join(body_html_parts)

    # Add CTA button
    body_html += f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td align="center">
<a href="tel:+14242358979" style="display:inline-block;background:{BRAND_GOLD};color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-size:15px;font-weight:600;letter-spacing:0.5px;">
Call Now: (424) 235-8979
</a>
</td></tr>
</table>
<p style="margin:0;text-align:center;font-size:13px;color:{BRAND_MUTED};">Or reply to this email — we respond within minutes!</p>
"""

    html = _base_template(body_html, f"Hi {first_name}! Luxury restroom trailer for your event...")
    return html, body_text


def follow_up(first_name: str, body_text: str, step_type: str = "gentle") -> tuple[str, str]:
    """Generate branded HTML for follow-up emails."""
    paragraphs = body_text.strip().split("\n\n")
    body_html_parts = []
    for p in paragraphs:
        lines = p.strip().split("\n")
        text = "<br>".join(lines)
        body_html_parts.append(f'<p style="margin:0 0 16px;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">{text}</p>')

    body_html = "\n".join(body_html_parts)

    # Add CTA based on follow-up type
    if step_type in ("urgency", "final_chance"):
        cta_text = "Check Availability"
        cta_color = "#d4534a"
    else:
        cta_text = "Get a Free Quote"
        cta_color = BRAND_GOLD

    body_html += f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0;">
<tr><td align="center">
<a href="https://zoarbathroomrental.com/#contact" style="display:inline-block;background:{cta_color};color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-size:15px;font-weight:600;letter-spacing:0.5px;">
{cta_text}
</a>
</td></tr>
</table>
"""

    preheader = "Quick follow-up about your event..." if step_type == "gentle" else "Dates are filling up..."
    html = _base_template(body_html, preheader)
    return html, body_text


def quote_email(first_name: str, event_type: str, event_date: str, quote_amount: float,
                includes: list = None) -> tuple[str, str]:
    """Generate branded HTML quote email."""
    if includes is None:
        includes = [
            "4-stall luxury restroom trailer",
            "Climate control (AC/Heat)",
            "LED lighting & full-length mirrors",
            "Flushing porcelain toilets & running water",
            "Bluetooth speaker system",
            "Delivery, setup & pickup included",
            "Fresh water & waste tank service",
        ]

    includes_html = "".join(
        f'<tr><td style="padding:6px 0;font-size:14px;color:{BRAND_TEXT};">✓ {item}</td></tr>'
        for item in includes
    )

    event_info = f"{event_type}" if event_type else "Your Event"
    date_info = f" on {event_date}" if event_date else ""

    body_html = f"""
<p style="margin:0 0 16px;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Hey {first_name},
</p>
<p style="margin:0 0 24px;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Thanks for your interest! Here's your custom quote for <strong>{event_info}</strong>{date_info}:
</p>

<!-- Quote Box -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:{BRAND_LIGHT};border-radius:8px;border:2px solid {BRAND_GOLD};margin:0 0 24px;">
<tr>
<td style="padding:24px;">
<p style="margin:0 0 4px;font-size:13px;color:{BRAND_MUTED};text-transform:uppercase;letter-spacing:1px;">Your Quote</p>
<p style="margin:0 0 16px;font-size:36px;font-weight:700;color:{BRAND_DARK};">${quote_amount:,.0f}</p>
<p style="margin:0 0 16px;font-size:13px;color:{BRAND_MUTED};">Delivery, setup, and pickup included</p>
<table cellpadding="0" cellspacing="0">
{includes_html}
</table>
</td>
</tr>
</table>

<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
<tr><td align="center">
<a href="tel:+14242358979" style="display:inline-block;background:{BRAND_GOLD};color:#ffffff;text-decoration:none;padding:14px 32px;border-radius:8px;font-size:15px;font-weight:600;">
Book Now — Call (424) 235-8979
</a>
</td></tr>
</table>

<p style="margin:0;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Just reply to this email or give us a call to lock in your date. We require a small deposit to hold your reservation.
</p>
<p style="margin:16px 0 0;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Looking forward to making your event special!<br>
<strong>— Zoar Bathroom Rentals</strong>
</p>
"""

    plain = (f"Hey {first_name},\n\nThanks for your interest! Here's your quote for {event_info}{date_info}:\n\n"
             f"Total: ${quote_amount:,.0f} (delivery, setup, and pickup included)\n\n"
             f"Includes:\n" + "\n".join(f"- {i}" for i in includes) +
             f"\n\nCall (424) 235-8979 or reply to book!\n\n— Zoar Bathroom Rentals")

    html = _base_template(body_html, f"Your custom quote: ${quote_amount:,.0f}")
    return html, plain


def booking_confirmation(first_name: str, event_type: str, event_date: str,
                         event_address: str = "", deposit_amount: float = 0) -> tuple[str, str]:
    """Generate branded booking confirmation email."""
    body_html = f"""
<p style="margin:0 0 8px;text-align:center;font-size:28px;">🎉</p>
<h2 style="margin:0 0 16px;text-align:center;color:{BRAND_DARK};font-size:22px;">You're Booked!</h2>
<p style="margin:0 0 24px;text-align:center;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Hey {first_name}, your luxury restroom trailer rental is confirmed!
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:{BRAND_LIGHT};border-radius:8px;margin:0 0 24px;">
<tr><td style="padding:20px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr>
<td style="padding:8px 0;font-size:13px;color:{BRAND_MUTED};width:120px;">Event</td>
<td style="padding:8px 0;font-size:15px;color:{BRAND_TEXT};font-weight:600;">{event_type or 'TBD'}</td>
</tr>
<tr>
<td style="padding:8px 0;font-size:13px;color:{BRAND_MUTED};">Date</td>
<td style="padding:8px 0;font-size:15px;color:{BRAND_TEXT};font-weight:600;">{event_date or 'TBD'}</td>
</tr>
{"<tr><td style='padding:8px 0;font-size:13px;color:" + BRAND_MUTED + ";'>Location</td><td style='padding:8px 0;font-size:15px;color:" + BRAND_TEXT + ";font-weight:600;'>" + event_address + "</td></tr>" if event_address else ""}
{"<tr><td style='padding:8px 0;font-size:13px;color:" + BRAND_MUTED + ";'>Deposit</td><td style='padding:8px 0;font-size:15px;color:" + BRAND_TEXT + ";font-weight:600;'>$" + f'{deposit_amount:,.0f}' + " received</td></tr>" if deposit_amount else ""}
</table>
</td></tr>
</table>

<p style="margin:0 0 16px;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
<strong>What happens next:</strong>
</p>
<ul style="margin:0 0 24px;padding-left:20px;line-height:2;font-size:14px;color:{BRAND_TEXT};">
<li>We'll confirm delivery logistics 2-3 days before your event</li>
<li>Our team handles all setup and teardown</li>
<li>The trailer will be sparkling clean and fully stocked</li>
<li>Call us anytime if you have questions</li>
</ul>

<p style="margin:0;line-height:1.7;font-size:15px;color:{BRAND_TEXT};">
Thank you for choosing Zoar! We're excited to be part of your event. 🙏<br>
<strong>— Zoar Bathroom Rentals</strong>
</p>
"""

    plain = (f"Hey {first_name}, your rental is confirmed!\n\n"
             f"Event: {event_type or 'TBD'}\nDate: {event_date or 'TBD'}\n"
             f"{'Location: ' + event_address if event_address else ''}\n\n"
             f"We'll confirm delivery logistics before your event.\n\n"
             f"Thanks for choosing Zoar!\n— Zoar Bathroom Rentals\n(424) 235-8979")

    html = _base_template(body_html, f"Your booking is confirmed for {event_date or 'your event'}!")
    return html, plain
