"""Docker-free tests for the MongoDB adapter's configuration and hygiene."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pymongo")

import culture_rules.store.mongo as mongo_module  # noqa: E402
from culture_rules.store.mongo import (  # noqa: E402
    ConfigError,
    MongoConfig,
    MongoStore,
    build_client_kwargs,
)
from culture_rules.store.port import StoreError  # noqa: E402
from tests.store import mongo_rig  # noqa: E402


@pytest.fixture
def certs(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    mongo_rig._make_certs(tmp_path)
    return tmp_path


URI = "mongodb://app:secret@127.0.0.1:27999/?replicaSet=rs0&authSource=culture_rules"


def test_config_error_is_store_error():
    assert issubclass(ConfigError, StoreError)


def test_uri_comes_from_env_only_and_there_is_no_default():
    with pytest.raises(ConfigError, match="CULTURE_RULES_MONGO_URI"):
        MongoConfig.from_env({})
    source = Path(mongo_module.__file__).read_text()
    assert "27017" not in source  # spark's 27017 belongs to unrelated containers


def test_from_env_reads_every_setting():
    cfg = MongoConfig.from_env(
        {
            "CULTURE_RULES_MONGO_URI": URI,
            "CULTURE_RULES_MONGO_DB": "mydb",
            "CULTURE_RULES_MONGO_TLS_CA_FILE": "/etc/ca.pem",
            "CULTURE_RULES_MONGO_TLS_CERT_KEY_FILE": "/etc/client.pem",
        }
    )
    assert cfg.uri == URI
    assert cfg.database == "mydb"
    assert cfg.tls_ca_file == "/etc/ca.pem"
    assert cfg.tls_cert_key_file == "/etc/client.pem"
    assert MongoConfig.from_env({"CULTURE_RULES_MONGO_URI": URI}).database == "culture_rules"


def test_client_options_require_tls_with_configurable_ca_and_client_cert():
    cfg = MongoConfig(URI, tls_ca_file="/etc/ca.pem", tls_cert_key_file="/etc/client.pem")
    kwargs = build_client_kwargs(cfg)
    assert kwargs["tls"] is True
    assert kwargs["tlsCAFile"] == "/etc/ca.pem"
    assert kwargs["tlsCertificateKeyFile"] == "/etc/client.pem"
    assert "tlsAllowInvalidCertificates" not in kwargs
    assert "tlsInsecure" not in kwargs
    assert "tlsCAFile" not in build_client_kwargs(MongoConfig(URI))  # system trust store


def test_client_options_force_majority_writes_and_reads():
    kwargs = build_client_kwargs(MongoConfig(URI))
    assert kwargs["w"] == "majority"
    assert kwargs["readConcernLevel"] == "majority"
    assert kwargs["retryWrites"] is True


def test_real_client_object_carries_majority_and_tls(certs):
    cfg = MongoConfig(URI, tls_ca_file=str(certs / "ca.pem"))
    store = MongoStore(cfg, connect=False)  # connects lazily: no server needed
    try:
        assert store.client.write_concern.document == {"w": "majority"}
        assert store.client.read_concern.level == "majority"
        assert store.client.options.pool_options._ssl_context is not None  # TLS is on
    finally:
        store.close()


def test_uri_without_credentials_is_refused():
    with pytest.raises(ConfigError, match="authentication"):
        MongoStore(MongoConfig("mongodb://127.0.0.1:27999/?replicaSet=rs0"), connect=False)


def test_uri_disabling_tls_is_refused():
    with pytest.raises(ConfigError, match="TLS"):
        MongoStore(MongoConfig(URI + "&tls=false"), connect=False)


def test_x509_auth_counts_as_authentication(certs):
    uri = "mongodb://127.0.0.1:27999/?authMechanism=MONGODB-X509&authSource=$external"
    cfg = MongoConfig(uri, tls_cert_key_file=str(certs / "server.pem"))
    MongoStore(cfg, connect=False).close()


def test_mongo_import_is_lazy_and_error_names_the_extra():
    code = (
        "import sys; sys.modules['pymongo'] = None\n"  # simulate the extra not installed
        "import culture_rules.store.mongo as m\n"  # importing must not need pymongo
        "try:\n"
        "    m.MongoStore(m.MongoConfig('mongodb://u:p@h/?replicaSet=r'), connect=False)\n"
        "except m.ConfigError as exc:\n"
        "    print(exc)\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert "culture-rules[store]" in out.stdout


def test_module_never_opens_files_for_writing():
    tree = ast.parse(Path(mongo_module.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            assert name not in {"open", "write_text", "write_bytes", "mkdir", "makedirs"}, name
