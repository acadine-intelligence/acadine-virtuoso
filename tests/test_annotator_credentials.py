"""Issue #68: annotator credentials resolved by name, never stored.

Every value here is synthetic. Keychain tools are replaced by fake scripts on
PATH, so no test can read a real keychain entry. Credential names are
synthetic too, so a real `security` or `secret-tool` would find nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from conftest import downgrade_credentials_to_v17
from virtuoso import credentials as credentials_module
from virtuoso.annotations import AnnotationError, AnnotationService
from virtuoso.modules import ModuleError, ModuleManifest
from virtuoso.workspace import WorkspaceService

KEY_NAME = "VIRTUOSO_TEST_KEY"
HOST_VALUE = "test-key-not-real-7f3a-host"
ENV_VALUE = "test-key-not-real-7f3a-env"
KEYCHAIN_VALUE = "test-key-not-real-7f3a-keychain"
ALL_VALUES = (HOST_VALUE, ENV_VALUE, KEYCHAIN_VALUE, "test-key-not-real-7f3a")
BASE_MODULE_ENV = {"PATH", "LANG", "LC_ALL", "PYTHONIOENCODING"}
# macOS may add this to every new process; it carries no workspace data.
PLATFORM_ENV = {"__CF_USER_TEXT_ENCODING"}

# Records which environment it received (names, plus a hash of the declared
# credential) and answers every question. It never prints the value.
_CREDENTIAL_ANNOTATOR = '''
import hashlib, json, os, sys
from pathlib import Path
request = json.load(sys.stdin)
name = sys.argv[2]
value = os.environ.get(name)
Path(sys.argv[1]).write_text(json.dumps({
    "env_keys": sorted(os.environ),
    "value_sha256": hashlib.sha256(value.encode()).hexdigest() if value else None,
}))
sys.stderr.write("debug: %s\\n" % value)
projection = request["projections"]["annotation.request"]
answers = {}
for question in projection["questions"]:
    if question["primitive"] == "noul":
        answer = 0.5
    elif question["primitive"] == "choice":
        answer = question["options"][0]
    else:
        answer = 3
    answers[question["id"]] = {
        "primitive": question["primitive"], "value": answer, "confidence": 0.5,
    }
json.dump({
    "schema": "virtuoso/module-result@0.1",
    "module_id": sys.argv[3],
    "kind": "annotation-answers",
    "payload": {"answers": answers},
}, sys.stdout)
'''

# A misbehaving module that echoes the key into an answer label.
_ECHOING_ANNOTATOR = '''
import json, os, sys
request = json.load(sys.stdin)
value = os.environ[sys.argv[2]]
answers = {}
for question in request["projections"]["annotation.request"]["questions"]:
    if question["primitive"] == "choice":
        answer = value
    else:
        answer = 0.5
    answers[question["id"]] = {
        "primitive": question["primitive"], "value": answer, "confidence": 0.5,
    }
json.dump({
    "schema": "virtuoso/module-result@0.1",
    "module_id": sys.argv[3],
    "kind": "annotation-answers",
    "payload": {"answers": answers},
}, sys.stdout)
'''

_FAKE_KEYCHAIN = '''#!{python}
import json, sys
from pathlib import Path
log = Path({log!r})
store = Path({store!r})
argv = sys.argv[1:]
with log.open("a") as handle:
    handle.write(json.dumps([{tool!r}] + argv) + "\\n")
if {tool!r} == "security":
    expected_prefix = ["find-generic-password", "-s", "virtuoso", "-a"]
    ok = len(argv) == 6 and argv[:4] == expected_prefix and argv[5] == "-w"
    account = argv[4] if ok else None
else:
    ok = len(argv) == 5 and argv[:4] == ["lookup", "service", "virtuoso", "account"]
    account = argv[4] if ok else None
entries = json.loads(store.read_text()) if store.exists() else {{}}
if account in entries:
    sys.stdout.write(entries[account] + "\\n")
    raise SystemExit(0)
sys.stderr.write("The specified item could not be found in the keychain.\\n")
raise SystemExit(44)
'''


class CredentialFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / "learner"
        self.workspace = WorkspaceService.init(self.root)
        self.capture = base / "capture.json"
        self.bin = base / "bin"
        self.bin.mkdir()
        self.keychain_log = base / "keychain.log"
        self.keychain_store = base / "keychain.json"
        for tool in ("security", "secret-tool"):
            path = self.bin / tool
            path.write_text(
                _FAKE_KEYCHAIN.format(
                    python=sys.executable,
                    log=str(self.keychain_log),
                    store=str(self.keychain_store),
                    tool=tool,
                )
            )
            path.chmod(0o700)
        self.workspace.add_item(
            item_id="testing-effect",
            title="Explain the testing effect",
            focus="learning-science",
            prompt="Why does retrieval improve recall?",
            answer="Retrieval strengthens later access.",
            hint="Think about what practice does to memory.",
        )
        # The test process environment must not carry the synthetic name.
        self.assertNotIn(KEY_NAME, os.environ)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- helpers -----------------------------------------------------------

    def _install(
        self,
        *,
        module_id: str = "remote-judge",
        network: str = "remote",
        credentials: Any = "default",
        script: str = _CREDENTIAL_ANNOTATOR,
    ) -> Path:
        module_dir = self.root / "modules" / module_id
        module_dir.mkdir(parents=True, mode=0o700)
        module_dir.parent.chmod(0o700)
        script_path = module_dir / "annotator.py"
        script_path.write_text(script)
        script_path.chmod(0o700)
        manifest: dict[str, Any] = {
            "schema": "virtuoso/module@0.1",
            "id": module_id,
            "version": "1.0.0",
            "category": "annotator",
            "network": network,
            "command": {
                "argv": [
                    sys.executable, str(script_path), str(self.capture), KEY_NAME, module_id,
                ],
                "timeout_seconds": 5,
            },
            "capabilities": {
                "reads": ["annotation.request"],
                "returns": "annotation-answers",
            },
            "trust": "local-executable",
        }
        if credentials == "default":
            credentials = [
                {"name": KEY_NAME, "provider": "typesafe", "sources": ["host", "env", "keychain"]}
            ]
        if credentials is not None:
            manifest["credentials"] = credentials
        path = module_dir / "virtuoso.module.json"
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        path.chmod(0o600)
        return path

    def _store_keychain(self, value: str) -> None:
        self.keychain_store.write_text(json.dumps({KEY_NAME: value}))

    def _env(self, **extra: str) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key != KEY_NAME
        }
        env["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        env.update(extra)
        return env

    def _cli(
        self, *args: str, env: dict[str, str] | None = None, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "virtuoso.cli", "--workspace", str(self.root), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env if env is not None else self._env(),
            input=stdin if stdin is not None else "",
            timeout=60,
        )

    def _snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }

    def _captured(self) -> dict[str, Any]:
        return json.loads(self.capture.read_text())

    def _keychain_calls(self) -> list[list[str]]:
        if not self.keychain_log.exists():
            return []
        return [json.loads(line) for line in self.keychain_log.read_text().splitlines()]

    def _assert_no_value(self, text: str | bytes, label: str) -> None:
        data = text.encode("utf-8") if isinstance(text, str) else text
        for value in ALL_VALUES:
            self.assertNotIn(value.encode("utf-8"), data, f"{label} contains a key value")

    @staticmethod
    def _sha(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ManifestCredentialValidationTests(CredentialFixture):
    def _load_with(self, credentials: Any, *, network: str = "remote") -> ModuleManifest:
        path = self._install(credentials=credentials, network=network)
        return ModuleManifest.load(path)

    def test_valid_declaration_loads_by_name_only(self) -> None:
        manifest = self._load_with(
            [{"name": "TYPESAFE_API_KEY", "provider": "typesafe", "sources": ["env", "host"]}]
        )
        self.assertEqual(len(manifest.credentials), 1)
        spec = manifest.credentials[0]
        self.assertEqual(spec.name, "TYPESAFE_API_KEY")
        self.assertEqual(spec.provider, "typesafe")
        # Precedence is fixed by Virtuoso, not by manifest order.
        self.assertEqual(spec.sources, ("host", "env"))

    def test_manifest_without_credentials_still_loads(self) -> None:
        manifest = self._load_with(None)
        self.assertEqual(manifest.credentials, ())

    def test_invalid_declarations_fail_closed(self) -> None:
        good = {"name": KEY_NAME, "provider": "typesafe", "sources": ["env"]}
        cases: dict[str, Any] = {
            "not a list": {"name": KEY_NAME},
            "empty list": [],
            "two credentials": [good, {**good, "name": "OTHER_KEY"}],
            "entry not object": ["KEY"],
            "unknown entry key": [{**good, "value": "x"}],
            "missing provider": [{"name": KEY_NAME, "sources": ["env"]}],
            "lowercase name": [{**good, "name": "typesafe_api_key"}],
            "leading digit": [{**good, "name": "1KEY"}],
            "too long name": [{**good, "name": "K" * 65}],
            "name with dash": [{**good, "name": "API-KEY"}],
            "reserved PATH": [{**good, "name": "PATH"}],
            "reserved loader": [{**good, "name": "LD_PRELOAD"}],
            "reserved dyld": [{**good, "name": "DYLD_INSERT_LIBRARIES"}],
            "reserved python": [{**good, "name": "PYTHONSTARTUP"}],
            "bad provider": [{**good, "provider": "Type Safe"}],
            "unknown source": [{**good, "sources": ["env", "file"]}],
            "duplicate source": [{**good, "sources": ["env", "env"]}],
            "empty sources": [{**good, "sources": []}],
            "sources not list": [{**good, "sources": "env"}],
        }
        for index, (label, credentials) in enumerate(cases.items()):
            with self.subTest(label):
                path = self._install(module_id=f"bad-{index}", credentials=credentials)
                with self.assertRaises(ModuleError):
                    ModuleManifest.load(path)

    def test_only_remote_annotators_may_declare_credentials(self) -> None:
        path = self._install(network="none")
        with self.assertRaisesRegex(ModuleError, "network: remote"):
            ModuleManifest.load(path)
        scheduler_dir = self.root / "modules" / "sched"
        scheduler_dir.mkdir()
        manifest = {
            "schema": "virtuoso/module@0.1",
            "id": "sched",
            "version": "1.0.0",
            "category": "scheduler",
            "command": {"argv": [sys.executable, "x.py"], "timeout_seconds": 2},
            "capabilities": {"reads": ["scheduler.request"], "returns": "scheduler-proposal"},
            "trust": "local-executable",
            "credentials": [{"name": KEY_NAME, "provider": "typesafe", "sources": ["env"]}],
        }
        (scheduler_dir / "virtuoso.module.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ModuleError, "credentials"):
            ModuleManifest.load(scheduler_dir / "virtuoso.module.json")


class CredentialResolutionTests(CredentialFixture):
    def setUp(self) -> None:
        super().setUp()
        self.workspace.configure_annotator(allow_remote=True)

    def test_no_resolvable_credential_exits_2_and_writes_nothing(self) -> None:
        self._install()
        before = self._snapshot()
        result = self._cli("annotate", "item", "--item", "testing-effect",
                           "--module", "remote-judge", "--json")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn(KEY_NAME, result.stderr)
        self.assertIn("typesafe", result.stderr)
        self.assertFalse(self.capture.exists(), "module must not start without a credential")
        self.assertEqual(self._snapshot(), before)

    def test_env_source_reaches_only_the_module_environment(self) -> None:
        self._install()
        result = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "remote-judge", "--json",
            env=self._env(**{KEY_NAME: ENV_VALUE}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["credential_source"], "env")
        self.assertEqual(record["credential_provider"], "typesafe")
        captured = self._captured()
        self.assertEqual(captured["value_sha256"], self._sha(ENV_VALUE))
        self.assertEqual(
            set(captured["env_keys"]) - PLATFORM_ENV, BASE_MODULE_ENV | {KEY_NAME}
        )
        self._assert_no_value(result.stdout + result.stderr, "annotate output")

    @unittest.skipUnless(
        sys.platform == "darwin" or sys.platform.startswith("linux"),
        "keychain lookup is implemented for macOS and Linux",
    )
    def test_keychain_source_uses_the_virtuoso_service_name(self) -> None:
        self._install()
        self._store_keychain(KEYCHAIN_VALUE)
        result = self._cli("annotate", "item", "--item", "testing-effect",
                           "--module", "remote-judge", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["credential_source"], "keychain")
        self.assertEqual(self._captured()["value_sha256"], self._sha(KEYCHAIN_VALUE))
        calls = self._keychain_calls()
        self.assertEqual(len(calls), 1)
        if sys.platform == "darwin":
            self.assertEqual(
                calls[0],
                ["security", "find-generic-password", "-s", "virtuoso", "-a", KEY_NAME, "-w"],
            )
        else:
            self.assertEqual(
                calls[0], ["secret-tool", "lookup", "service", "virtuoso", "account", KEY_NAME]
            )
        self._assert_no_value(result.stdout + result.stderr, "annotate output")

    def test_host_source_reads_one_value_from_stdin(self) -> None:
        self._install()
        result = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
            "--host-credential-stdin", "--json",
            stdin=HOST_VALUE + "\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["credential_source"], "host")
        self.assertEqual(self._captured()["value_sha256"], self._sha(HOST_VALUE))
        self.assertEqual(self._keychain_calls(), [], "a host value must skip the keychain")
        self._assert_no_value(result.stdout + result.stderr, "annotate output")

    def test_precedence_is_host_then_env_then_keychain(self) -> None:
        self._install()
        self._store_keychain(KEYCHAIN_VALUE)
        env = self._env(**{KEY_NAME: ENV_VALUE})
        args = ("annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
                "--json")
        host = self._cli(*args, "--host-credential-stdin", env=env, stdin=HOST_VALUE)
        self.assertEqual(host.returncode, 0, host.stderr)
        self.assertEqual(json.loads(host.stdout)["credential_source"], "host")
        self.assertEqual(self._captured()["value_sha256"], self._sha(HOST_VALUE))

        env_run = self._cli(*args, env=env)
        self.assertEqual(env_run.returncode, 0, env_run.stderr)
        self.assertEqual(json.loads(env_run.stdout)["credential_source"], "env")
        self.assertEqual(self._captured()["value_sha256"], self._sha(ENV_VALUE))
        self.assertEqual(self._keychain_calls(), [], "env must win before the keychain")

        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            keychain = self._cli(*args)
            self.assertEqual(keychain.returncode, 0, keychain.stderr)
            self.assertEqual(json.loads(keychain.stdout)["credential_source"], "keychain")
            self.assertEqual(self._captured()["value_sha256"], self._sha(KEYCHAIN_VALUE))

    def test_manifest_sources_limit_which_sources_are_tried(self) -> None:
        self._install(
            credentials=[{"name": KEY_NAME, "provider": "typesafe", "sources": ["keychain"]}]
        )
        before = self._snapshot()
        env = self._env(**{KEY_NAME: ENV_VALUE})
        result = self._cli("annotate", "item", "--item", "testing-effect",
                           "--module", "remote-judge", env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.capture.exists())
        self.assertEqual(self._snapshot(), before)
        refused_host = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
            "--host-credential-stdin", env=env, stdin=HOST_VALUE,
        )
        self.assertEqual(refused_host.returncode, 2)
        self.assertIn("host", refused_host.stderr)
        self.assertFalse(self.capture.exists())
        self.assertEqual(self._snapshot(), before)
        self._assert_no_value(result.stderr + refused_host.stderr, "error output")

    def test_empty_host_value_fails_closed_without_falling_back(self) -> None:
        self._install()
        before = self._snapshot()
        env = self._env(**{KEY_NAME: ENV_VALUE})
        result = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
            "--host-credential-stdin", env=env, stdin="\n",
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.capture.exists())
        self.assertEqual(self._snapshot(), before)

    def test_host_value_from_a_terminal_is_refused(self) -> None:
        self._install()
        from virtuoso import cli

        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        stderr = io.StringIO()
        with mock.patch.object(cli.sys, "stdin", fake_stdin), \
                mock.patch.dict(os.environ, {"PATH": self._env()["PATH"]}), \
                contextlib.redirect_stderr(stderr):
            code = cli.main([
                "--workspace", str(self.root), "annotate", "item", "--item", "testing-effect",
                "--module", "remote-judge", "--host-credential-stdin",
            ])
        self.assertEqual(code, 2)
        self.assertIn("not typed at a terminal", stderr.getvalue())
        fake_stdin.buffer.read.assert_not_called()
        self.assertFalse(self.capture.exists())

    def test_consent_is_checked_before_any_source(self) -> None:
        self._install()
        self._store_keychain(KEYCHAIN_VALUE)
        self.workspace.configure_annotator(allow_remote=False)
        before = self._snapshot()
        for extra_args, stdin in (((), None), (("--host-credential-stdin",), HOST_VALUE)):
            with self.subTest(extra_args=extra_args):
                result = self._cli(
                    "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
                    *extra_args, env=self._env(**{KEY_NAME: ENV_VALUE}), stdin=stdin,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("allow-remote", result.stderr)
                self.assertFalse(self.capture.exists())
                self.assertEqual(self._keychain_calls(), [])
                self.assertEqual(self._snapshot(), before)
                self._assert_no_value(result.stderr, "consent error")

    def test_service_reads_host_value_only_after_consent(self) -> None:
        self._install()
        self.workspace.configure_annotator(allow_remote=False)
        reader = mock.Mock(return_value=HOST_VALUE)
        with self.assertRaisesRegex(AnnotationError, "allow-remote"):
            AnnotationService(self.workspace).annotate_item(
                item_id="testing-effect", module_id="remote-judge", host_credential=reader
            )
        reader.assert_not_called()

    def test_module_echoing_the_key_cannot_leak_it_through_errors(self) -> None:
        self._install(script=_ECHOING_ANNOTATOR)
        before = self._snapshot()
        result = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
            env=self._env(**{KEY_NAME: ENV_VALUE}),
        )
        self.assertEqual(result.returncode, 2)
        self._assert_no_value(result.stdout + result.stderr, "error output")
        self.assertIn("[redacted]", result.stderr)
        self.assertEqual(self._snapshot(), before)

    def test_no_value_reaches_workspace_files_or_read_surfaces(self) -> None:
        self._install()
        self._store_keychain(KEYCHAIN_VALUE)
        env = self._env(**{KEY_NAME: ENV_VALUE})
        outputs: list[str] = []
        for extra_args, stdin in (((), None), (("--host-credential-stdin",), HOST_VALUE)):
            run = self._cli(
                "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
                "--json", *extra_args, env=env, stdin=stdin,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            outputs.extend((run.stdout, run.stderr))
        export_dir = Path(self.tmp.name) / "export"
        for args in (
            ("annotate", "list", "--json"),
            ("annotate", "list"),
            ("attempts", "--json"),
            ("queries", "history", "--item", "testing-effect", "--json"),
            ("doctor", "--json"),
            ("annotate", "doctor", "--json"),
            ("annotate", "doctor"),
            ("export", "obsidian", "--out", str(export_dir), "--json"),
        ):
            result = self._cli(*args, env=env)
            self.assertEqual(result.returncode, 0, (args, result.stderr))
            outputs.extend((result.stdout, result.stderr))
        for index, text in enumerate(outputs):
            self._assert_no_value(text, f"command output {index}")
        for relative, data in self._snapshot().items():
            self._assert_no_value(data, f"workspace file {relative}")
        for path in export_dir.rglob("*"):
            if path.is_file():
                self._assert_no_value(path.read_bytes(), f"export file {path.name}")
        receipts = WorkspaceService.open(self.root).list_module_receipts()
        self._assert_no_value(json.dumps(receipts), "module receipts")
        annotated = [r for r in receipts if r["category"] == "annotator"]
        self.assertEqual(
            [(r["credential_source"], r["credential_provider"]) for r in annotated],
            [("env", "typesafe"), ("host", "typesafe")],
        )
        with sqlite3.connect(self.workspace.db_path) as db:
            rows = db.execute(
                "SELECT credential_source, credential_provider FROM annotations "
                "ORDER BY occurred_at"
            ).fetchall()
        self.assertEqual(rows, [("env", "typesafe"), ("host", "typesafe")])

    def test_local_annotators_are_unaffected(self) -> None:
        self._install(module_id="local-judge", network="none", credentials=None)
        self._store_keychain(KEYCHAIN_VALUE)
        self.workspace.configure_annotator(allow_remote=False)
        result = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "local-judge", "--json",
            env=self._env(**{KEY_NAME: ENV_VALUE}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertIsNone(record["credential_source"])
        self.assertIsNone(record["credential_provider"])
        captured = self._captured()
        self.assertIsNone(captured["value_sha256"])
        self.assertEqual(set(captured["env_keys"]) - PLATFORM_ENV, BASE_MODULE_ENV)
        self.assertEqual(self._keychain_calls(), [])
        refused = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "local-judge",
            "--host-credential-stdin", stdin=HOST_VALUE,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("declares no credentials", refused.stderr)

    def test_annotation_rows_reject_updates_to_credential_fields(self) -> None:
        self._install()
        result = self._cli(
            "annotate", "item", "--item", "testing-effect", "--module", "remote-judge",
            env=self._env(**{KEY_NAME: ENV_VALUE}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(self.workspace.db_path) as db:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "append-only"):
                db.execute("UPDATE annotations SET credential_source = 'keychain'")
            receipt_id = db.execute("SELECT module_receipt_id FROM annotations").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO annotations SELECT 'annotation-x', subject_kind, subject_id, "
                    "subject_hash, question_set_id, question_set_hash, module_id, "
                    "module_version, 'none', ?, answers_json, occurred_at, surface, "
                    "created_at, credential_provider, credential_source FROM annotations",
                    (receipt_id,),
                )


class KeychainLookupTests(unittest.TestCase):
    def test_missing_tool_does_not_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(
                credentials_module.keychain_lookup(KEY_NAME, environ={"PATH": empty})
            )

    def test_unsupported_platform_does_not_resolve(self) -> None:
        with mock.patch.object(credentials_module.sys, "platform", "win32"):
            self.assertIsNone(credentials_module.keychain_lookup(KEY_NAME, environ={}))

    def test_resolved_credential_repr_hides_the_value(self) -> None:
        resolved = credentials_module.ResolvedCredential(
            name=KEY_NAME, provider="typesafe", source="env", value=ENV_VALUE
        )
        self.assertNotIn(ENV_VALUE, repr(resolved))
        self.assertNotIn(ENV_VALUE, str(resolved))


class AnnotatorDoctorTests(CredentialFixture):
    def test_doctor_reports_sources_per_remote_annotator_without_values(self) -> None:
        self._install()
        self._install(module_id="local-judge", network="none", credentials=None)
        before = self._snapshot()
        result = self._cli("annotate", "doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema"], "virtuoso/annotator-doctor@0.1")
        self.assertFalse(report["allow_remote"])
        self.assertEqual([a["module_id"] for a in report["annotators"]], ["remote-judge"])
        judge = report["annotators"][0]
        self.assertFalse(judge["ready"])
        credential = judge["credentials"][0]
        self.assertEqual(credential["name"], KEY_NAME)
        self.assertEqual(credential["provider"], "typesafe")
        self.assertEqual(credential["sources"]["host"], "per-run")
        self.assertEqual(credential["sources"]["env"], "unresolved")
        self.assertIsNone(credential["local_source"])

        self._store_keychain(KEYCHAIN_VALUE)
        self.workspace.configure_annotator(allow_remote=True)
        before = self._snapshot()
        resolved = self._cli(
            "annotate", "doctor", "--json", env=self._env(**{KEY_NAME: ENV_VALUE})
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self._assert_no_value(resolved.stdout + resolved.stderr, "doctor output")
        report = json.loads(resolved.stdout)
        credential = report["annotators"][0]["credentials"][0]
        self.assertEqual(credential["sources"]["env"], "resolves")
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            self.assertEqual(credential["sources"]["keychain"], "resolves")
        self.assertEqual(credential["local_source"], "env")
        self.assertTrue(report["annotators"][0]["ready"])
        self.assertEqual(self._snapshot(), before, "doctor must not write")

    def test_doctor_reports_unreadable_manifests_without_running_them(self) -> None:
        bad = self._install(module_id="broken-judge")
        bad.write_text("{not json")
        bad.chmod(0o600)
        result = self._cli("annotate", "doctor", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["annotators"], [])
        self.assertEqual([e["module_id"] for e in report["errors"]], ["broken-judge"])
        self.assertFalse(self.capture.exists())


class CredentialMigrationTests(unittest.TestCase):
    def test_v17_workspace_upgrades_and_keeps_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "learner"
            WorkspaceService.init(root)
            db_path = root / ".virtuoso" / "state.sqlite3"
            with sqlite3.connect(db_path) as db:
                downgrade_credentials_to_v17(db)
                columns = {row[1] for row in db.execute("PRAGMA table_info(annotations)")}
                self.assertNotIn("credential_source", columns)
            reopened = WorkspaceService.open(root)
            with sqlite3.connect(reopened.db_path) as db:
                versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations")]
                annotation_columns = {
                    row[1] for row in db.execute("PRAGMA table_info(annotations)")
                }
                receipt_columns = {
                    row[1] for row in db.execute("PRAGMA table_info(module_run_receipts)")
                }
            self.assertEqual(versions[-1], 18)
            for columns in (annotation_columns, receipt_columns):
                self.assertTrue({"credential_source", "credential_provider"} <= columns)


if __name__ == "__main__":
    unittest.main()
