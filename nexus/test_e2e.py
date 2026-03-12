"""Comprehensive E2E test for Nexus CRM dashboard and API."""
import httpx, time, sys, json, sqlite3
from pathlib import Path

base = 'http://127.0.0.1:17862'
c = httpx.Client(base_url=base, timeout=15)

# Wait for server
for i in range(25):
    try:
        r = c.get('/')
        if r.status_code == 200:
            print(f'Server ready ({i+1}s)')
            break
    except:
        pass
    time.sleep(1)
else:
    print('Server not ready'); sys.exit(1)

passed = 0
failed = 0
test_lead_ids = []

def ok(label, condition=True, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print(f'  [PASS] {label}' + (f' ({detail})' if detail else ''))
    else:
        failed += 1
        print(f'  [FAIL] {label}' + (f' ({detail})' if detail else ''))

# ═══════════════════════════════════════════════════════
print('\n=== STATIC FILES ===')
r = c.get('/dashboard/')
ok('Dashboard HTML', r.status_code == 200 and 'NEXUS CRM' in r.text, f'{len(r.text)} bytes')

for f in ['/dashboard/js/api.js', '/dashboard/js/app.js', '/dashboard/js/charts.js', '/dashboard/css/dashboard.css']:
    r = c.get(f)
    ok(f'Static: {f}', r.status_code == 200)

# Verify dashboard has all GHL-style views
html = c.get('/dashboard/').text
ok('Has Dashboard nav', "goTo('dashboard')" in html)
ok('Has Conversations nav', "goTo('conversations')" in html)
ok('Has Contacts nav', "goTo('contacts')" in html)
ok('Has Opportunities nav', "goTo('opportunities')" in html)
ok('Has AI Agents nav', "goTo('ai_agents')" in html)
ok('Has Marketing nav', "goTo('marketing')" in html)
ok('Has Automation nav', "goTo('automation')" in html)
ok('Has Sites nav', "goTo('sites')" in html)
ok('Has New Lead modal', "modal === 'newLead'" in html)
ok('Has Add Note modal', "modal === 'addNote'" in html)
ok('Has Change Stage modal', "modal === 'changeStage'" in html)
ok('Has Send SMS modal', "modal === 'sendSMS'" in html)
ok('Has Send Email modal', "modal === 'sendEmail'" in html)
ok('Has Edit Lead modal', "modal === 'editLead'" in html)
ok('Has kanban view', 'kanban-col' in html)
ok('Has detail-grid layout', 'detail-grid' in html)
ok('Has loading bar', 'loading-bar' in html)
ok('Has charts.js script', 'charts.js' in html)
ok('Has conv-layout', 'conv-layout' in html)
ok('Has placeholder pages', 'placeholder-page' in html)
ok('Has business_name field', 'business_name' in html)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: READ ENDPOINTS ===')

r = c.get('/api/crm/stages')
ok('GET stages', r.status_code == 200)
stages = r.json()['stages']
ok('8 pipeline stages', len(stages) == 8, ', '.join(s['key'] for s in stages))
# Verify new stage names
stage_keys = [s['key'] for s in stages]
ok('Has contacted stage', 'contacted' in stage_keys)
ok('Has qualified stage', 'qualified' in stage_keys)
ok('Has deposit_requested stage', 'deposit_requested' in stage_keys)
ok('Has delivered stage', 'delivered' in stage_keys)
ok('Has closed stage', 'closed' in stage_keys)

r = c.get('/api/crm/stats')
ok('GET stats', r.status_code == 200 and 'total' in r.json())

r = c.get('/api/crm/activity')
ok('GET activity', r.status_code == 200 and 'activity' in r.json())

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: DASHBOARD ENDPOINT ===')

r = c.get('/api/crm/dashboard')
ok('GET dashboard', r.status_code == 200)
dash = r.json()
ok('Dashboard has total_leads', 'total_leads' in dash)
ok('Dashboard has total_value', 'total_value' in dash)
ok('Dashboard has won_revenue', 'won_revenue' in dash)
ok('Dashboard has conversion_pct', 'conversion_pct' in dash)
ok('Dashboard has stage_counts', isinstance(dash.get('stage_counts'), dict))
ok('Dashboard has funnel', isinstance(dash.get('funnel'), list))
ok('Funnel has 8 entries', len(dash.get('funnel', [])) == 8)
if dash.get('funnel'):
    f0 = dash['funnel'][0]
    ok('Funnel entry has fields', all(k in f0 for k in ['stage','label','count','cumulative','cumulative_pct','next_step_pct']))

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: CONVERSATIONS ENDPOINT ===')

r = c.get('/api/crm/conversations')
ok('GET conversations', r.status_code == 200)
conv = r.json()
ok('Conversations has list', 'conversations' in conv)
ok('Conversations has total', 'total' in conv)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: CREATE LEADS ===')

r = c.post('/api/crm/leads', json={
    'first_name': 'Alice', 'last_name': 'Smith',
    'phone': '5550001111', 'email': 'alice@test.com',
    'source': 'e2e_test', 'event_type': 'wedding',
    'event_date': '2026-06-15', 'event_city': 'Miami',
    'guest_count': 150, 'notes': 'VIP client'
})
ok('Create Alice', r.status_code == 200)
alice = r.json()['lead']
alice_id = alice['id']
test_lead_ids.append(alice_id)
ok('Alice UUID', bool(alice['lead_uuid']))
ok('Alice status=new_lead', alice['booking_status'] == 'new_lead')
ok('Alice full_name', alice['full_name'] == 'Alice Smith')

r = c.post('/api/crm/leads', json={
    'first_name': 'Bob', 'last_name': 'Jones',
    'phone': '5550002222', 'email': 'bob@test.com',
    'source': 'e2e_test', 'event_type': 'corporate',
    'event_date': '2026-07-20', 'event_city': 'Tampa', 'guest_count': 80
})
ok('Create Bob', r.status_code == 200)
bob_id = r.json()['lead']['id']
test_lead_ids.append(bob_id)

r = c.post('/api/crm/leads', json={
    'first_name': 'Carol', 'last_name': 'Davis',
    'phone': '5550003333', 'source': 'e2e_test', 'event_type': 'construction'
})
ok('Create Carol', r.status_code == 200)
carol_id = r.json()['lead']['id']
test_lead_ids.append(carol_id)

# Dedup
r = c.post('/api/crm/leads', json={'phone': '5550001111', 'first_name': 'Dup'})
ok('Dedup by phone', r.json().get('deduplicated') == True)

r = c.post('/api/crm/leads', json={'email': 'bob@test.com', 'first_name': 'Dup2'})
ok('Dedup by email', r.json().get('deduplicated') == True)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: SEARCH & FILTER ===')

r = c.get('/api/crm/leads?limit=10')
ok('List leads', r.status_code == 200 and r.json()['total'] >= 3)

r = c.get('/api/crm/leads?q=Alice')
ok('Search by name', r.json()['total'] >= 1 and r.json()['leads'][0]['first_name'] == 'Alice')

r = c.get('/api/crm/leads?q=5550002222')
ok('Search by phone', r.json()['total'] >= 1)

r = c.get('/api/crm/leads?q=bob@test.com')
ok('Search by email', r.json()['total'] >= 1)

r = c.get('/api/crm/leads?q=Miami')
ok('Search by city', r.json()['total'] >= 1)

r = c.get('/api/crm/leads?status=new_lead')
ok('Filter by stage', r.status_code == 200 and r.json()['total'] >= 3)

r = c.get('/api/crm/leads?limit=2&offset=0')
ok('Pagination limit', len(r.json()['leads']) <= 2)

r = c.get('/api/crm/leads?limit=2&offset=2')
ok('Pagination offset', r.status_code == 200)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: LEAD DETAIL ===')

r = c.get(f'/api/crm/leads/{alice_id}')
ok('Get Alice detail', r.status_code == 200)
d = r.json()
ok('Has lead object', 'lead' in d and d['lead']['first_name'] == 'Alice')
ok('Has timeline array', isinstance(d.get('timeline'), list))
ok('Has messages array', isinstance(d.get('messages'), list))
ok('Event city=Miami', d['lead']['event_city'] == 'Miami')
ok('Guest count=150', d['lead']['guest_count'] == 150)

r = c.get('/api/crm/leads/99999')
ok('404 for missing lead', r.status_code == 404)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: STAGE TRANSITIONS (GHL naming) ===')

r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'contacted', 'reason': 'Initial SMS'})
ok('new_lead -> contacted', r.json().get('ok') == True)

