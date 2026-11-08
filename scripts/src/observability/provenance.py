"""
Linking record items to the step that produced them (Provenance.step_id),
and checking those links against pipeline_steps before a record is saved.
"""

from uuid import UUID

from pydantic import BaseModel

from record.common import Provenance


def _holders(model, path: str = ""):
    """(path, model) for every model in the tree that has a provenance field."""
    if isinstance(model, Provenance):
        return
    if isinstance(model, BaseModel):
        fields = type(model).model_fields
        if "provenance" in fields:
            yield path or type(model).__name__, model
        for name in fields:
            if name != "provenance":
                child = f"{path}.{name}" if path else name
                yield from _holders(getattr(model, name), child)
    elif isinstance(model, (list, tuple)):
        for i, value in enumerate(model):
            yield from _holders(value, f"{path}[{i}]")
    elif isinstance(model, dict):
        # By position: a key could be document text, and paths get logged.
        for i, value in enumerate(model.values()):
            yield from _holders(value, f"{path}{{{i}}}")


def stamp(model: BaseModel, provenance: Provenance, *, overwrite: bool = False) -> int:
    """
    Give every item in `model` (a record, an item, a list's owner) a copy of
    `provenance`, typically step.provenance(). Items that already have one
    keep it unless `overwrite`. Returns how many items were stamped.
    """
    count = 0
    for _, holder in _holders(model):
        if overwrite or holder.provenance is None:
            holder.provenance = provenance.model_copy(deep=True)
            count += 1
    return count


def check_provenance(conn, model: BaseModel, *, run_id: str | None = None) -> list[str]:
    """
    Problems with the provenance in `model`: an item without one, one
    without a step id, or a step id that isn't a step of the stated run and
    node (and of `run_id`, if given). Empty when every item traces back to
    its step. The messages hold paths and ids only, so they can be logged.
    """
    problems = []
    linked: list[tuple[str, Provenance, str]] = []
    for path, holder in _holders(model):
        p = holder.provenance
        if p is None:
            problems.append(f"{path}: no provenance")
        elif p.step_id is None:
            problems.append(f"{path}: provenance has no step id")
        else:
            try:
                linked.append((path, p, str(UUID(p.step_id))))
            except ValueError:
                problems.append(f"{path}: step id {p.step_id!r} is not a uuid")
        if p is not None and run_id is not None and p.run_id != str(run_id):
            problems.append(f"{path}: provenance is from run {p.run_id}, not {run_id}")

    steps = {}
    if linked:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id::text, run_id::text, node FROM pipeline_steps"
                " WHERE id = ANY(%s::uuid[])",
                (sorted({step_id for _, _, step_id in linked}),),
            )
            steps = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    for path, p, step_id in linked:
        found = steps.get(step_id)
        if found is None:
            problems.append(f"{path}: step {step_id} is not in pipeline_steps")
            continue
        step_run, step_node = found
        if step_run != p.run_id:
            problems.append(f"{path}: step {step_id} belongs to run {step_run}")
        if step_node != p.node:
            problems.append(
                f"{path}: step {step_id} is a {step_node} step, not {p.node}"
            )
    return problems
