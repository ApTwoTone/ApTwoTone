#!/usr/bin/env python3
"""
Zoar Quick Email Sender
========================
Send branded HTML emails from the terminal in 30 seconds.
No coding required. Just paste the recipient, subject, and body.

Usage:
    zoar-email                          # Interactive mode (prompts for everything)
    zoar-email --to email --subject "x" # Pre-fill some fields
    zoar-email --template follow-up     # Use a pre-built template
"""

import json
import smtplib
import os
import sys
import argparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from pathlib import Path

CONFIG_PATH = os.path.expanduser('~/.nexus/config.json')
LOGO_PATH = '/Users/kai/nexus/static/images/zoar_logo.jpg'


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def wrap_in_html(body_text):
    """Wrap plain text body in the branded Zoar HTML template."""

    # Convert plain text to HTML paragraphs
    # Handle bullet points / dashes
    lines = body_text.strip().split('\n')
    html_lines = []
    in_list = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            html_lines.append('<br>')
            continue

        # Detect bullet points (- or *)
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                html_lines.append('<ul style="margin: 8px 0; padding-left: 20px;">')
                in_list = True
            item_text = stripped[2:].strip()
            html_lines.append(f'<li style="margin: 4px 0; line-height: 1.5;">{item_text}</li>')
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            html_lines.append(f'<p style="margin: 0 0 12px 0; line-height: 1.6;">{stripped}</p>')

    if in_list:
        html_lines.append('</ul>')

    body_html = '\n'.join(html_lines)

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background-color:#f5f5f5; font-family: Arial, Helvetica, sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5; padding: 20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border-radius:8px; overflow:hidden;">

<!-- HEADER -->
<tr><td style="background-color:#1a1a2e; padding: 30px 40px; text-align:center;">
<h1 style="color:#d4a017; margin:0; font-size:22px; letter-spacing:2px;">ZOAR BATHROOM RENTALS</h1>
<p style="color:#c5975b; margin:8px 0 0; font-size:13px; letter-spacing:3px;">LUXURY RESTROOM TRAILERS</p>
</td></tr>

<!-- BODY -->
<tr><td style="padding: 35px 40px; color:#333333; font-size:15px; line-height:1.6;">
{body_html}
</td></tr>

<!-- CTA BUTTON -->
<tr><td align="center" style="padding: 0 40px 30px;">
<a href="tel:4242358979" style="display:inline-block; background-color:#d4a017; color:#ffffff; text-decoration:none; padding:14px 35px; border-radius:6px; font-size:16px; font-weight:bold;">Call Now: (424) 235-8979</a>
<p style="margin:12px 0 0; font-size:13px; color:#888;">Or reply to this email — we respond within minutes!</p>
</td></tr>

<!-- FOOTER SIGNATURE -->
<tr><td style="padding: 20px 40px; border-top: 1px solid #eeeeee;">
<table cellpadding="0" cellspacing="0">
<tr>
<td style="padding-right:15px; vertical-align:top;">
<img src="cid:zoar_logo" alt="Zoar" width="60" style="border-radius:4px;">
</td>
<td style="vertical-align:top;">
<p style="margin:0; font-weight:bold; font-size:14px; color:#333;">Zoar Bathroom Rentals</p>
<p style="margin:2px 0; font-size:12px; color:#666;">Luxury Restroom Trailers for Any Occasion</p>
<p style="margin:2px 0; font-size:12px;">
<a href="tel:4242358979" style="color:#d4a017;">(424) 235-8979</a> |
<a href="mailto:zoarbathrooms@gmail.com" style="color:#d4a017;">zoarbathrooms@gmail.com</a>
</p>
<p style="margin:2px 0; font-size:12px;">
<a href="https://zoarbathroomrental.com" style="color:#d4a017;">zoarbathroomrental.com</a>
</p>
</td>
</tr>
</table>
</td></tr>

<!-- SERVICE AREA -->
<tr><td style="padding: 10px 40px; text-align:center;">
<p style="font-size:11px; color:#aaa; margin:0;">Serving Los Angeles, San Fernando Valley, Santa Clarita, Ventura, Oxnard &amp; Santa Monica</p>
</td></tr>

