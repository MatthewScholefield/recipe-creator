from fastapi import APIRouter, HTTPException, Request, Response

from .admin import audit
from .recipes import _revalidate
from .schemas import DEFAULT_SITE_COPY, SiteSettings, SiteSettingsUpdate
from .security import authorize

router = APIRouter()


@router.get('/site-settings', response_model=SiteSettings)
async def get_settings(request: Request, response: Response):
    row = await request.app.state.repo.get('site_settings', 'public')
    result = {'revision': row['revision'], 'copy': row['copy']} if row else {'revision': 0, 'copy': DEFAULT_SITE_COPY}
    return _revalidate(request, response, result)


@router.put('/admin/site-settings', response_model=SiteSettings)
async def update_settings(body: SiteSettingsUpdate, request: Request):
    async with request.app.state.repo.transaction() as tx:
        context = await authorize(request, tx, admin=True)
        row = await tx.get('site_settings', 'public')
        previous = row['copy'] if row else DEFAULT_SITE_COPY
        if (row['revision'] if row else 0) != body.expected_revision:
            raise HTTPException(409, 'Revision changed')
        data = {'copy': body.copy.model_dump()}
        if row:
            result = await tx.compare_and_swap('site_settings', 'public', body.expected_revision, data)
        else:
            result = await tx.create('site_settings', {**data, 'revision': 1}, id='public')
        await audit(tx, context, 'site_settings.update', 'site_settings:public', previous_copy=previous, copy=data['copy'])
        return {'revision': result['revision'], 'copy': result['copy']}
