from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stage0_sim.adapters.elements import FileSystemElementLibrary
from stage0_sim.api.elements import router as element_router
from stage0_sim.api.ui import OperatorSessionStore
from stage0_sim.api.ui_elements import router as ui_element_router
from stage0_sim.application.element_library import (
    ElementConflictError,
    ElementDependencyError,
    ElementLibraryError,
    ElementNotFoundError,
)
from stage0_sim.application.elements import (
    ElementKind,
    NpcRoleElementDefinition,
    ObjectElementDefinition,
    RoomElementDefinition,
    element_content_hash,
)
from stage0_sim.config import Settings


def _reference(
    kind: ElementKind,
    element_id: str,
    content_hash: str,
) -> dict[str, str]:
    return {
        "kind": kind.value,
        "id": element_id,
        "content_hash": content_hash,
    }


def test_filesystem_element_library_crud_and_stale_conflicts(
    tmp_path: Path,
) -> None:
    library = FileSystemElementLibrary(tmp_path / "elements")
    role = NpcRoleElementDefinition(id="cashier", name="Cashier")
    created = library.create(role)
    created_hash = element_content_hash(created)

    assert library.get("cashier", ElementKind.NPC_ROLE) == created
    assert [summary.id for summary in library.list()] == ["cashier"]
    with pytest.raises(ElementConflictError):
        library.create(role)

    updated = role.model_copy(update={"name": "Checkout Cashier"})
    library.update("cashier", updated, created_hash)
    with pytest.raises(ElementConflictError, match="changed since"):
        library.update("cashier", role, created_hash)

    current = library.get("cashier")
    renamed = library.rename(
        "cashier",
        "store-cashier",
        element_content_hash(current),
    )
    assert renamed.id == "store-cashier"
    with pytest.raises(ElementNotFoundError):
        library.get("cashier")

    library.delete("store-cashier", element_content_hash(renamed))
    assert library.list() == ()


def test_library_filters_kinds_and_rejects_wrong_kind(tmp_path: Path) -> None:
    library = FileSystemElementLibrary(tmp_path)
    role = library.create(
        NpcRoleElementDefinition(id="cashier", name="Cashier")
    )
    checkout = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "checkout",
                "name": "Checkout",
                "kind": "object",
                "physical": {"footprint": {"cells": [{"x": 0, "y": 0}]}},
                "object_type": "transaction",
                "offers": [
                    {
                        "id": "buy-meal",
                        "name": "Buy meal",
                        "character_receives": [
                            {"item_id": "meal", "quantity": 1}
                        ],
                    }
                ],
                "operation": "STAFFED",
                "npc_role": _reference(
                    ElementKind.NPC_ROLE,
                    role.id,
                    element_content_hash(role),
                ),
            }
        )
    )

    assert [item.id for item in library.list(ElementKind.OBJECT)] == [
        checkout.id
    ]
    with pytest.raises(ElementLibraryError, match="expected room"):
        library.get(checkout.id, ElementKind.ROOM)


def test_library_blocks_deleting_or_renaming_referenced_elements(
    tmp_path: Path,
) -> None:
    library = FileSystemElementLibrary(tmp_path)
    role = library.create(
        NpcRoleElementDefinition(id="cashier", name="Cashier")
    )
    checkout = library.create(
        ObjectElementDefinition.model_validate(
            {
                "id": "checkout",
                "name": "Checkout",
                "kind": "object",
                "physical": {"footprint": {"cells": [{"x": 0, "y": 0}]}},
                "object_type": "transaction",
                "offers": [
                    {
                        "id": "buy-meal",
                        "name": "Buy meal",
                        "character_receives": [
                            {"item_id": "meal", "quantity": 1}
                        ],
                    }
                ],
                "operation": "STAFFED",
                "npc_role": _reference(
                    ElementKind.NPC_ROLE,
                    role.id,
                    element_content_hash(role),
                ),
            }
        )
    )
    room = library.create(
        RoomElementDefinition.model_validate(
            {
                "id": "dining-room",
                "name": "Dining room",
                "kind": "room",
                "room_type": "DINING",
                "width": 4,
                "height": 4,
                "objects": [
                    {
                        "key": "checkout",
                        "element": _reference(
                            ElementKind.OBJECT,
                            checkout.id,
                            element_content_hash(checkout),
                        ),
                        "position": {"x": 2, "y": 2},
                        "placement": {
                            "anchor": {"x": 22, "y": 22},
                            "parent_relation": {"kind": "ON_FLOOR"},
                        },
                        "staff_position": {"x": 2, "y": 1},
                    }
                ],
            }
        )
    )

    assert [item.id for item in library.dependents("cashier")] == [
        "checkout"
    ]
    assert [item.id for item in library.dependents("checkout")] == [
        room.id
    ]
    with pytest.raises(ElementDependencyError, match="object:checkout"):
        library.delete("cashier", element_content_hash(role))
    with pytest.raises(ElementDependencyError, match="room:dining-room"):
        library.rename(
            "checkout",
            "renamed-checkout",
            element_content_hash(checkout),
        )


