"""Evidence-file storage (uploaded temperature CSVs).

  * Azure Blob Storage - used when AZURE_STORAGE_CONNECTION_STRING is set. The container is PRIVATE; files are only
    ever served through the authenticated API (so role checks and the audit log always apply).
  * Local folder       - fallback (MEDTRACE_STORAGE, default ./storage) for quick local runs and tests.

The value kept in temperature_logs.file_reference tells which one holds a file:
    blob://<container>/csv/batch12_ab34cd56ef78.csv      (Azure Blob)
    csv/batch12_ab34cd56ef78.csv                         (local folder)
"""
import os
import re
from pathlib import Path

from . import config  # noqa: F401  (loads .env)
from .db import storage_dir

BLOB_PREFIX = "blob://"
NAME_RE = re.compile(r"^csv/batch(\d+)_([0-9a-f]{12})\.csv$")
_SDK_KW = {"retry_total": 2, "connection_timeout": 10}   # modest retries so a bad setting fails fast
_ready: set = set()  # (connection string, container) pairs already checked/created in this process


class StorageUnavailable(RuntimeError):
    """Human-readable reason why a file could not be stored or fetched."""


def use_blob() -> bool:
    return bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING"))


def backend_name() -> str:
    return "azure-blob" if use_blob() else "local"


def container_name() -> str:
    return os.environ.get("AZURE_STORAGE_CONTAINER", "medtrace-evidence")


def file_id(ref: str):
    """'blob://c/csv/batch12_ab34cd56ef78.csv' -> 'ab34cd56ef78' (None if the reference is not one of ours)."""
    m = NAME_RE.match(ref.split("/", 3)[-1] if ref.startswith(BLOB_PREFIX) else ref)
    return m.group(2) if m else None


def _friendly(e: Exception) -> str:
    msg = str(e)
    if "AuthenticationFailed" in msg or "AuthorizationFailure" in msg or "403" in msg:
        return ("Azure Storage refused the credentials or this network. Check AZURE_STORAGE_CONNECTION_STRING and the "
                "storage account's Networking / firewall settings (docs/AZURE_SETUP.md, Part B).")
    if "Name or service not known" in msg or "getaddrinfo" in msg or "Max retries" in msg or "Failed to establish" in msg:
        return "Could not reach Azure Storage. Check the account name in the connection string and your internet connection."
    if "Connection string" in msg or "connection string" in msg or "AccountName" in msg:
        return "AZURE_STORAGE_CONNECTION_STRING is not valid. Copy it again from Storage account > Access keys."
    if "ContainerNotFound" in msg:
        return "The storage container does not exist yet."
    return "Azure Storage error: " + msg[:250]


def _service():
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        raise StorageUnavailable("Package 'azure-storage-blob' is not installed. Run: pip install -r backend/requirements.txt")
    try:
        return BlobServiceClient.from_connection_string(os.environ["AZURE_STORAGE_CONNECTION_STRING"], **_SDK_KW)
    except Exception as e:
        raise StorageUnavailable(_friendly(e))


def _container():
    """Return the (private) container client, creating the container the first time."""
    svc, name = _service(), container_name()
    client = svc.get_container_client(name)
    key = (os.environ["AZURE_STORAGE_CONNECTION_STRING"], name)
    if key not in _ready:
        from azure.core.exceptions import ResourceExistsError
        try:
            client.create_container()          # no public_access argument = private
        except ResourceExistsError:
            pass
        except Exception as e:
            raise StorageUnavailable(_friendly(e))
        _ready.add(key)
    return client


def save(name: str, text: str, metadata: dict | None = None) -> str:
    """Store a CSV and return its file_reference. `name` must be a server-generated csv/batch<id>_<hex>.csv."""
    if not NAME_RE.match(name):
        raise ValueError("unexpected evidence file name")
    data = text.encode("utf-8")
    if use_blob():
        from azure.storage.blob import ContentSettings
        try:
            _container().upload_blob(name, data, overwrite=False, metadata={k: str(v) for k, v in (metadata or {}).items()},
                                     content_settings=ContentSettings(content_type="text/csv; charset=utf-8"))
        except StorageUnavailable:
            raise
        except Exception as e:
            raise StorageUnavailable(_friendly(e))
        return f"{BLOB_PREFIX}{container_name()}/{name}"
    path = storage_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return name


def read(ref: str) -> bytes:
    if ref.startswith(BLOB_PREFIX):
        container, _, name = ref[len(BLOB_PREFIX):].partition("/")
        if not use_blob():
            raise StorageUnavailable("This file is in Azure Blob Storage but AZURE_STORAGE_CONNECTION_STRING is not set.")
        if not NAME_RE.match(name):
            raise StorageUnavailable("Invalid file reference.")
        try:
            return _service().get_blob_client(container, name).download_blob().readall()
        except StorageUnavailable:
            raise
        except Exception as e:
            if "BlobNotFound" in str(e) or "ResourceNotFound" in str(e):
                raise StorageUnavailable("The evidence file is missing from Azure Blob Storage.")
            raise StorageUnavailable(_friendly(e))
    if not NAME_RE.match(ref):
        raise StorageUnavailable("Invalid file reference.")
    base = storage_dir().resolve()
    path = (base / ref).resolve()
    if base not in path.parents or not path.is_file():            # blocks path traversal; file may have been removed
        raise StorageUnavailable("The evidence file was not found on this server.")
    return path.read_bytes()


def delete(ref: str) -> None:
    """Best-effort clean-up (used when the database write fails after the file was stored)."""
    try:
        if ref.startswith(BLOB_PREFIX) and use_blob():
            container, _, name = ref[len(BLOB_PREFIX):].partition("/")
            _service().get_blob_client(container, name).delete_blob()
        elif not ref.startswith(BLOB_PREFIX) and NAME_RE.match(ref):
            (storage_dir() / ref).unlink(missing_ok=True)
    except Exception:
        pass
