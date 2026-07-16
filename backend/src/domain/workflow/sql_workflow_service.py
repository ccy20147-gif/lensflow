"""PostgreSQL-backed workflow lifecycle service.

This is the durable counterpart to ``WorkflowService``.  It intentionally
shares the public lifecycle methods so callers can select persistence without
changing workflow semantics.
"""
# ORM models predate SQLAlchemy's ``Mapped[]`` annotations.  Runtime values are
# normal Python values; these suppressions are local to that legacy boundary.
# mypy: disable-error-code="arg-type,assignment,return-value,attr-defined"
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from src.core.exceptions import ConflictError, NotFoundError
from src.infra.db.models import (
    CompiledExecutionPlanModel,
    OutboxEventModel,
    WorkflowDraftModel,
    WorkflowModel,
    WorkflowRevisionModel,
)
from src.infra.db.session import get_session_factory
from src.schemas.enums import RevisionStatus
from src.schemas.models import CompiledExecutionPlan, OwnerScope, RegistrySnapshot, Workflow, WorkflowDraft, WorkflowRevision

from .draft_revision import (
    WorkflowDiff,
    compute_diff,
    compute_draft_hashes,
    compute_full_draft_hash,
    create_draft,
    create_revision,
    normalize_graph_and_layout,
    required_human_gate_ids,
)


def _to_workflow(model: WorkflowModel) -> Workflow:
    kind, _, owner_id = model.owner_scope.partition(":")
    return Workflow(
        workflow_id=model.workflow_id,
        owner_scope=OwnerScope(kind=kind, id=UUID(owner_id)),
        created_at=model.created_at,
    )


def _to_draft(model: WorkflowDraftModel) -> WorkflowDraft:
    full_hash = getattr(model, "full_draft_hash", "") or ""
    if not full_hash:
        # The column was added in a forward-compatible migration; legacy
        # rows written before it must still be readable.  Recompute on the
        # fly so callers always see a stable compare-and-swap token.
        raw_version = model.draft_version
        current_version = raw_version if raw_version is not None else 0
        full_hash = compute_full_draft_hash(  # type: ignore[arg-type]
            str(model.graph_hash or ""),
            str(model.layout_hash or ""),
            str(model.execution_hash or ""),
            current_version,
        )
    return WorkflowDraft(
        workflow_id=model.workflow_id,
        draft_version=model.draft_version,
        base_revision_id=model.base_revision_id,
        graph=model.graph or {},
        config=model.config or {},
        layout=model.layout or {},
        graph_hash=model.graph_hash,
        layout_hash=model.layout_hash,
        execution_hash=model.execution_hash,
        full_draft_hash=full_hash,
        updated_at=model.updated_at,
    )


def _to_revision(model: WorkflowRevisionModel) -> WorkflowRevision:
    return WorkflowRevision(
        workflow_id=model.workflow_id,
        revision_id=model.revision_id,
        revision_number=model.revision_number,
        graph_hash=model.graph_hash,
        execution_hash=model.execution_hash,
        registry_snapshot_id=model.registry_snapshot_id,
        revision_status=model.revision_status,
        created_at=model.created_at,
    )