<!-- CAN-SPAM FOOTER -->
<tr><td style="padding: 15px 40px 20px; text-align:center; border-top: 1px solid #eeeeee;">
<p style="font-size:10px; color:#bbb; margin:0;">Zoar Bathroom Rentals | San Fernando Valley, CA</p>
<p style="font-size:10px; color:#bbb; margin:4px 0 0;">If you do not wish to receive future emails, reply with "unsubscribe" and we will remove you immediately.</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    return html


def send_email(to_email, subject, plain_body, html_body):
    """Send the branded email via Gmail SMTP."""
    config = load_config()
    smtp_user = config.get('gmail_address', 'zoarbathrooms@gmail.com')
    smtp_pass = config.get('gmail_app_password', '')

    if not smtp_pass:
        print("\n  No Gmail app password found in ~/.nexus/config.json")
        print("  Add 'gmail_app_password' to the config file.")
        return False

    try:
        msg = MIMEMultipart('related')
        msg['From'] = f"Zoar Bathroom Rentals <{smtp_user}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        msg['Reply-To'] = smtp_user

        # Add plain text + HTML
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(plain_body, 'plain', 'utf-8'))
        alt.attach(MIMEText(html_body, 'html', 'utf-8'))
        msg.attach(alt)

        # Attach logo
        if os.path.exists(LOGO_PATH):
            with open(LOGO_PATH, 'rb') as f:
                logo = MIMEImage(f.read(), _subtype='jpeg')
                logo.add_header('Content-ID', '<zoar_logo>')
                logo.add_header('Content-Disposition', 'inline', filename='zoar_logo.jpg')
                msg.attach(logo)

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15)
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())
        server.quit()

        return True
    except Exception as e:
        print(f"\n  Send failed: {e}")
        return False


def get_multiline_input(prompt):
    """Read multiple lines of input. Empty line = done."""
    print(prompt)
    print("  (Type your message. Press Enter on an empty line when done.)\n")
    lines = []
    while True:
        try:
            line = input()
            if line == '' and lines and lines[-1] == '':
                # Two empty lines in a row = done
                lines.pop()  # Remove the trailing empty line
                break
            lines.append(line)
        except EOFError:
            break
    return '\n'.join(lines)


# -------------------------------------------------------
# PRE-BUILT TEMPLATES
# -------------------------------------------------------

