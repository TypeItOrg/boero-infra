#!/usr/bin/env python3
"""Execute the approved directed checks; keep credentials and raw test output private."""
import ast
from collections import Counter
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
REPOS = {"infra": ROOT, "api": Path(os.environ["API_REPO"]).resolve(),
         "ui": Path(os.environ["UI_REPO"]).resolve()}
RUN_ID = os.environ["ACCEPTANCE_RUN_ID"]
FINGERPRINTS = json.loads(os.environ["ACCEPTANCE_FINGERPRINTS"])
MANIFEST = json.loads((HERE / "requirements.json").read_text())
PROOFS = {}
COMMANDS = []
FAILURES = []
SECRETS = set()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path.resolve()


def redacted(text):
    for value in sorted(SECRETS, key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, "[redacted]")
    text = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted-jwt]", text)
    text = re.sub(r"([?&](?:token|code)=)[^\s\"'<>]+", r"\1[redacted]", text, flags=re.I)
    text = re.sub(r"(?i)((?:password|accessToken|refreshToken|authorization|cookie|secret)\s*[:=]\s*)[^\n,}]+",
                  r"\1[redacted]", text)
    return re.sub(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43,}(?![A-Za-z0-9_-])", "[redacted-opaque]", text)


def register_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and re.search(r"password|token|secret|encryption", key, re.I):
                SECRETS.add(item)
            register_secrets(item)
    elif isinstance(value, list):
        for item in value:
            register_secrets(item)


def run(name, argv, cwd, private, env=None, timeout=2400):
    print(f"CHECK {name}", flush=True)
    raw = private / (name + ".log")
    start = time.time()
    with raw.open("w") as stream:
        raw.chmod(0o600)
        try:
            result = subprocess.run(argv, cwd=cwd, env=env or os.environ.copy(),
                                    stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(str(error))
            code = 1
    record = {"check": name, "argv": list(map(str, argv)), "cwd": str(cwd),
              "exitCode": code, "durationSeconds": round(time.time() - start, 2)}
    COMMANDS.append(record)
    if code:
        FAILURES.append(name)
        # Raw API/XML/Jest output may contain credentials. It is never copied into artifacts.
        print(f"FAILED {name}: exit {code}; sanitized summaries will mark closure incomplete.", flush=True)
    return code, start


def proof(case, kind, tests, artifact):
    status = "passed" if tests and all(row["status"] == "passed" for row in tests) else "failed"
    PROOFS.setdefault(case, []).append({"kind": kind, "status": status, "tests": tests,
        "artifact": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()})


def unittest_module(path):
    spec = importlib.util.spec_from_file_location("acceptance_directed_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verifier_checks(out):
    module = unittest_module(ROOT / "tests/acceptance-verifier.test.py")
    statuses = []

    class Results(unittest.TextTestResult):
        def addSuccess(self, test):
            statuses.append({"name": test._testMethodName, "status": "passed"})
            super().addSuccess(test)

        def addFailure(self, test, error):
            statuses.append({"name": test._testMethodName, "status": "failed"})
            super().addFailure(test, error)

        addError = addFailure

        def addSkip(self, test, reason):
            statuses.append({"name": test._testMethodName, "status": "skipped"})
            super().addSkip(test, reason)

    result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(module.AcceptanceVerifierTest))
    report = write(out / "verifier-tests.json", {"runId": RUN_ID, "tests": statuses})
    if not result.wasSuccessful() or result.skipped:
        FAILURES.append("verifier-negative-tests")
    for case in ("V01.missing-skipped-failed", "V01.stale-evidence"):
        proof(case, "verifier-test", statuses, report)


def infra_checks(out, private):
    path = private / "infra-cases.json"
    code, _ = run("infra-directed", ["python3", "tests/qa-config.test.py", "--evidence", str(path)], ROOT, private)
    raw = json.loads(path.with_name("qa-config-report.json").read_text()) if path.exists() else {}
    statuses = [{"name": name, "status": status} for name, status in raw.get("tests", {}).items()]
    # Compose renders contain synthetic secrets. Preserve only command identity/exit codes.
    report = write(out / "infra-tests.json", {"runId": RUN_ID, "tests": statuses,
        "commands": [{k: v for k, v in row.items() if k in ("argv", "cwd", "exit_code")}
                     for row in raw.get("commands", [])],
        "boundary": raw.get("boundary", "No report")})
    tree = ast.parse((ROOT / "tests/qa-config.test.py").read_text())
    mapping = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "CASE_TESTS" for target in node.targets))
    for case, names in mapping.items():
        tests = [{"name": name, "status": raw.get("tests", {}).get(name, "missing")} for name in names]
        if code:
            tests.append({"name": "infra-directed process", "status": "failed"})
        proof(case, "configuration", tests, report)
    run("legacy-deploy-tests", ["sh", "tests/deploy-service.test.sh"], ROOT, private)