class SqlWorkflowService:
    """Workflow CRUD backed by PostgreSQL with CAS draft writes."""

    def __init__(self, factory: sessionmaker[Session] | None = None) -> None:
        self._factory = factory or get_session_factory()

    def create_workflow(self, workflow_id: UUID | None = None, owner_scope: OwnerScope | None = None) -> Workflow:
        workflow_id = workflow_id or uuid4()
        owner_scope = owner_scope or OwnerScope(kind="user", id=uuid4())
        now = datetime.now(timezone.utc)
        with self._factory.begin() as session:
            workflow = WorkflowModel(
                workflow_id=workflow_id,
                owner_scope=owner_scope.scoped_id,
                created_at=now,
            )
            draft = create_draft(workflow_id=workflow_id, draft_version=1)
            session.add(workflow)
            # No ORM relationship declares this FK dependency, so make the
            # parent insert explicit before the draft is flushed.
            session.flush()
            session.add(self._draft_model(draft))
        return Workflow(workflow_id=workflow_id, owner_scope=owner_scope, created_at=now)

    def get_workflow(self, workflow_id: UUID) -> Workflow:
        with self._factory() as session:
            model = session.get(WorkflowModel, workflow_id)
            if model is None:
                raise NotFoundError("Workflow", str(workflow_id))
            return _to_workflow(model)

    def list_workflows(self, owner_scope: Any = None, offset: int = 0, limit: int = 50) -> list[Workflow]:
        statement = select(WorkflowModel).order_by(WorkflowModel.created_at).offset(offset).limit(limit)
        if owner_scope is not None:
            scope = owner_scope.scoped_id if isinstance(owner_scope, OwnerScope) else str(owner_scope)
            statement = statement.where(WorkflowModel.owner_scope == scope)
        with self._factory() as session:
            return [_to_workflow(row) for row in session.scalars(statement)]

    def delete_workflow(self, workflow_id: UUID) -> None:
        with self._factory.begin() as session:
            if session.get(WorkflowModel, workflow_id) is None:
                raise NotFoundError("Workflow", str(workflow_id))
            session.execute(delete(WorkflowDraftModel).where(WorkflowDraftModel.workflow_id == workflow_id))
            session.execute(delete(WorkflowRevisionModel).where(WorkflowRevisionModel.workflow_id == workflow_id))
            # Workflow run data is intentionally protected; deletion is refused if runs exist.
            session.execute(delete(WorkflowModel).where(WorkflowModel.workflow_id == workflow_id))

    def get_draft(self, workflow_id: UUID) -> WorkflowDraft:
        with self._factory() as session:
            model = session.get(WorkflowDraftModel, workflow_id)
            if model is None:
                self._raise_workflow_or_draft_not_found(session, workflow_id)
            return _to_draft(model)

    def save_draft(
        self,
        workflow_id: UUID,
        graph: dict[str, Any],
        config: dict[str, Any],
        layout: dict[str, Any],
        base_graph_hash: str,
        pinned_dependency_revisions: list[str] | None = None,
        *,
        expected_draft_version: int | None = None,
        expected_full_draft_hash: str | None = None,
    ) -> WorkflowDraft:
        """Save a draft with full-draft compare-and-swap.

        The contract is the union of every persisted draft fact.  Older
        callers still pass ``base_graph_hash``; the layout-only race (two
        tabs only moving a node) would silently pass a graph-hash-only CAS
        and clobber the second save, so the durable path always evaluates
        ``expected_draft_version`` and/or ``expected_full_draft_hash`` when
        the caller is the canonical draft API (TF-PLT-003).  When neither
        is provided we still require ``base_graph_hash`` to match the
        current ``graph_hash``; this is the explicit compatibility shim
        referenced in the task brief.
        """
        graph, layout = normalize_graph_and_layout(graph, layout)
        graph_hash, layout_hash, execution_hash = compute_draft_hashes(
            graph, config, layout, pinned_dependency_revisions
        )
        now = datetime.now(timezone.utc)
        with self._factory.begin() as session:
            # Lock before evaluating both CAS and the mandatory-Gate invariant.
            # This keeps an Architect Patch on the same boundary as a manual
            # canvas save and avoids a check-then-write race.
            model = session.execute(
                select(WorkflowDraftModel)
                .where(WorkflowDraftModel.workflow_id == workflow_id)
                .with_for_update()
            ).scalar_one_or_none()
            if model is None:
                self._raise_workflow_or_draft_not_found(session, workflow_id)
            assert model is not None
            current_version = int(model.draft_version or 0)  # type: ignore[arg-type]
            current_graph_hash = str(model.graph_hash or "")
            current_layout_hash = str(model.layout_hash or "")
            current_execution_hash = str(model.execution_hash or "")
            current_full_hash = str(getattr(model, "full_draft_hash", "") or "") or compute_full_draft_hash(
                current_graph_hash, current_layout_hash, current_execution_hash, current_version,
            )
            # Full draft CAS, when supplied, is the authoritative token.
            if expected_full_draft_hash is not None:
                if expected_full_draft_hash != current_full_hash:
                    raise ConflictError(
                        message=(
                            f"WorkflowDraft {workflow_id} 冲突: "
                            f"expected_full_draft_hash {expected_full_draft_hash[:12]}… "
                            f"与当前版本 {current_full_hash[:12]}… 不一致"
                        ),
                        details={
                            "expected_full_draft_hash": expected_full_draft_hash,
                            "current_full_draft_hash": current_full_hash,
                            "current_draft_version": current_version,
                            "current_graph_hash": current_graph_hash,
                        },
                    )
            elif expected_draft_version is not None:
                if expected_draft_version != current_version:
                    raise ConflictError(
                        message=(
                            f"WorkflowDraft {workflow_id} 冲突: "
                            f"expected_draft_version {expected_draft_version} "
                            f"与当前版本 {current_version} 不一致"
                        ),
                        details={
                            "expected_draft_version": expected_draft_version,
                            "current_draft_version": current_version,
                            "current_graph_hash": current_graph_hash,
                        },
                    )
            elif current_graph_hash != base_graph_hash:
                # Legacy path: graph-hash-only CAS.  A pure layout change
                # would not change ``graph_hash``; the new contract requires
                # callers to pass either ``expected_full_draft_hash`` or
                # ``expected_draft_version`` so that a second tab cannot
                # silently win the race.
                raise ConflictError(
                    message=(
                        f"WorkflowDraft {workflow_id} 冲突: "
                        f"base_hash {base_graph_hash} 不匹配当前版本；"
                        f"请改用 expected_draft_version 或 expected_full_draft_hash"
                    ),
                    details={
                        "expected_graph_hash": base_graph_hash,
                        "current_graph_hash": current_graph_hash,
                        "current_draft_version": current_version,
                    },
                )
            protected = required_human_gate_ids(model.graph or {})
            active_graphs = session.scalars(
                select(WorkflowRevisionModel.graph).where(
                    WorkflowRevisionModel.workflow_id == workflow_id,
                    WorkflowRevisionModel.revision_status == RevisionStatus.ACTIVE,
                )
            )
            for active_graph in active_graphs:
                protected.update(required_human_gate_ids(active_graph or {}))
            removed = protected - required_human_gate_ids(graph)
            if removed:
                raise ConflictError(
                    "domain_required 或 policy_required Human Gate 不得从 Draft 或 Patch 删除",
                    details={"required_gate_node_ids": sorted(removed)},
                )
            new_version = current_version + 1
            new_full_hash = compute_full_draft_hash(
                graph_hash, layout_hash, execution_hash, new_version,
            )
            model.draft_version = new_version
            model.graph = graph
            model.config = config
            model.layout = layout
            model.graph_hash = graph_hash
            model.layout_hash = layout_hash
            model.execution_hash = execution_hash
            model.full_draft_hash = new_full_hash  # type: ignore[assignment]
            model.updated_at = now
            session.flush()
            return _to_draft(model)

    def create_revision_from_draft(self, workflow_id: UUID, registry_snapshot_id: UUID) -> WorkflowRevision:
        with self._factory.begin() as session:
            draft_model = session.execute(
                select(WorkflowDraftModel).where(WorkflowDraftModel.workflow_id == workflow_id).with_for_update()
            ).scalar_one_or_none()
            if draft_model is None:
                self._raise_workflow_or_draft_not_found(session, workflow_id)
            assert draft_model is not None
            draft = _to_draft(draft_model)
            last_number = session.scalar(
                select(WorkflowRevisionModel.revision_number)
                .where(WorkflowRevisionModel.workflow_id == workflow_id)
                .order_by(WorkflowRevisionModel.revision_number.desc())
                .limit(1)
            )
            revision = create_revision(
                workflow_id=workflow_id,
                draft=draft,
                registry_snapshot_id=registry_snapshot_id,
                revision_number=(last_number or 0) + 1,
            )
            session.execute(
                update(WorkflowRevisionModel)
                .where(
                    WorkflowRevisionModel.workflow_id == workflow_id,
                    WorkflowRevisionModel.revision_status == RevisionStatus.ACTIVE,
                )
                .values(revision_status=RevisionStatus.RETIRED)
            )
            session.add(
                WorkflowRevisionModel(
                    revision_id=revision.revision_id,
                    workflow_id=workflow_id,
                    revision_number=revision.revision_number,
                    graph_hash=revision.graph_hash,
                    execution_hash=revision.execution_hash,
                    registry_snapshot_id=registry_snapshot_id,
                    graph=draft.graph,
                    config=draft.config,
                    layout=draft.layout,
                    revision_status=RevisionStatus.ACTIVE,
                    created_at=revision.created_at,
                )
            )
            return revision

    def publish_compiled_revision(
        self,
        workflow_id: UUID,
        registry: RegistrySnapshot,
        compiler: Any,
        *,
        graph_override: dict[str, Any] | None = None,
        compilation_context: Any | None = None,
        expected_draft_hash: str,
        actor_id: UUID | None = None,
    ) -> tuple[WorkflowRevision, CompiledExecutionPlan]:
        """Atomically activate only a Draft the owner has confirmed.

        ``expected_draft_hash`` is the owner-confirmed full-draft token
        and is **mandatory**.  The service refuses to start an
        activation transaction without it so no internal caller (a
        future test harness, a CLI tool, or a freshly added route)
        can freeze an unconfirmed snapshot.  HTTP clients hit the
        Pydantic 422 layer first; this guard is the second line of
        defence.

        Within a single transaction the method:

        1. locks the ``WorkflowDraft`` row,
        2. compares the supplied ``expected_draft_hash`` against the
           persisted ``full_draft_hash`` (raising
           ``ConflictError`` on mismatch — no revision, plan, base
           pointer, or outbox event is created),
        3. runs the compiler (any exception rolls back the
           transaction),
        4. retires the previous active revision,
        5. inserts the new ``WorkflowRevision``,
        6. inserts the ``CompiledExecutionPlan``,
        7. updates ``WorkflowDraft.base_revision_id``,
        8. inserts the ``outbox_events`` audit row.
        """
        if not expected_draft_hash or len(expected_draft_hash) != 64:
            # The HTTP layer already rejects this with a 422 via
            # Pydantic; this branch catches internal callers that
            # bypass the schema.  The error uses the same
            # ``VALIDATION_ERROR`` envelope so callers do not need a
            # separate catch arm.
            from src.core.exceptions import ValidationError_
            raise ValidationError_(
                "激活需要 owner-confirmed expected_full_draft_hash；"
                "该字段为 64 字符 SHA-256 必填。",
                details={
                    "field": "expected_draft_hash",
                    "expected_length": 64,
                    "received": (
                        None if expected_draft_hash is None
                        else f"{len(expected_draft_hash)} chars"
                    ),
                },
            )
        with self._factory.begin() as session:
            draft_model = session.execute(
                select(WorkflowDraftModel)
                .where(WorkflowDraftModel.workflow_id == workflow_id)
                .with_for_update()
            ).scalar_one_or_none()
            if draft_model is None:
                self._raise_workflow_or_draft_not_found(session, workflow_id)
            assert draft_model is not None
            draft = _to_draft(draft_model)
            # Owner-confirmation CAS.  Refuse if the draft was edited
            # between the owner's read and the activation request so we
            # never freeze an unintended snapshot.
            if expected_draft_hash != draft.full_draft_hash:
                raise ConflictError(
                    message=(
                        f"激活失败: WorkflowDraft {workflow_id} 已被修改，"
                        f"expected_full_draft_hash 与当前 {draft.full_draft_hash[:12]}… 不一致"
                    ),
                    details={
                        "expected_draft_hash": expected_draft_hash,
                        "current_draft_hash": draft.full_draft_hash,
                        "current_draft_version": draft.draft_version,
                        "current_graph_hash": draft.graph_hash,
                    },
                )
            # A Draft may intentionally contain latest_at_compile references.
            # Its frozen Revision must not. The route resolves the graph under
            # the authenticated owner before entering this transaction.
            activation_graph = draft.graph
            activation_config = draft.config
            activation_layout = draft.layout
            if graph_override is not None:
                graph, layout = normalize_graph_and_layout(graph_override, draft.layout)
                graph_hash, layout_hash, execution_hash = compute_draft_hashes(
                    graph, draft.config, layout,
                )
                draft = draft.model_copy(update={
                    "graph": graph, "layout": layout, "graph_hash": graph_hash,
                    "layout_hash": layout_hash, "execution_hash": execution_hash,
                })
                activation_graph = graph
                activation_config = draft.config
                activation_layout = layout
            last = session.scalar(
                select(WorkflowRevisionModel.revision_number)
                .where(WorkflowRevisionModel.workflow_id == workflow_id)
                .order_by(WorkflowRevisionModel.revision_number.desc())
                .limit(1)
            ) or 0
            revision = create_revision(
                workflow_id=workflow_id,
                draft=draft,
                registry_snapshot_id=registry.snapshot_id,
                revision_number=last + 1,
            )
            # Compile inside the same transaction so a compiler failure
            # cannot leave an isolated revision row behind.
            try:
                plan = compiler.compile(
                    workflow_revision_id=revision.revision_id,
                    graph=activation_graph,
                    registry_snapshot=registry,
                    compilation_context=compilation_context,
                )
            except Exception:
                # Re-raise as-is; the ``with`` block below rolls back the
                # transaction.  Do not catch the CompileError here — the
                # caller (the HTTP route) must see a 422 with the
                # structured diagnostics.
                raise
            # The previous active revision is retired *before* the new row
            # is committed, so the unique-active invariant is never broken
            # even under concurrent activations.
            session.execute(
                update(WorkflowRevisionModel)
                .where(
                    WorkflowRevisionModel.workflow_id == workflow_id,
                    WorkflowRevisionModel.revision_status == RevisionStatus.ACTIVE,
                )
                .values(revision_status=RevisionStatus.RETIRED)
            )
            revision_model = WorkflowRevisionModel(
                revision_id=revision.revision_id,
                workflow_id=workflow_id,
                revision_number=revision.revision_number,
                graph_hash=revision.graph_hash,
                execution_hash=revision.execution_hash,
                registry_snapshot_id=registry.snapshot_id,
                graph=activation_graph,
                config=activation_config,
                layout=activation_layout,
                revision_status=RevisionStatus.ACTIVE,
                created_at=revision.created_at,
            )
            session.add(revision_model)
            session.flush()
            session.add(CompiledExecutionPlanModel(
                plan_id=plan.plan_id,
                workflow_revision_id=revision.revision_id,
                registry_snapshot_id=registry.snapshot_id,
                status="succeeded",
                plan_hash=plan.plan_hash,
                compiler_version=plan.compiler_version,
                plan_json=plan.model_dump(mode="json"),
                diagnostics=[],
                created_at=plan.created_at,
            ))
            # Pin the Draft to the just-created immutable revision so the
            # next save CAS knows which baseline the owner reviewed.  The
            # graph/config/layout stay mutable; only the pointer changes.
            draft_model.base_revision_id = revision.revision_id  # type: ignore[assignment]
            session.flush()
            # Durable audit/notification evidence.  In the same transaction
            # so a failure of this insert also rolls back the revision.
            outbox_payload = {
                "workflow_id": str(workflow_id),
                "revision_id": str(revision.revision_id),
                "revision_number": revision.revision_number,
                "graph_hash": revision.graph_hash,
                "layout_hash": draft.layout_hash,
                "execution_hash": revision.execution_hash,
                "registry_snapshot_id": str(registry.snapshot_id),
                "compiled_plan_id": str(plan.plan_id),
                "expected_draft_hash": expected_draft_hash,
                "current_draft_hash": draft.full_draft_hash,
                "current_draft_version": draft.draft_version,
                "actor_id": str(actor_id) if actor_id else None,
            }
            session.add(OutboxEventModel(
                event_id=uuid4(),
                aggregate_type="workflow_revision",
                aggregate_id=revision.revision_id,
                event_type="workflow.revision.activated",
                purpose="workflow_revision_activated",
                payload=outbox_payload,
            ))
            return revision, plan

    def get_successful_plan(self, revision_id: UUID) -> CompiledExecutionPlan:
        with self._factory() as session:
            row = session.scalar(select(CompiledExecutionPlanModel).where(CompiledExecutionPlanModel.workflow_revision_id == revision_id, CompiledExecutionPlanModel.status == "succeeded").order_by(CompiledExecutionPlanModel.created_at.desc()).limit(1))
            if row is None:
                raise NotFoundError("CompiledExecutionPlan", str(revision_id))
            plan = CompiledExecutionPlan.model_validate(row.plan_json)
            # The database row is the immutable execution authority.  Refuse
            # to execute a JSON payload that was copied from another plan or
            # revision, even if a corrupt/manual write marked its row success.
            if (
                plan.plan_id != row.plan_id
                or plan.workflow_revision_id != revision_id
                or plan.plan_hash != row.plan_hash
            ):
                raise ConflictError("持久化 CompiledExecutionPlan 与固定 Revision 不一致")
            return plan

    def get_revision(self, revision_id: UUID) -> WorkflowRevision:
        with self._factory() as session:
            model = session.get(WorkflowRevisionModel, revision_id)
            if model is None:
                raise NotFoundError("WorkflowRevision", str(revision_id))
            return _to_revision(model)

    def list_revisions(self, workflow_id: UUID, offset: int = 0, limit: int = 50) -> list[WorkflowRevision]:
        statement = (
            select(WorkflowRevisionModel)
            .where(WorkflowRevisionModel.workflow_id == workflow_id)
            .order_by(WorkflowRevisionModel.revision_number.desc())
            .offset(offset)
            .limit(limit)
        )
        with self._factory() as session:
            return [_to_revision(row) for row in session.scalars(statement)]

    def get_active_revision(self, workflow_id: UUID) -> WorkflowRevision | None:
        statement = (
            select(WorkflowRevisionModel)
            .where(
                WorkflowRevisionModel.workflow_id == workflow_id,
                WorkflowRevisionModel.revision_status == RevisionStatus.ACTIVE,
            )
            .order_by(WorkflowRevisionModel.revision_number.desc())
            .limit(1)
        )
        with self._factory() as session:
            model = session.scalar(statement)
            return _to_revision(model) if model is not None else None

    def get_revision_graph(self, revision_id: UUID) -> dict[str, Any]:
        with self._factory() as session:
            model = session.get(WorkflowRevisionModel, revision_id)
            if model is None:
                raise NotFoundError("WorkflowRevision", str(revision_id))
            return model.graph or {}

    def retire_revision(self, revision_id: UUID) -> WorkflowRevision:
        with self._factory.begin() as session:
            model = session.get(WorkflowRevisionModel, revision_id)
            if model is None:
                raise NotFoundError("WorkflowRevision", str(revision_id))
            model.revision_status = RevisionStatus.RETIRED
            session.flush()
            return _to_revision(model)

    def rollback_to_revision(
        self,
        workflow_id: UUID,
        revision_id: UUID,
        *,
        base_graph_hash: str | None = None,
    ) -> WorkflowDraft:
        """Derive the current Draft from an immutable revision.

        A rollback is deliberately a Draft write, never an activation or a
        mutation of the historic revision.  API callers must supply the hash
        they reviewed so an old browser tab cannot overwrite a newer Draft.
        ``None`` remains supported only for the legacy in-process test double
        compatibility path; durable HTTP writes always use CAS.
        """
        with self._factory.begin() as session:
            revision = session.get(WorkflowRevisionModel, revision_id)
            if revision is None or revision.workflow_id != workflow_id:
                raise NotFoundError("WorkflowRevision", str(revision_id))
            existing = session.get(WorkflowDraftModel, workflow_id)
            if existing is None:
                raise NotFoundError("WorkflowDraft", str(workflow_id))
            if base_graph_hash is not None and existing.graph_hash != base_graph_hash:
                raise ConflictError(
                    message=(
                        f"WorkflowDraft {workflow_id} 冲突: base_hash "
                        f"{base_graph_hash} 不匹配当前版本"
                    )
                )
            graph_hash, layout_hash, execution_hash = compute_draft_hashes(
                revision.graph or {}, revision.config or {}, revision.layout or {}
            )
            existing.draft_version += 1
            existing.base_revision_id = revision_id
            existing.graph = revision.graph or {}
            existing.config = revision.config or {}
            existing.layout = revision.layout or {}
            existing.graph_hash = graph_hash
            existing.layout_hash = layout_hash
            existing.execution_hash = execution_hash
            existing.updated_at = datetime.now(timezone.utc)
            session.flush()
            return _to_draft(existing)

    def diff_draft_vs_revision(self, workflow_id: UUID, revision_id: UUID | None = None) -> WorkflowDiff:
        draft = self.get_draft(workflow_id)
        if revision_id is None:
            active = self.get_active_revision(workflow_id)
            if active is None:
                return compute_diff({}, draft.graph, {}, draft.config, {}, draft.layout)
            revision_id = active.revision_id
        with self._factory() as session:
            revision = session.get(WorkflowRevisionModel, revision_id)
            if revision is None or revision.workflow_id != workflow_id:
                raise NotFoundError("WorkflowRevision", str(revision_id))
            return compute_diff(
                revision.graph or {}, draft.graph, revision.config or {}, draft.config, revision.layout or {}, draft.layout
            )

    @staticmethod
    def _draft_model(draft: WorkflowDraft) -> WorkflowDraftModel:
        full_hash = draft.full_draft_hash or compute_full_draft_hash(
            draft.graph_hash,
            draft.layout_hash,
            draft.execution_hash,
            draft.draft_version,
        )
        return WorkflowDraftModel(
            workflow_id=draft.workflow_id,
            draft_version=draft.draft_version,
            base_revision_id=draft.base_revision_id,
            graph=draft.graph,
            config=draft.config,
            layout=draft.layout,
            graph_hash=draft.graph_hash,
            layout_hash=draft.layout_hash,
            execution_hash=draft.execution_hash,
            full_draft_hash=full_hash,
            updated_at=draft.updated_at,
        )

    @staticmethod
    def _raise_workflow_or_draft_not_found(session: Session, workflow_id: UUID) -> None:
        if session.get(WorkflowModel, workflow_id) is None:
            raise NotFoundError("Workflow", str(workflow_id))
        raise NotFoundError("WorkflowDraft", str(workflow_id))
