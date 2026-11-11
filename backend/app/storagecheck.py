"""Check the evidence storage (Azure Blob Storage or local folder).   python -m app.storagecheck"""
import sys
import uuid

from . import storage


def main() -> int:
    print(f"Storage backend: {storage.backend_name()}")
    if storage.use_blob():
        print(f"Container: {storage.container_name()}")
    name = f"csv/batch0_{uuid.uuid4().hex[:12]}.csv"
    try:
        ref = storage.save(name, "recorded_at,temperature\n2026-01-01T00:00:00Z,5.0\n", {"purpose": "storagecheck"})
        ok = storage.read(ref).startswith(b"recorded_at")
        storage.delete(ref)
    except storage.StorageUnavailable as e:
        print("FAILED:", e)
        return 1
    print("Write / read / delete test file:", "OK" if ok else "READ-BACK MISMATCH")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