def api_checks(out, private):
    mapping = json.loads((HERE / "api-tests.json").read_text())
    all_tests = {}
    for task, key in (("fastTest", "fastTests"), ("integrationTest", "integrationTests")):
        code, started = run("api-" + task, mapping["commands"][key] + ["--rerun-tasks"], REPOS["api"], private)
        tests = []
        for path in (REPOS["api"] / "build/test-results" / task).glob("TEST-*.xml"):
            if path.stat().st_mtime < started:
                continue
            suite = ET.parse(path).getroot()
            for test in suite.iter("testcase"):
                status = "failed" if test.find("failure") is not None or test.find("error") is not None else (
                    "skipped" if test.find("skipped") is not None else "passed")
                tests.append({"className": test.get("classname", suite.get("name")), "name": test.get("name"),
                              "status": status, "durationSeconds": test.get("time", "0")})
        if not tests or any(test["status"] != "passed" for test in tests):
            FAILURES.append("api-" + task + "-report")
        report = write(out / ("api-" + task + ".json"), {"runId": RUN_ID, "task": task,
                         "tests": tests, "processExitCode": code,
                         "boundary": "actual PostgreSQL/Flyway/Redis" if task == "integrationTest" else "unit/http slices"})
        all_tests[task] = tests, report, code
    for case, expected in mapping["cases"].items():
        for task in sorted({row["task"] for row in expected}):
            actual, report, code = all_tests[task]
            selected = []
            for row in expected:
                if row["task"] != task:
                    continue
                matches = [test for test in actual if test["className"] == row["className"] and test["name"] == row["methodName"]]
                selected.extend({"name": test["className"] + "." + test["name"], "status": test["status"]} for test in matches)
                if not matches:
                    selected.append({"name": row["className"] + "." + row["methodName"], "status": "missing"})
            if code:
                selected.append({"name": task + " process", "status": "failed"})
            proof(case, "api-integration" if task == "integrationTest" else "api-unit", selected, report)
    run("api-static", mapping["commands"]["static"] + ["--rerun-tasks"], REPOS["api"], private)


def ui_checks(out, private):
    mapping = json.loads((HERE / "ui-tests.json").read_text())
    raw_path = private / "jest.json"
    code, _ = run("ui-jest", ["pnpm", "exec", "jest", "--runInBand", "--runTestsByPath", *mapping["testPaths"],
                             "--json", "--outputFile=" + str(raw_path)], REPOS["ui"], private)
    raw = json.loads(raw_path.read_text()) if raw_path.exists() else {}
    tests = [{"path": str(Path(suite["name"]).relative_to(REPOS["ui"])), "name": test["fullName"], "status": test["status"]}
             for suite in raw.get("testResults", []) for test in suite.get("assertionResults", [])]
    report = write(out / "ui-jest.json", {"runId": RUN_ID, "tests": tests,
                   "suites": raw.get("numPassedTestSuites"), "boundary": "unit/component mocks only"})
    if not tests or any(test["status"] != "passed" for test in tests) or not raw.get("success"):
        FAILURES.append("ui-jest-report")
    for case, expected in mapping["cases"].items():
        counts = Counter((row["path"], row["fullName"]) for row in expected)
        selected = []
        for (path, name), count in counts.items():
            matches = [test for test in tests if test["path"] == path and test["name"] == name]
            selected.extend({"name": path + ": " + name, "status": test["status"]} for test in matches)
            if len(matches) < count:
                selected.append({"name": path + ": " + name, "status": "missing"})
        if code:
            selected.append({"name": "Jest process", "status": "failed"})
        proof(case, "ui-unit", selected, report)
    run("ui-typecheck", ["pnpm", "exec", "tsc", "--noEmit"], REPOS["ui"], private)
    changed = subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=REPOS["ui"]).decode().splitlines()
    changed += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPOS["ui"]).decode().splitlines()
    sources = sorted({name for name in changed if name.endswith((".ts", ".tsx", ".mjs")) and (REPOS["ui"] / name).is_file()})
    if sources:
        run("ui-eslint", ["pnpm", "exec", "eslint", "--max-warnings=0", *sources], REPOS["ui"], private)
        run("ui-format", ["pnpm", "exec", "prettier", "--check", *sources], REPOS["ui"], private)
    for name, path in REPOS.items():
        run(name + "-diff", ["git", "diff", "--check"], path, private)


