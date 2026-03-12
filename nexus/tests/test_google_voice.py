import asyncio
import time

from integrations.google_voice import GoogleVoiceSMS


class _FakePage:
    def __init__(self, url: str, ready: bool = True):
        self.url = url
        self.ready = ready
        self.goto_calls = []
        self.reload_calls = 0
        self.load_state_calls = []

    async def wait_for_load_state(self, wait_until, timeout):
        self.load_state_calls.append((wait_until, timeout))

    async def query_selector(self, selector):
        return object() if self.ready else None

    async def reload(self, wait_until=None, timeout=None):
        self.reload_calls += 1
        self.url = "https://voice.google.com/u/0/messages"

    async def goto(self, url, wait_until=None, timeout=None):
        self.goto_calls.append((url, wait_until, timeout))
        self.url = url


def test_goto_messages_skips_redundant_navigation_when_messages_view_is_ready():
    async def scenario():
        client = GoogleVoiceSMS()
        page = _FakePage("https://voice.google.com/u/0/messages", ready=True)
        client.page = page
        client._last_messages_nav_monotonic = time.monotonic()
        background_calls = []

        async def fake_background(force=False):
            background_calls.append(force)

        client._background_window = fake_background
        await client._goto_messages(force_refresh=True)
        return page, background_calls

    page, background_calls = asyncio.run(scenario())

    assert page.goto_calls == []
    assert page.reload_calls == 0
    assert background_calls == [True]


def test_goto_messages_navigates_when_not_already_on_messages_page():
    async def scenario():
        client = GoogleVoiceSMS()
        page = _FakePage("https://voice.google.com/u/0/calls", ready=False)
        client.page = page
        background_calls = []

        async def fake_background(force=False):
            background_calls.append(force)

        client._background_window = fake_background
        await client._goto_messages(force_refresh=False)
        return page, background_calls

    page, background_calls = asyncio.run(scenario())

    assert page.goto_calls
    assert page.goto_calls[0][0] == "https://voice.google.com/u/0/messages"
    assert background_calls == [True]


def test_goto_messages_reloads_stale_messages_page_when_force_refresh_requested():
    async def scenario():
        client = GoogleVoiceSMS()
        page = _FakePage("https://voice.google.com/u/0/messages", ready=False)
        client.page = page
        client._last_messages_nav_monotonic = time.monotonic() - 120

        async def fake_background(force=False):
            return None

        client._background_window = fake_background
        await client._goto_messages(force_refresh=True)
        return page

    page = asyncio.run(scenario())

    assert page.reload_calls == 1
    assert page.goto_calls == []


def test_google_voice_window_keeper_rehides_window_periodically():
    async def scenario():
        client = GoogleVoiceSMS()
        client.hide_window_interval_seconds = 0.01
        client.page = object()
        background_calls = []

        async def fake_background(force=False):
            background_calls.append(force)

        client._background_window = fake_background
        client._start_window_keeper()
        await asyncio.sleep(0.04)
        task_before_stop = client._window_keeper_task
        await client._stop_window_keeper()
        return background_calls, task_before_stop, client._window_keeper_task

    background_calls, task_before_stop, task_after_stop = asyncio.run(scenario())

    assert len(background_calls) >= 2
    assert all(background_calls)
    assert task_before_stop is not None
    assert task_after_stop is None


def test_google_voice_window_keeper_does_not_spawn_duplicate_tasks():
    async def scenario():
        client = GoogleVoiceSMS()
        client.hide_window_interval_seconds = 0.05
        client.page = object()

        async def fake_background(force=False):
            await asyncio.sleep(0.01)

        client._background_window = fake_background
        client._start_window_keeper()
        first_task = client._window_keeper_task
        client._start_window_keeper()
        second_task = client._window_keeper_task
        await client._stop_window_keeper()
        return first_task, second_task

    first_task, second_task = asyncio.run(scenario())

    assert first_task is second_task