def test_library_rejects_unsafe_malformed_and_mismatched_files(
    tmp_path: Path,
) -> None:
    library = FileSystemElementLibrary(tmp_path)
    with pytest.raises(ElementLibraryError):
        library.get("../outside")
    with pytest.raises(ElementLibraryError):
        library.get("con")

    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(ElementLibraryError, match="not valid JSON"):
        library.get("broken")

    (tmp_path / "mismatch.json").write_text(
        json.dumps(
            NpcRoleElementDefinition(
                id="different",
                name="Different",
            ).model_dump(mode="json")
        ),
        encoding="utf-8",
    )
    with pytest.raises(ElementLibraryError, match="declares ID different"):
        library.get("mismatch")


def test_element_files_are_deterministic_and_newline_terminated(
    tmp_path: Path,
) -> None:
    library = FileSystemElementLibrary(tmp_path)
    role = NpcRoleElementDefinition(id="cashier", name="Cashier")
    library.create(role)
    first = (tmp_path / "cashier.json").read_bytes()
    library.update("cashier", role, element_content_hash(role))
    second = (tmp_path / "cashier.json").read_bytes()

    assert first == second
    assert first.endswith(b"\n")
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_element_library_rejects_legacy_content_with_migration_command(
    tmp_path: Path,
) -> None:
    (tmp_path / "old-role.json").write_text(
        '{"schema_version":1,"id":"old-role","name":"Old Role",'
        '"kind":"npc_role"}',
        encoding="utf-8",
    )
    with pytest.raises(
        ElementLibraryError,
        match="stage0-sim migrate content",
    ):
        FileSystemElementLibrary(tmp_path).get("old-role")


def test_element_api_exposes_crud_filters_and_conflicts(
    tmp_path: Path,
) -> None:
    api = FastAPI()
    api.state.element_library = FileSystemElementLibrary(tmp_path)
    api.include_router(element_router)
    role = NpcRoleElementDefinition(id="cashier", name="Cashier")

    with TestClient(api) as client:
        created = client.post(
            "/elements",
            json={"element": role.model_dump(mode="json")},
        )
        assert created.status_code == 201
        content_hash = created.json()["content_hash"]
        assert client.get("/elements?kind=npc_role").json()["elements"][0][
            "id"
        ] == "cashier"
        assert client.get("/elements?kind=room").json() == {"elements": []}
        assert client.get("/elements/cashier").json()["element"]["name"] == (
            "Cashier"
        )

        updated_payload = role.model_copy(
            update={"name": "Updated Cashier"}
        ).model_dump(mode="json")
        updated = client.put(
            "/elements/cashier",
            json={
                "expected_hash": content_hash,
                "element": updated_payload,
            },
        )
        assert updated.status_code == 200
        assert updated.json()["element"]["name"] == "Updated Cashier"

        stale = client.put(
            "/elements/cashier",
            json={
                "expected_hash": content_hash,
                "element": role.model_dump(mode="json"),
            },
        )
        assert stale.status_code == 409

        deleted = client.delete(
            "/elements/cashier",
            params={"expected_hash": updated.json()["content_hash"]},
        )
        assert deleted.status_code == 200
        assert client.get("/elements/cashier").status_code == 404


def test_settings_reads_element_directory_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "element-library"
    monkeypatch.setenv("STAGE0_ELEMENT_DIRECTORY", str(configured))

    assert Settings().element_directory == configured


def test_element_library_ui_creates_duplicates_and_deletes(
    tmp_path: Path,
) -> None:
    api = FastAPI()
    api.state.element_library = FileSystemElementLibrary(tmp_path)
    api.state.operator_sessions = OperatorSessionStore()
    api.include_router(ui_element_router)
    role = NpcRoleElementDefinition(id="cashier", name="Cashier")

    with TestClient(api) as client:
        page = client.get("/ui/elements/")
        assert page.status_code == 200
        assert "<h1>Element Library</h1>" in page.text
        assert "New Building" in page.text

        created = client.post(
            "/ui/elements/save",
            data={
                "resource_id": "cashier",
                "original_id": "",
                "expected_hash": "",
                "selected_kind": "npc_role",
                "element_json": json.dumps(role.model_dump(mode="json")),
            },
            follow_redirects=True,
        )
        assert created.status_code == 200
        assert "Saved Cashier." in created.text
        assert 'value="cashier"' in created.text

        duplicated = client.post(
            "/ui/elements/cashier/duplicate",
            data={"new_id": "cashier-copy"},
            follow_redirects=True,
        )
        assert "Duplicated cashier as cashier-copy." in duplicated.text

        duplicate = api.state.element_library.get("cashier-copy")
        deleted = client.post(
            "/ui/elements/cashier-copy/delete",
            data={
                "expected_hash": element_content_hash(duplicate),
                "confirm": "yes",
            },
            follow_redirects=True,
        )
        assert "Deleted cashier-copy." in deleted.text
