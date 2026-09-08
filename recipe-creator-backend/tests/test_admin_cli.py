import pytest
from recipe_creator import cli
from test_repository import repo


def test_noninteractive_flags_and_retired_password():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['hash-password'])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['admin-revoke'])
    assert cli.parser().parse_args(['admin-users']).limit == 20


async def test_cli_permissions_listing_and_audit(repo, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: False)
    user = await repo.create('users', {'display_name': 'Duplicate'})
    await repo.create('users', {'display_name': 'Blocked', 'state': 'blocked'})
    args = cli.parser().parse_args(['admin-grant', '--user-id', user['id']])
    with pytest.raises(ValueError, match='Noninteractive'):
        await cli.admin_command(repo, args)
    args.yes = True
    result = await cli.admin_command(repo, args)
    assert result['is_admin']
    assert (await repo.get('users', user['id']))['is_admin']
    rows = await cli.recent_admin_users(repo, q='duplicate')
    assert len(rows) == 1 and set(rows[0]) == {'id', 'display_name', 'last_seen', 'is_admin'}
    args.command = 'admin-revoke'
    assert not (await cli.admin_command(repo, args))['is_admin']
    assert len(await repo.list('audit')) == 2
    await repo.update('users', user['id'], {'state': 'merged', 'merged_into': user['id']})
    with pytest.raises(ValueError, match='active, unmerged'):
        await cli.admin_command(repo, args)


async def test_cli_audit_failure_rolls_back(repo, monkeypatch):
    user = await repo.create('users', {'display_name': 'Operator'})
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: False)
    original = type(repo).create
    async def fail(self, table, *args, **kwargs):
        if table == 'audit':
            raise RuntimeError('audit failure')
        return await original(self, table, *args, **kwargs)
    monkeypatch.setattr(type(repo), 'create', fail)
    args = cli.parser().parse_args(['admin-grant', '--user-id', user['id'], '--yes'])
    with pytest.raises(RuntimeError):
        await cli.admin_command(repo, args)
    assert not (await repo.get('users', user['id']))['is_admin']