r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'qualified'})
ok('contacted -> qualified', r.json().get('ok') == True)

r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'quote_sent'})
ok('qualified -> quote_sent', r.json().get('ok') == True)

r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'deposit_requested'})
ok('quote_sent -> deposit_requested', r.json().get('ok') == True)

# Invalid transition
r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'delivered'})
ok('Invalid: deposit_requested -> delivered rejected', r.status_code == 400)

r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'booked'})
ok('deposit_requested -> booked', r.json().get('ok') == True)

r = c.post(f'/api/crm/leads/{alice_id}/stage', json={'stage': 'delivered'})
ok('booked -> delivered', r.json().get('ok') == True)

# Bob: closed and reopen
r = c.post(f'/api/crm/leads/{bob_id}/stage', json={'stage': 'qualified'})
ok('Bob -> qualified', r.json().get('ok'))
r = c.post(f'/api/crm/leads/{bob_id}/stage', json={'stage': 'closed', 'reason': 'No budget'})
ok('Bob -> closed', r.json().get('ok'))
r = c.post(f'/api/crm/leads/{bob_id}/stage', json={'stage': 'new_lead', 'reason': 'Reopened'})
ok('Bob closed -> new_lead (reopen)', r.json().get('ok'))

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: NOTES ===')

r = c.post(f'/api/crm/leads/{alice_id}/note', json={'note': 'Called, very interested', 'author': 'Kai'})
ok('Add note 1', r.status_code == 200)
r = c.post(f'/api/crm/leads/{alice_id}/note', json={'note': 'Sent quote for $2500', 'author': 'Kai'})
ok('Add note 2', r.status_code == 200)

