import asyncio

import pytest
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from recipe_creator.repository import ConflictError, Repository
from recipe_creator.schemas import DEFAULT_SITE_COPY, SiteSettingsUpdate
from recipe_creator.site_settings import router
from test_identity import identity_app, client, csrf, profile
from test_admin import login


def test_copy_strict_limits():
    SiteSettingsUpdate(expected_revision=0, copy=DEFAULT_SITE_COPY)
    for changes in ({'site_title': ' '}, {'site_title': 'x' * 81}, {'home_title': ''},
                    {'footer_text': '\x00'}, {'unknown': 'secret'}, {'home_intro': 'x' * 301}):
        with pytest.raises(ValidationError):
            SiteSettingsUpdate(expected_revision=0, copy={**DEFAULT_SITE_COPY, **changes})
    with pytest.raises(ValidationError):
        SiteSettingsUpdate(expected_revision=0, copy={})


async def test_copy_defaults_cas_and_audit_rollback(identity_app, monkeypatch):
    identity_app.include_router(router)
    repo = identity_app.state.repo
    @identity_app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return JSONResponse({'detail': 'Conflict'}, status_code=409)
    async with client(identity_app) as browser:
        result = await browser.get('/site-settings')
        assert result.json() == {'revision': 0, 'copy': DEFAULT_SITE_COPY}
        assert result.headers['cache-control'] == 'public, no-cache'
        assert (await browser.get('/site-settings', headers={'If-None-Match': result.headers['etag']})).status_code == 304
        assert not await repo.list('site_settings')
        await profile(browser)
        body = {'expected_revision': 0, 'copy': {**DEFAULT_SITE_COPY, 'home_intro': '<b>authored text</b>'}}
        assert (await browser.put('/admin/site-settings', json=body)).status_code == 403
        await login(browser)
        results = await asyncio.gather(*(browser.put('/admin/site-settings', json=body) for _ in range(2)))
        assert sorted(result.status_code for result in results) == [200, 409]
        assert all(result.headers['cache-control'] == 'no-store' for result in results)
        assert (await browser.get('/site-settings')).json()['revision'] == 1
        events = await repo.list('audit', {'action': 'site_settings.update'})
        assert len(events) == 1 and events[0]['previous_copy'] == DEFAULT_SITE_COPY
        async with Repository(repo.settings) as restarted:
            assert (await restarted.get('site_settings', 'public'))['copy'] == body['copy']
        original = type(repo).create
        async def fail(self, table, *args, **kwargs):
            if table == 'audit':
                raise RuntimeError('audit failure')
            return await original(self, table, *args, **kwargs)
        monkeypatch.setattr(type(repo), 'create', fail)
        with pytest.raises(RuntimeError, match='audit failure'):
            await browser.put('/admin/site-settings', json={**body, 'expected_revision': 1})
        assert (await repo.get('site_settings', 'public'))['revision'] == 1