TEMPLATES = {
    'follow-up': {
        'subject': 'Zoar Bathroom Rentals — just tried calling!',
        'body': """Hi {name},

Thanks for reaching out! I just tried calling but missed you.

To put together a custom quote for the luxury restroom trailer, I just need:
- What type of event?
- Approximate date?
- Location?

Reply here or text me anytime at (424) 235-8979.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""
    },
    'quote': {
        'subject': 'Your custom quote from Zoar Bathroom Rentals',
        'body': """Hi {name},

Great chatting with you! Here's the quote for your {event_type}:

Event: {event_type}
Date: {event_date}
Location: {location}

Our luxury restroom trailer includes:
- Climate control (AC and heat)
- Real flushing porcelain toilets
- Running water sinks with mirrors
- Hardwood-style flooring
- Interior lighting
- Delivery, setup, restocking, and pickup

I'll send over some photos separately so you can see the trailer.

Ready to lock in your date? Just reply here or call me at (424) 235-8979.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""
    },
    'photos': {
        'subject': 'Photos of our luxury restroom trailer — Zoar Bathroom Rentals',
        'body': """Hi {name},

As promised, here are some photos of our luxury restroom trailer!

The trailer features multiple private stalls, each with a real flushing toilet, running water sink, mirror, and climate control. We deliver, set up, and pick up — you don't have to worry about a thing.

Let me know if you have any questions or if you'd like to reserve your date.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""
    },
    'reminder': {
        'subject': 'Quick follow up — Zoar Bathroom Rentals',
        'body': """Hi {name},

Just following up on my earlier message about our luxury restroom trailers. Wanted to make sure it didn't get buried in your inbox!

If you're still looking for restroom facilities for your event, I'd love to help. Just need the event type, date, and location to put a quote together.

No pressure at all — just didn't want you to miss out if you're still interested.

Best,
Kai
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com"""
    },
}


def interactive_mode():
    """Full interactive mode -- prompts for everything."""
    print("\n" + "=" * 50)
    print("  ZOAR QUICK EMAIL SENDER")
    print("=" * 50)

    # Check for templates first
    print("\n  Templates available:")
    print("    1. Custom (write your own)")
    print("    2. follow-up -- \"Just tried calling\"")
    print("    3. quote -- Send a custom quote")
    print("    4. photos -- Send photos follow-up")
    print("    5. reminder -- Follow-up nudge")

    choice = input("\n  Choose (1-5): ").strip()

    to_email = input("\n  To: ").strip()

    if not to_email or '@' not in to_email:
        print("  Invalid email address")
        return

    if choice in ['2', '3', '4', '5']:
        template_names = {'2': 'follow-up', '3': 'quote', '4': 'photos', '5': 'reminder'}
        template = TEMPLATES[template_names[choice]]

        # Get name for personalization
        name = input("  Recipient's first name: ").strip() or "there"

        subject = template['subject']
        body = template['body'].replace('{name}', name)

        # If quote template, get event details
        if choice == '3':
            event_type = input("  Event type: ").strip() or "event"
            event_date = input("  Event date: ").strip() or "TBD"
            location = input("  Location: ").strip() or "TBD"
            body = body.replace('{event_type}', event_type)
            body = body.replace('{event_date}', event_date)
            body = body.replace('{location}', location)

        # Allow subject edit
        edit_subject = input(f"\n  Subject: {subject}\n  Edit? (press Enter to keep, or type new): ").strip()
        if edit_subject:
            subject = edit_subject

    else:
        # Custom email
        subject = input("  Subject: ").strip()
        if not subject:
            print("  Subject required")
            return

        body = get_multiline_input("\n  Body:")
        if not body.strip():
            print("  Body required")
            return

    # Preview
    preview_body = body[:100].replace('\n', ' ')
    print("\n" + "-" * 50)
    print("  PREVIEW:")
    print(f"  To: {to_email}")
    print(f"  Subject: {subject}")
    print(f"  Body: {preview_body}...")
    print("-" * 50)

    confirm = input("\n  Send this email? (y/n): ").strip().lower()

    if confirm != 'y':
        print("  Cancelled.")
        return

    # Wrap in HTML and send
    html_body = wrap_in_html(body)

    print("\n  Sending...")
    success = send_email(to_email, subject, body, html_body)

    if success:
        print(f"\n  Sent! Email delivered to {to_email}")
        print("  The email has the full branded Zoar template.\n")
    else:
        print(f"\n  Failed to send. Check your internet connection and Gmail app password.\n")


def cli_mode(args):
    """Non-interactive mode with command line args."""
    if not args.to or not args.subject:
        print("  --to and --subject required in CLI mode")
        return

    if args.template and args.template in TEMPLATES:
        template = TEMPLATES[args.template]
        name = args.name or "there"
        subject = args.subject or template['subject']
        body = template['body'].replace('{name}', name)
    elif args.body:
        subject = args.subject
        body = args.body
    else:
        print("  Provide --body or --template")
        return

    html_body = wrap_in_html(body)

    print(f"\n  Sending to {args.to}...")
    success = send_email(args.to, subject, body, html_body)

    if success:
        print(f"  Sent to {args.to}")
    else:
        print(f"  Failed")


def main():
    parser = argparse.ArgumentParser(description='Zoar Quick Email Sender')
    parser.add_argument('--to', type=str, help='Recipient email')
    parser.add_argument('--subject', type=str, help='Email subject')
    parser.add_argument('--body', type=str, help='Email body (plain text)')
    parser.add_argument('--name', type=str, help='Recipient first name')
    parser.add_argument('--template', type=str, choices=list(TEMPLATES.keys()),
                        help='Use a pre-built template')
    args = parser.parse_args()

    if args.to:
        cli_mode(args)
    else:
        interactive_mode()


if __name__ == '__main__':
    main()