def capture_preservation():
    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args])
    repos = {}
    for name, path in REPOS.items():
        envs = {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in path.glob(".env*")
                if p.is_file() and not p.name.endswith((".example", ".md"))}
        repos[name] = {"head": git(path, "rev-parse", "HEAD").decode().strip(),
                       "branch": git(path, "branch", "--show-current").decode().strip(),
                       # git status/diff can refresh index stat-cache bytes without staging
                       # anything. Compare staged paths/modes/blob IDs, not that cache.
                       "index_entries_sha256": hashlib.sha256(git(path, "ls-files", "--stage", "-z")).hexdigest(),
                       "private_env_sha256": envs}
    docker = ["docker", "--context", "default"]
    ids = subprocess.check_output([*docker, "ps", "--no-trunc", "-aq"]).decode().split()
    containers = {}
    if ids:
        for item in json.loads(subprocess.check_output([*docker, "inspect", *ids])):
            containers[item["Name"].lstrip("/")] = {"id": item["Id"], "startedAt": item["State"]["StartedAt"],
                "running": item["State"]["Running"], "mounts": item["Mounts"]}
    volumes = subprocess.check_output([*docker, "volume", "ls", "--format", "{{.Name}}"]).decode().split()
    return {"repos": repos, "containers": containers, "volumes": volumes}


def preservation_checks(before, out):
    after = capture_preservation()
    comparisons = [("HEAD, branch, index and private environment unchanged", before["repos"] == after["repos"]),
                   ("All baseline container identities, mount sets and start states unchanged", all(
                       after["containers"].get(name) == value for name, value in before["containers"].items())),
                   ("No pre-existing Docker volume removed", set(before["volumes"]).issubset(after["volumes"]))]
    original = os.environ.get("ACCEPTANCE_PRESERVATION_BASELINE")
    if original:
        original_path = Path(original)
        repos = json.loads(original_path.read_text())
        containers = json.loads(original_path.with_name("docker-baseline.json").read_text())
        comparisons.append(("Original task HEAD/index/private environment preserved", all(
            all(after["repos"][name][field] == value[field] for field in ("head", "branch", "private_env_sha256"))
            and value.get("status") == "" and subprocess.run(["git", "diff", "--cached", "--quiet", "HEAD"],
                cwd=REPOS[name]).returncode == 0
            for name, value in ((key.removeprefix("boero-"), data) for key, data in repos.items()))))
        comparisons.append(("Original task Docker resources preserved", all(after["containers"].get(name) == value
            for name, value in containers["containers"].items()) and set(containers["volumes"]).issubset(after["volumes"])))
    paths = subprocess.check_output(["git", "ls-files", "src/main/resources/db/migration"], cwd=REPOS["api"]).decode().splitlines()
    unchanged = all((REPOS["api"] / name).is_file() and (REPOS["api"] / name).read_bytes() == subprocess.check_output(
        ["git", "show", "HEAD:" + name], cwd=REPOS["api"]) for name in paths)
    comparisons.append(("Every previously tracked Flyway migration byte-identical to HEAD", unchanged))
    tests = [{"name": name, "status": "passed" if passed else "failed"} for name, passed in comparisons]
    report = write(out / "preservation.json", {"runId": RUN_ID, "tests": tests,
                   "boundary": "No existing database contents were queried or changed"})
    proof("S01.preservation", "preservation", tests, report)
    if not all(passed for _, passed in comparisons):
        FAILURES.append("preservation")


