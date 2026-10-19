from tests.conftest import grant, upload
from tests.fakes import make_pdf

PDF = make_pdf([["Article 1", "Champ d application de la directive sur les marches financiers."]])


def test_member_sees_only_granted_or_open_collections(admin, member, member_user, collection):
    assert member.get("/collections").json() == []
    # Hidden collections answer 404, not 403, so their existence does not leak.
    assert member.get(f"/collections/{collection['id']}/documents").status_code == 404

    grant(admin, collection["id"], member_user.id, "viewer")
    listed = member.get("/collections").json()
    assert [c["access"] for c in listed] == ["viewer"]

    admin.post("/collections", json={"name": "Procedures internes", "is_open": True})
    names = {c["name"] for c in member.get("/collections").json()}
    assert names == {"Reglementation UE", "Procedures internes"}


def test_viewer_cannot_upload_or_delete_editor_can(admin, member, member_user, collection):
    grant(admin, collection["id"], member_user.id, "viewer")
    assert upload(member, collection["id"], "a.pdf", PDF).status_code == 403

    grant(admin, collection["id"], member_user.id, "editor")
    resp = upload(member, collection["id"], "a.pdf", PDF)
    assert resp.status_code == 202, resp.text
    assert member.delete(f"/documents/{resp.json()['id']}").status_code == 200


def test_only_admins_manage_collections_users_and_audit(member, collection):
    assert member.post("/collections", json={"name": "x"}).status_code == 403
    assert member.delete(f"/collections/{collection['id']}").status_code == 403
    assert member.get("/admin/users").status_code == 403
    assert member.get("/admin/audit").status_code == 403
    assert member.get("/admin/system").status_code == 403


def test_revoking_grant_removes_access(admin, member, member_user, collection):
    grant(admin, collection["id"], member_user.id, "viewer")
    assert admin.delete(f"/admin/collections/{collection['id']}/grants/{member_user.id}").status_code == 200
    assert member.get("/collections").json() == []


def test_ask_cannot_target_inaccessible_collection(member, collection):
    resp = member.post("/chat/ask", json={"question": "test", "collection_ids": [collection["id"]]})
    assert resp.status_code == 404


def test_ask_without_any_collection_is_explained(member):
    resp = member.post("/chat/ask", json={"question": "test"})
    assert resp.status_code == 400
    assert "Aucune collection" in resp.json()["detail"]


def test_last_admin_cannot_be_demoted_or_deleted(admin, admin_user, member_user):
    assert admin.patch(f"/admin/users/{admin_user.id}", json={"role": "member"}).status_code == 409
    assert admin.delete(f"/admin/users/{admin_user.id}").status_code == 409


def test_conversations_are_private(admin, member, member_user, collection):
    grant(admin, collection["id"], member_user.id, "viewer")
    conversation_id = admin.post("/chat/ask", json={"question": "Bonjour"}).json()["conversation_id"]
    assert member.get(f"/conversations/{conversation_id}").status_code == 404
    assert member.delete(f"/conversations/{conversation_id}").status_code == 404
