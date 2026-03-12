# Facebook Group Vendor Scraper

Playwright-based scraper that monitors Facebook groups for event vendor posts in the LA/SoCal area. Extracts vendor contact information from posts and profiles, then sends it to the Nexus CRM server.

## Architecture

- **Runs locally on Mac mini** — uses a visible browser with your real Facebook session
- **Sends data to Nexus VPS** — via Cloudflare tunnel HTTPS
- **Controlled via Telegram** — start/stop scrapes, add groups, view results
- **READ ONLY** — never likes, comments, follows, or interacts with Facebook

## Setup

```bash
cd scrapers/fb_vendor_scraper
./setup.sh
```

This creates a Python virtual environment, installs dependencies, installs Playwright Chromium, and creates a launchd plist for daily scheduling.

## First Run — Facebook Login

```bash
source .venv/bin/activate
python scraper.py
```

A browser window will open. Log into Facebook manually. After logging in, press Ctrl+C. Your session is saved to `~/.nexus/fb_browser_data/` and persists between runs.

## Configuration

Edit `config.py`:

- `NEXUS_BASE_URL` — Your Cloudflare tunnel URL (e.g., `https://nexus.yourdomain.com`)
- `ALLOWED_IPS` — Add your VPS IP address for remote triggers
- Timing delays can be adjusted but defaults are safe

Set your API key:
```bash
export NEXUS_API_KEY=your_key_here
```

## Discovering Groups

```bash
python scraper.py --discover
```

Searches Facebook for relevant event vendor groups in LA/SoCal. Displays results but does NOT join any groups. Join the ones you want manually, then add them.

## Adding Groups

Via Telegram:
```
/fb_add_group https://facebook.com/groups/la-event-vendors
```

Or edit `groups.json` directly.

## Running

### Manual (one-time)
```bash
python scraper.py
```

### With daily schedule + Telegram triggers
```bash
python scraper.py --schedule
```
Or use launchd:
```bash
launchctl load ~/Library/LaunchAgents/com.nexus.fb-vendor-scraper.plist
```

### Via Telegram
```
/fb_scrape    — Start a scrape
/fb_stop      — Stop current scrape
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/fb_scrape` | Trigger manual scrape |
| `/fb_stop` | Stop current scrape |
| `/fb_prospects` | Prospect summary |
| `/fb_prospects {category}` | Filter by category |
| `/fb_today` | Today's finds |
| `/fb_lead {id}` | Full prospect detail |
| `/fb_status {id} {status}` | Update status |
| `/fb_note {id} {text}` | Add note |
| `/fb_groups` | List active groups |
| `/fb_add_group {url}` | Add group |
| `/fb_remove_group {url}` | Remove group |

## Logs

- Main log: `scraper.log` (rotated daily, kept 30 days)
- Stdout/stderr (when running via launchd): `scraper_stdout.log`, `scraper_stderr.log`

## Troubleshooting

**"Facebook login required" alert:**
Your session expired. Run `python scraper.py` manually, log in again in the browser, then restart the service.

**"Could not reach Mac scraper" in Telegram:**
The listener isn't running. Start the scraper with `--schedule` or load the launchd plist.

**Scraper stops with "CAPTCHA detected":**
Facebook flagged automated behavior. Wait 24 hours before running again. Consider increasing delay times in `config.py`.

**Pending uploads not sending:**
Check that `NEXUS_BASE_URL` is correct and the tunnel is running. Pending data is saved in `pending_uploads.json` and retried on next run.

## Safety

- Never interacts with Facebook (no likes, comments, follows, messages)
- Stops immediately on CAPTCHA, checkpoint, or error
- 45-minute maximum session length
- Random delays between all actions to simulate natural browsing
- Only scrapes publicly visible information
