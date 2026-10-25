"""Explicit approval of synthetic knowledge used by existing offline scenarios."""


def reviewed_knowledge(store, *args, **kwargs):
    document = store.upsert_knowledge(*args, **kwargs)
    return store.approve_knowledge(document["id"], expected_updated_at=document["updated_at"])