# Check notes in lead data
r = c.get(f'/api/crm/leads/{alice_id}')
notes = json.loads(r.json()['lead'].get('internal_notes', '[]'))
ok('Notes stored', len(notes) >= 2, f'{len(notes)} notes')
ok('Note content correct', notes[0]['note'] == 'Called, very interested')

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: UPDATE LEAD ===')

r = c.put(f'/api/crm/leads/{alice_id}', json={
    'total_quote_amount': 2500, 'deposit_amount': 500,
    'deposit_status': 'sent', 'event_address': '123 Beach Rd',
    'terrain_notes': 'Flat grass', 'power_water_notes': 'Outlet available',
    'event_start_time': '10:00 AM', 'event_end_time': '4:00 PM',
    'business_name': 'Smith Events LLC'
})
ok('Update lead', r.status_code == 200)
lead = r.json()['lead']
ok('Quote = 2500', lead['total_quote_amount'] == 2500)
ok('Deposit = 500', lead['deposit_amount'] == 500)
ok('Deposit status = sent', lead['deposit_status'] == 'sent')
ok('Address updated', lead['event_address'] == '123 Beach Rd')
ok('Terrain updated', lead['terrain_notes'] == 'Flat grass')
ok('Business name updated', lead['business_name'] == 'Smith Events LLC')

# Update name
r = c.put(f'/api/crm/leads/{carol_id}', json={'first_name': 'Caroline'})
ok('Update name', r.json()['lead']['first_name'] == 'Caroline')
ok('Full name auto-updated', 'Caroline' in r.json()['lead']['full_name'])

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: TIMELINE ===')

