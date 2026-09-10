"""ORM repository with real SurrealDB 3 interactive transactions.

0.33.1's WebSocketTransaction sends BEGIN and CANCEL as separate query RPCs;
that does NOT roll back writes on 3.2.4. The small SDK adapter below uses the
server's begin/commit/cancel RPC and txn envelope while retaining ORM CRUD.
Never replace this with the stock buffered HTTP or WebSocket transaction.
"""
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import uuid4
import re
import asyncio

from surreal_orm import SurrealDBConnectionManager as Connections
from surreal_orm.migrations import MigrationExecutor
from surreal_sdk import WebSocketConnection
from surreal_sdk.protocol.rpc import RPCRequest
from surreal_sdk.protocol.cbor import _cbor_default_encoder
from cbor2 import CBORTag, dumps as cbor_dumps
from surreal_sdk.transaction import WebSocketTransaction

from .models import MODELS, DomainModel, utcnow
from .settings import Settings


class ConflictError(RuntimeError):
    """Concurrent modification, duplicate ID, or failed expected revision."""


class NotFoundError(LookupError):
    pass


class _TransactionRequest(RPCRequest):
    def __init__(self, request: RPCRequest, transaction_id: Any):
        super().__init__(request.method, request.params, request.id)
        self.transaction_id = transaction_id

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        if self.transaction_id is not None:
            result["txn"] = self.transaction_id
        return result

    def to_cbor(self) -> bytes:
        # SDK maps EVERY None to NONE, dropping explicit null keys in nested
        # documents. Preserve JSON null inside our flexible document fields.
        flexible = {"payload", "content", "ingredient_groups", "_sv_payload", "_sv_content", "_sv_ingredient_groups"}
        def prepare(value, document=False):
            if value is None:
                return None if document else CBORTag(6, None)
            if isinstance(value, dict):
                return {k: prepare(v, document or k in flexible) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [prepare(v, document) for v in value]
            return value
        return cbor_dumps(prepare(self.to_dict()), default=_cbor_default_encoder)


class _Connection(WebSocketConnection):
    transaction_id: Any = None

    async def _send_rpc(self, request):
        transaction_id = self.transaction_id if request.method not in {"begin", "commit", "cancel"} else None
        return await super()._send_rpc(_TransactionRequest(request, transaction_id))

    async def query(self, sql, vars=None):
        result = await super().query(sql, vars)
        if not result.is_ok:
            errors = [str(statement.result) for statement in result.results if statement.status != "OK"]
            raise RuntimeError("SurrealDB statement failed: " + "; ".join(errors))
        return result


class _InteractiveTransaction(WebSocketTransaction):
    async def _begin(self):
        self._connection.transaction_id = await self._connection.rpc("begin")
        self._active = True

    async def commit(self):
        try:
            result = await self._connection.rpc("commit", [self._connection.transaction_id])
            self._committed = True
            return result
        finally:
            self._active = False
            self._connection.transaction_id = None

    async def rollback(self):
        try:
            if self._active:
                await self._connection.rpc("cancel", [self._connection.transaction_id])
        finally:
            self._active = False
            self._rolled_back = True
            self._connection.transaction_id = None


def _is_conflict(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(term in text for term in (
        "transaction conflict", "read or write conflict", "concurrent",
        "already exists", "already contains", "failed to commit transaction",
    ))


class _MigrationExecutor(MigrationExecutor):
    def _sort_by_dependencies(self, migrations):
        # Upstream treats dependencies already applied as a circular dependency.
        pending = {migration.name: migration for migration in migrations}
        ordered = []
        while pending:
            ready = [m for m in pending.values() if not set(m.dependencies) & pending.keys()]
            if not ready:
                raise ValueError("Circular migration dependency")
            for migration in ready:
                ordered.append(migration)
                del pending[migration.name]
        return ordered

    async def ensure_migrations_table(self) -> None:
        # Upstream redefines fields twice per migrate(), without IF NOT EXISTS.
        client = await Connections.get_client()
        await client.query("""
            DEFINE TABLE IF NOT EXISTS _surreal_orm_migrations SCHEMAFULL;
            DEFINE FIELD IF NOT EXISTS name ON _surreal_orm_migrations TYPE string;
            DEFINE FIELD IF NOT EXISTS applied_at ON _surreal_orm_migrations TYPE datetime DEFAULT time::now();
            DEFINE INDEX IF NOT EXISTS migration_name ON _surreal_orm_migrations FIELDS name UNIQUE;
        """)


class Repository:
    """Dictionary CRUD; IDs and reference values are bare IDs at this boundary.

    Unknown top-level keys are stored losslessly in payload, then flattened on
    output. Common fields are Pydantic validated. Filters are equality-only,
    on known fields or individual payload keys. Update merges a partial dict.
    Use ``async with repo.transaction() as tx`` for multi-record atomicity;
    transaction-bound methods read their own writes. Do not share tx across tasks.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self._name = "recipe_" + uuid4().hex
        self._connection: _Connection | None = None
        self._tx: _InteractiveTransaction | None = None
        self._closed = False
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> "Repository":
        async with self._connect_lock:
            return await self._connect()

    async def _connect(self) -> "Repository":
        if self._closed:
            raise RuntimeError("Repository is closed")
        if self._connection is None:
            url = self.settings.db_url.rstrip("/")
            url = re.sub(r"^http", "ws", url)
            if not url.endswith("/rpc"):
                url += "/rpc"
            connection = _Connection(url, self.settings.db_namespace, self.settings.db_database, auto_reconnect=False)
            try:
                await connection.connect()
                await connection.signin(user=self.settings.db_user, password=self.settings.db_password.get_secret_value())
            except BaseException:
                await connection.close()
                raise
            self._connection = connection
            # Pinned ORM has no public existing-client registration API. Named
            # contextvar selection prevents cross-request/transaction leakage.
            Connections.add_connection(
                self._name, url=self.settings.db_url, user=self.settings.db_user,
                password=self.settings.db_password.get_secret_value(),
                namespace=self.settings.db_namespace, database=self.settings.db_database,
            )
            Connections._clients[self._name] = connection
        return self

    async def close(self) -> None:
        if self._connection is not None:
            Connections._clients.pop(self._name, None)
            await Connections.remove_connection(self._name)
            await self._connection.close()
            self._connection = None
        self._closed = True

    async def __aenter__(self):
        return await self.connect()

    async def __aexit__(self, *exc):
        await self.close()

    async def migrate(self) -> list[str]:
        """Explicit maintenance only. DDL is nontransactional; back up first."""
        await self.connect()
        async with Connections.using(self._name):
            # Identifiers validated by Settings; no user-controlled SQL values.
            await self._connection.query(
                f"DEFINE NAMESPACE IF NOT EXISTS `{self.settings.db_namespace}`; "
                f"DEFINE DATABASE IF NOT EXISTS `{self.settings.db_database}`;"
            )
            return await _MigrationExecutor(self.settings.migrations_dir).migrate()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["Repository"]:
        if self._closed:
            raise RuntimeError("Repository is closed")
        if self._tx is not None:
            raise RuntimeError("Nested transactions are not supported; pass the existing tx repository")
        bound = Repository(self.settings)
        try:
            await bound.connect()
            async with _InteractiveTransaction(bound._connection) as transaction:
                bound._tx = transaction
                yield bound
        except Exception as exc:
            if _is_conflict(exc):
                raise ConflictError("Concurrent database modification; retry the entire transaction") from exc
            raise
        finally:
            await bound.close()

    @staticmethod
    def _model(table: str):
        if table not in MODELS:
            raise ValueError(f"Unknown table: {table}")
        return MODELS[table]

    @staticmethod
    def _id(table: str, id: str) -> str:
        if id.startswith(table + ":"):
            id = id[len(table) + 1:]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", id):
            raise ValueError("Invalid record ID")
        return id

    @staticmethod
    def _output(model: DomainModel) -> dict[str, Any]:
        data = model.model_dump(mode="json")
        payload = data.pop("payload", {})
        for name, target in model.get_foreign_key_targets().items():
            if data.get(name) and target and data[name].startswith(target + ":"):
                data[name] = data[name][len(target) + 1:]
        return {**payload, **data}

    def _input(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        model = self._model(table)
        if "id" in data:
            raise ValueError("Pass id separately; IDs are immutable")
        field_names = {
            alias: name
            for name, field in model.model_fields.items()
            if isinstance(alias := field.alias, str)
        }
        result = {
            field_names.get(key, key): value
            for key, value in data.items()
            if key in model.model_fields or key in field_names
        }
        payload = dict(result.get("payload", {}))
        payload.update({
            key: value
            for key, value in data.items()
            if key not in model.model_fields and key not in field_names
        })
        result["payload"] = payload
        for name, target in model.get_foreign_key_targets().items():
            if result.get(name) is not None and target:
                result[name] = f"{target}:{self._id(target, result[name])}"
        return result

    async def get(self, table: str, id: str) -> dict[str, Any] | None:
        model = self._model(table)
        id = self._id(table, id)
        await self.connect()
        async with Connections.using(self._name):
            try:
                value = await model.objects().get(id)
            except model.DoesNotExist:
                return None
        return self._output(value)

    async def list(self, table: str, filters: dict | None = None, limit: int = 100, start: int = 0) -> list[dict]:
        model = self._model(table)
        if not 0 <= limit <= 1000 or start < 0:
            raise ValueError("limit must be 0..1000 and start nonnegative")
        if limit == 0:
            return []
        kwargs = {}
        for key, value in (filters or {}).items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) or "__" in key:
                raise ValueError("Filters accept simple field names only")
            if key in model.get_foreign_key_targets() and value is not None:
                from surreal_sdk.protocol.cbor import RecordId
                target = model.get_foreign_key_targets()[key]
                value = RecordId(target, self._id(target, value))
            kwargs[key if key in model.model_fields else "payload." + key] = value
        await self.connect()
        async with Connections.using(self._name):
            values = await model.objects().filter(**kwargs).order_by("id").limit(limit).offset(start).exec()
        return [self._output(value) for value in values]

    async def _validate_references(self, value: DomainModel) -> None:
        for field, target in value.get_foreign_key_targets().items():
            reference = getattr(value, field)
            if reference is not None and target and await self.get(target, reference) is None:
                raise NotFoundError(f"Referenced {target} record does not exist")

    async def create(self, table: str, data: dict, id: str | None = None) -> dict:
        if self._tx is None:
            async with self.transaction() as tx:
                return await tx.create(table, data, id)
        id = self._id(table, id or str(uuid4()))
        if await self.get(table, id) is not None:
            raise ConflictError("Record already exists")
        model = self._model(table)
        value = model(id=id, **self._input(table, data))
        # ORM save excludes unset defaults; explicitly validate the full dump.
        value = model.model_validate(value.model_dump())
        await self._validate_references(value)
        async with Connections.using(self._name):
            await value.save(tx=self._tx)
        return await self.get(table, id)

    async def update(self, table: str, id: str, data: dict) -> dict:
        if table in {"revisions", "audit"}:
            raise ValueError("History and audit rows are immutable")
        if self._tx is None:
            async with self.transaction() as tx:
                return await tx.update(table, id, data)
        current = await self.get(table, id)
        if current is None:
            raise NotFoundError(f"{table}:{id}")
        current.pop("id")
        merged = {**current, **data, "updated_at": utcnow()}
        value = self._model(table)(id=self._id(table, id), **self._input(table, merged))
        value._db_persisted = True
        await self._validate_references(value)
        async with Connections.using(self._name):
            await value.save(tx=self._tx)
        return await self.get(table, id)

    async def delete(self, table: str, id: str) -> None:
        if table in {"revisions", "audit"}:
            raise ValueError("History and audit rows are immutable")
        if self._tx is None:
            async with self.transaction() as tx:
                await tx.delete(table, id)
            return
        if await self.get(table, id) is None:
            return
        value = self._model(table)(id=self._id(table, id))
        async with Connections.using(self._name):
            await value.delete(tx=self._tx)

    async def compare_and_swap(self, table: str, id: str, expected_revision: int, data: dict) -> dict:
        """Atomic revision check/update; safe for recipes, pairings, jobs, usage.

        Conflicts may also surface at transaction exit. Do not retry stale recipe
        edits automatically. Retry complete transactions for counters/job claims.
        """
        if "revision" not in self._model(table).model_fields:
            raise ValueError("Table has no revision field")
        if self._tx is None:
            async with self.transaction() as tx:
                return await tx.compare_and_swap(table, id, expected_revision, data)
        current = await self.get(table, id)
        if current is None:
            raise NotFoundError(f"{table}:{id}")
        if current["revision"] != expected_revision:
            raise ConflictError("Revision changed")
        return await self.update(table, id, {**data, "revision": expected_revision + 1})

    async def save_recipe_revision(self, id: str, expected_revision: int, data: dict, *, actor_id: str | None = None, reason: str = "edit") -> dict:
        if self._tx is None:
            async with self.transaction() as tx:
                return await tx.save_recipe_revision(id, expected_revision, data, actor_id=actor_id, reason=reason)
        previous = await self.get("recipes", id)
        result = await self.compare_and_swap("recipes", id, expected_revision, data)
        history = await self.list("revisions", {"recipe_id": id, "revision": expected_revision}, limit=1)
        if not history:
            await self.create("revisions", {"recipe_id": id, "actor_id": actor_id, "revision": expected_revision, "reason": reason, "content": previous})
        return result