def runtime_checks(out, private):
    # Keep recoverable ownership state outside the raw-log directory. If cleanup is
    # rejected or Docker becomes unavailable, deleting logs must not orphan resources.
    state_path = Path("/tmp") / ("boero-acceptance-state-" + RUN_ID + ".json")
    driver = ["python3", str(HERE / "runtime.py")]
    try:
        code, _ = run("runtime-start", [*driver, "start", "--state", str(state_path), "--artifacts", str(out / "runtime")], ROOT, private)
        if code:
            return
        state = json.loads(state_path.read_text())
        fixture_path = Path(state["fixturePath"])
        register_secrets(json.loads(fixture_path.read_text()))
        env = os.environ.copy() | {"ACCEPTANCE_FIXTURE": str(fixture_path), "ACCEPTANCE_ARTIFACTS": str(out)}
        run("browser-install", ["pnpm", "exec", "playwright", "install", "chromium"], REPOS["ui"], private, env)
        code, _ = run("browser-acceptance", ["pnpm", "test:acceptance"], REPOS["ui"], private, env)
        path = out / "playwright.json"
        raw = json.loads(path.read_text()) if path.is_file() else {}
        tests = raw.get("tests", [])
        if not tests or any(test.get("status") != "passed" for test in tests) or raw.get("status") != "passed":
            FAILURES.append("browser-report")
        for requirement, names in MANIFEST["requirements"].items():
            for name in names:
                case = requirement + "." + name
                selected = [{"name": test["title"], "status": test["status"]} for test in tests if "[" + case + "]" in test["title"]]
                if selected:
                    if code:
                        selected.append({"name": "Chromium process", "status": "failed"})
                    # These two Playwright cases exercise real backend HTTP without
                    # requesting its lazy Chromium fixture. Do not label them as DOM proof.
                    kind = "http-runtime" if case in ("A03.attempt-context", "A03.session-context") else "browser"
                    proof(case, kind, selected, path)
        case_path = out / "runtime-cases.json"
        code, _ = run("runtime-check", [*driver, "check", "--state", str(state_path), "--evidence", str(case_path)], ROOT, private)
        if case_path.is_file():
            for case, result in json.loads(case_path.read_text()).items():
                artifact = Path(result["artifact"])
                tests = [{"name": name, "status": result["status"]} for name in result.get("tests", [])]
                if code:
                    tests.append({"name": "Runtime check process", "status": "failed"})
                proof(case, "runtime", tests, artifact)
    finally:
        if state_path.exists():
            code, _ = run("runtime-cleanup", [*driver, "stop", "--state", str(state_path)], ROOT, private)
            write(out / "runtime-cleanup.json", {"statePath": str(state_path), "exitCode": code,
                  "boundary": "Only namespace/owner-labelled resources from this run"})


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_acceptance.py <evidence.json>")
    evidence = Path(sys.argv[1]).resolve()
    allowed = ROOT / "build/verification"
    if not evidence.is_relative_to(allowed):
        raise SystemExit("Evidence must be beneath ignored infra build/verification")
    out = evidence.parent / "runs" / RUN_ID
    out.mkdir(parents=True, exist_ok=False)
    # Even read-only inventory must not accidentally reach a remote Docker daemon.
    unittest_module(HERE / "runtime.py").require_local_daemon()
    before = capture_preservation()
    with tempfile.TemporaryDirectory(prefix="boero-acceptance-run-", dir="/tmp") as directory:
        private = Path(directory)
        private.chmod(0o700)
        try:
            verifier_checks(out)
            infra_checks(out, private)
            api_checks(out, private)
            ui_checks(out, private)
            # No disposable stack is launched if a directed prerequisite already failed.
            if not FAILURES:
                runtime_checks(out, private)
        except Exception as error:
            FAILURES.append("runner-exception")
            write(out / "runner-error.json", {"error": redacted(str(error)), "type": type(error).__name__})
        finally:
            try:
                preservation_checks(before, out)
            except Exception as error:
                FAILURES.append("preservation-exception")
                write(out / "preservation-error.json", {"error": redacted(str(error))})
    cases = {}
    for case, results in PROOFS.items():
        artifact = write(out / "cases" / (case + ".json"), {"runId": RUN_ID, "case": case,
            "fingerprints": FINGERPRINTS, "sources": MANIFEST.get("sourcePaths", {}).get(case.split(".")[0], []),
            "checks": results})
        cases[case] = {"status": "passed" if all(row["status"] == "passed" for row in results) else "failed",
            "artifact": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "kinds": sorted({row["kind"] for row in results}),
            "tests": [test["name"] for row in results for test in row["tests"]]}
    for requirement, names in MANIFEST["requirements"].items():
        for name in names:
            case = requirement + "." + name
            if not set(MANIFEST.get("requiredKinds", {}).get(case, [])).issubset(cases.get(case, {}).get("kinds", [])):
                FAILURES.append("missing-evidence-" + case)
            if cases.get(case, {}).get("status") != "passed":
                FAILURES.append("failed-or-unexecuted-" + case)
    code = 1 if FAILURES else 0
    write(out / "commands.json", {"runId": RUN_ID, "commands": COMMANDS, "failures": sorted(set(FAILURES))})
    write(evidence, {"runId": RUN_ID, "runnerExitCode": code, "fingerprints": FINGERPRINTS,
                     "cases": cases, "commandReport": str(out / "commands.json")})
    print(f"Evidence: {evidence}; exit {code}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