r = c.get(f'/api/crm/leads/{alice_id}/timeline')
ok('Get timeline', r.status_code == 200)
events = r.json()['timeline']
ok('Multiple events', len(events) >= 7, f'{len(events)} events')
types = set(e['event_type'] for e in events)
ok('Has lead_created', 'lead_created' in types)
ok('Has stage_changed', 'stage_changed' in types)
ok('Has note_added', 'note_added' in types)
ok('Has lead_updated', 'lead_updated' in types)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: PIPELINE & STATS ===')

r = c.get('/api/crm/leads/pipeline')
ok('Pipeline kanban data', r.status_code == 200)
pipeline = r.json()['pipeline']
ok('Pipeline has 8 stages', len(pipeline) == 8)
ok('delivered has Alice', len(pipeline.get('delivered', [])) >= 1)

r = c.get('/api/crm/stats')
stats = r.json()
ok('Stats total >= 3', stats['total'] >= 3)
ok('Stats has by_stage', isinstance(stats.get('by_stage'), dict))
ok('Stats has new_24h', 'new_24h' in stats)

r = c.get('/api/crm/stages')
stages = r.json()['stages']
total_in_stages = sum(s['count'] for s in stages)
ok('Stage counts consistent', total_in_stages >= 3)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: DASHBOARD WITH DATA ===')

r = c.get('/api/crm/dashboard')
dash = r.json()
ok('Dashboard total >= 3', dash['total_leads'] >= 3)
ok('Dashboard total_value >= 2500', dash['total_value'] >= 2500)
ok('Dashboard won_revenue >= 2500', dash['won_revenue'] >= 2500)
ok('Dashboard conversion_pct > 0', dash['conversion_pct'] > 0)
ok('Dashboard stage_counts non-empty', sum(dash['stage_counts'].values()) >= 3)

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: ACTIVITY ===')

r = c.get('/api/crm/activity?limit=50')
ok('Activity feed', r.status_code == 200)
activity = r.json()['activity']
ok('Has activity items', len(activity) >= 5, f'{len(activity)} items')
ok('Activity has lead_name', bool(activity[0].get('lead_name')))

# ═══════════════════════════════════════════════════════
print('\n=== CRM API: FACEBOOK WEBHOOK ===')

r = c.get('/api/crm/webhook/facebook?hub.mode=subscribe&hub.verify_token=nexus-fb-verify&hub.challenge=abc123')
ok('FB verify OK', r.status_code == 200 and r.text == 'abc123')

r = c.get('/api/crm/webhook/facebook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=x')
ok('FB verify bad token', r.status_code == 403)

# ═══════════════════════════════════════════════════════
print('\n=== LEGACY ENDPOINTS ===')

r = c.get('/api/leads')
ok('Legacy /api/leads', r.status_code == 200)

r = c.get('/api/leads/stats')
ok('Legacy /api/leads/stats', r.status_code == 200)

r = c.get('/')
ok('Main UI GET /', r.status_code == 200)

# SSE is streaming — just check it starts OK via headers
with c.stream('GET', '/api/events') as sse:
    ok('SSE endpoint', sse.status_code == 200)

# ═══════════════════════════════════════════════════════
# Cleanup
conn = sqlite3.connect(str(Path.home() / '.nexus' / 'memory.db'))
for lid in test_lead_ids:
    conn.execute('DELETE FROM lead_events WHERE lead_id = ?', (lid,))
    conn.execute('DELETE FROM lead_messages WHERE lead_id = ?', (lid,))
    conn.execute('DELETE FROM leads WHERE id = ?', (lid,))
conn.commit()
conn.close()

print(f'\n{"="*55}')
print(f'  RESULTS: {passed} passed, {failed} failed, {passed+failed} total')
if failed == 0:
    print('  === ALL TESTS PASSED ===')
else:
    print(f'  === {failed} TESTS FAILED ===')
    sys.exit(1)
