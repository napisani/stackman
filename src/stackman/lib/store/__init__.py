"""SQLite persistence for stackman (schema, repos, branches, stack labels)."""

from .branches import (
    delete_all_branches,
    delete_all_branches_global,
    delete_branch,
    get_branch,
    list_all_branches,
    list_branches,
    list_branches_with_parent,
    reparent_children_and_delete_branch,
    update_branch_fork_point,
    upsert_branch,
)
from .repos import get_repo, upsert_repo
from .schema import initialize
from .stacks import (
    clear_branch_labels,
    create_stack,
    get_stack,
    label_branch,
    list_branch_labels,
    list_branch_names_with_stack_label,
)

__all__ = [
    "initialize",
    "upsert_repo",
    "get_repo",
    "upsert_branch",
    "update_branch_fork_point",
    "get_branch",
    "list_branches",
    "list_all_branches",
    "list_branches_with_parent",
    "delete_branch",
    "delete_all_branches",
    "delete_all_branches_global",
    "reparent_children_and_delete_branch",
    "clear_branch_labels",
    "create_stack",
    "get_stack",
    "label_branch",
    "list_branch_labels",
    "list_branch_names_with_stack_label",
]
