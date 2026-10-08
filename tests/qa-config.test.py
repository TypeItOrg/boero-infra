#!/usr/bin/env python3
"""QA config/script/workflow checks: synthetic envs, mocked Docker and a real temp Git graph.

Uses only Python stdlib and the installed docker compose CLI. Never starts containers,
reads an existing .env, connects via SSH or pushes.
"""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

INFRA = Path(__file__).resolve().parents[1]
API = Path(os.environ.get("API_REPO", INFRA.parent / "boero-api")).resolve()
UI = Path(os.environ.get("UI_REPO", INFRA.parent / "boero-ui")).resolve()
OLD_SHA = "a" * 40
NEW_SHA = "b" * 40
OTHER_SHA = "c" * 40


def command(args, cwd, env=None, check=True):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if check and result.returncode:
        raise AssertionError(f"{args!r} failed ({result.returncode}): {result.stderr}")
    return result


def synthetic_env():
    return {
        "UI_VERSION": "sha-" + OLD_SHA, "API_VERSION": "sha-" + OTHER_SHA,
        "AUTH_COOKIE_SECURE": "true", "DB_NAME": "qa_disposable", "DB_USER": "qa_disposable",
        "DB_PASSWORD": "synthetic-only", "JWT_SECRET": "synthetic-only-key-" + "x" * 32,
        "REFRESH_REPLAY_ENCRYPTION_KEY": "c3ludGhldGljLW9ubHktMzItYnl0ZS1rZXktMDAwMDA=",
        "WEBAUTHN_RP_ID": "testing.typeit.com.ar", "WEBAUTHN_ALLOWED_ORIGINS": "https://testing.typeit.com.ar",
        "MAIL_HOST": "external.invalid", "MAIL_USERNAME": "synthetic-unused", "MAIL_PASSWORD": "synthetic-unused",
        "MAIL_FROM": "qa@example.invalid", "PASSWORD_RECOVERY_FRONTEND_URL": "https://testing.typeit.com.ar",
    }


def write_env(path, values):
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)


def cli_environment():
    # Compose interpolation must not inherit any developer environment variable.
    return {k: v for k, v in os.environ.items() if k in
            {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"}}


def job(text, name):
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", text, re.M | re.S)
    if not match:
        raise AssertionError(f"Missing job {name}")
    return match.group(0)


def scalar(text, indent, key):
    match = re.search(rf"^{' ' * indent}{re.escape(key)}: ([^\n]+)$", text, re.M)
    if not match:
        raise AssertionError(f"Missing scalar {key}")
    value = match.group(1)
    if value in (">-", ">", "|"):
        lines = text[match.end():].splitlines()
        # Restrict multiline condition to contiguous indentation, not later steps.
        value_lines = []
        for line in lines[1:]:
            if not line.startswith(" " * (indent + 2)):
                break
            value_lines.append(line.strip())
        value = " ".join(value_lines)
    return value


def condition(text, event, ref):
    expression = scalar(text, 4, "if")
    expression = expression.replace("github.event_name", repr(event)).replace("github.ref_name", repr(ref))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.Constant)):
            raise AssertionError(f"Unsupported CI condition: {expression}")
    return eval(compile(tree, "<ci-condition>", "eval"), {"__builtins__": {}}, {})


def run_block(workflow, step):
    match = re.search(rf"^      - name: {re.escape(step)}\n(.*?)(?=^      - name:|\Z)", workflow, re.M | re.S)
    if not match:
        raise AssertionError(f"Missing step {step}")
    block = re.search(r"^        run: \|\n((?:          [^\n]*\n|\n)+)", match.group(1), re.M)
    if not block:
        raise AssertionError(f"Missing executable body in {step}")
    return "\n".join(line[10:] for line in block.group(1).splitlines())


class QaConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="boero-qa-config-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def render(self, environment, values):
        env_file = self.work / f"synthetic-{environment}.env"
        write_env(env_file, values)
        result = command(["docker", "compose", "--profile", "maintenance", "--env-file", str(env_file),
                          "-f", str(INFRA / "compose.yaml"), "-f", str(INFRA / f"compose.{environment}.yaml"),
                          "config", "--format", "json"], INFRA, cli_environment())
        return json.loads(result.stdout)

    def mocked_workspace(self):
        for name in ("Makefile", "compose.yaml", "compose.qa.yaml", "compose.staging.yaml", "compose.production.yaml"):
            shutil.copy2(INFRA / name, self.work / name)
        shutil.copytree(INFRA / "scripts", self.work / "scripts")
        for environment in ("qa", "staging", "production"):
            values = synthetic_env()
            values["BACKUP_DIR"] = str(self.work / "backups")
            values["BACKUP_RETENTION_DAYS"] = "7"
            write_env(self.work / f".env.{environment}", values)
        bindir = self.work / "bin"
        bindir.mkdir()
        docker = bindir / "docker"
        docker.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
with open(os.environ['DOCKER_TRACE'], 'a') as stream:
    stream.write(json.dumps(args)+'\\n')
if args[:2] == ['volume', 'create']:
    print(args[2]); sys.exit(0)
if args[0] != 'compose': sys.exit(9)
if os.environ.get('FAIL_PREFLIGHT') == 'true' and args[-2:] == ['config','--quiet']:
    sys.exit(2)
envfile=pathlib.Path(args[args.index('--env-file')+1])
values=dict(line.split('=',1) for line in envfile.read_text().splitlines() if '=' in line)
if 'up' in args and os.environ.get('FAIL_NEW_VERSION') == 'true':
    key='API_VERSION' if args[-1] == 'api' else 'UI_VERSION'
    if values[key] == 'sha-'+('b'*40): sys.exit(3)
if 'api-storage-init' in args and os.environ.get('FAIL_STORAGE_INIT') == 'true':
    if values['API_VERSION'] == 'sha-'+('b'*40): sys.exit(4)
if 'exec' in args and 'postgres' in args:
    if any('pg_dump' in arg for arg in args): print('synthetic-custom-dump')
    if 'pg_restore' in args:
        if not sys.stdin.read().startswith('synthetic-custom-dump'): sys.exit(5)
''')
        docker.chmod(0o755)
        self.trace = self.work / "docker.jsonl"
        self.env = cli_environment() | {"PATH": str(bindir) + ":" + os.environ["PATH"], "DOCKER_TRACE": str(self.trace)}

    def trace_commands(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def make(self, target, extra=(), env=None, check=True):
        return command(["make", target, "ENV=qa", *extra], self.work, self.env | (env or {}), check)

    def preserved(self):
        return {name: (self.work / name).read_bytes() for name in (".env.staging", ".env.production")}

    def assert_preserved(self, baseline):
        for name, value in baseline.items():
            self.assertEqual((self.work / name).read_bytes(), value)
        self.assertFalse((self.work / ".deploy/staging").exists())
        self.assertFalse((self.work / ".deploy/production").exists())

    def test_compose_isolation(self):
        configs = {environment: self.render(environment, synthetic_env()) for environment in ("qa", "staging", "production")}
        qa = configs["qa"]
        self.assertEqual(qa["name"], "boero-qa")
        self.assertEqual(qa["networks"]["default"]["name"], "boero-network-qa")
        expected = {"ui": (3001, 3000), "api": (8081, 8080), "mailpit": (8026, 8025)}
        for service, ports in expected.items():
            bound = qa["services"][service]["ports"]
            self.assertEqual(len(bound), 1)
            self.assertEqual((int(bound[0]["published"]), bound[0]["target"]), ports)
            self.assertEqual(bound[0]["host_ip"], "127.0.0.1")
        for service in ("postgres", "redis"):
            self.assertNotIn("ports", qa["services"][service])
        for name, service in qa["services"].items():
            if name != "api-storage-init":
                self.assertEqual(service["container_name"], f"boero-{name}-qa")
        for other in (configs["staging"], configs["production"]):
            self.assertTrue({v["name"] for v in qa["volumes"].values()}.isdisjoint(v["name"] for v in other["volumes"].values()))
            self.assertNotEqual(qa["networks"]["default"]["name"], other["networks"]["default"]["name"])
            self.assertTrue({port["published"] for name in expected for port in qa["services"][name]["ports"]}.isdisjoint(
                port["published"] for name in ("ui", "api") for port in other["services"][name]["ports"]))
            self.assertEqual(other["services"]["ui"]["environment"]["FRONTEND_PUBLIC_URL"], "")
        api = qa["services"]["api"]
        self.assertEqual(api["environment"]["MAIL_HOST"], "mailpit")
        self.assertEqual(str(api["environment"]["MAIL_PORT"]), "1025")
        self.assertEqual(api["environment"]["MAIL_SMTP_AUTH"], "false")
        self.assertEqual(api["environment"]["MAIL_SMTP_STARTTLS"], "false")
        self.assertEqual(qa["services"]["mailpit"]["expose"], ["1025"])
        self.assertNotIn("MP_SMTP_RELAY_CONFIG", qa["services"]["mailpit"]["environment"])
        helper = qa["services"]["api-storage-init"]
        self.assertEqual(helper["image"], api["image"])
        self.assertEqual(helper["network_mode"], "none")
        self.assertEqual([v["source"] for v in helper["volumes"]], ["api-storage"])

    def test_qa_profile(self):
        profile = API / "src/main/resources/application-qa.properties"
        values = dict(line.split("=", 1) for line in profile.read_text().splitlines() if line and not line.startswith("#"))
        self.assertEqual(values["spring.jpa.hibernate.ddl-auto"], "validate")
        self.assertEqual(values["spring.flyway.locations"], "classpath:db/migration")
        self.assertEqual(values["spring.datasource.driver-class-name"], "org.postgresql.Driver")
        self.assertTrue(values["spring.datasource.url"].startswith("jdbc:postgresql:"))
        self.assertEqual(values["spring.sql.init.mode"], "never")
        self.assertEqual(values["springdoc.api-docs.enabled"], "false")
        self.assertEqual(values["springdoc.swagger-ui.enabled"], "false")
        rendered = self.render("qa", synthetic_env())
        self.assertEqual(rendered["services"]["api"]["environment"]["SPRING_PROFILES_ACTIVE"], "qa")
        self.assertEqual(rendered["services"]["postgres"]["image"], "postgres:18-alpine")
        self.assertIn("readiness", " ".join(rendered["services"]["api"]["healthcheck"]["test"]))
        self.assertIn("pg_isready", " ".join(rendered["services"]["postgres"]["healthcheck"]["test"]))
        guard = API / "src/main/java/ar/edu/utn/frvm/typeit/boero_api/auth/config/WebAuthnDeploymentGuard.java"
        exemptions = re.findall(r'profile.equalsIgnoreCase\("([^"]+)"\)', guard.read_text())
        self.assertEqual(set(exemptions), {"dev", "test"})

    def test_postgres_final_readiness(self):
        bindir = self.work / "bin"
        bindir.mkdir()
        # initdb's temporary server accepts Unix sockets immediately; the real TCP
        # endpoint becomes ready only on the third probe. CREATE must never run early.
        implementations = {
            "docker-entrypoint.sh": '''#!/usr/bin/env python3
import os,pathlib,time
work=pathlib.Path(os.environ['PG_TEST_WORK'])
deadline=time.monotonic()+5
while not (work/'created').exists():
    if time.monotonic()>deadline: raise SystemExit(9)
    time.sleep(.01)
''',
            "pg_isready": '''#!/usr/bin/env python3
import json,os,pathlib,sys
work=pathlib.Path(os.environ['PG_TEST_WORK'])
with (work/'probes.jsonl').open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')
if '-h' not in sys.argv: raise SystemExit(0)
if sys.argv[sys.argv.index('-h')+1]!='127.0.0.1': raise SystemExit(8)
count_file=work/'probe-count'
count=int(count_file.read_text())+1 if count_file.exists() else 1
count_file.write_text(str(count))
if count<3: raise SystemExit(1)
(work/'tcp-ready').touch()
''',
            "psql": '''#!/usr/bin/env python3
import os,pathlib,sys
work=pathlib.Path(os.environ['PG_TEST_WORK'])
if not (work/'tcp-ready').exists(): raise SystemExit(10)
if any('CREATE DATABASE' in arg for arg in sys.argv): (work/'created').touch()
''',
            "sleep": "#!/bin/sh\nexit 0\n",
        }
        for name, source in implementations.items():
            target = bindir / name
            target.write_text(source)
            target.chmod(0o755)
        environment = cli_environment() | {"PATH": str(bindir)+":"+os.environ["PATH"],
                                            "PG_TEST_WORK": str(self.work), "POSTGRES_USER": "fixture", "DB_NAME": "fixture_qa"}
        command(["sh", str(INFRA / "docker/postgres-entrypoint.sh")], self.work, environment)
        probes = [json.loads(line) for line in (self.work / "probes.jsonl").read_text().splitlines()]
        self.assertEqual(len(probes), 3)
        self.assertTrue(all(probe[probe.index("-h")+1] == "127.0.0.1" for probe in probes))
        self.assertTrue((self.work / "created").exists())

    def test_qa_operations(self):
        self.mocked_workspace()
        baseline = self.preserved()
        for target in ("prepare", "preflight", "status", "logs", "logs-api", "logs-api-file", "down"):
            self.make(target)
        self.make("logs-api-request", ["REQUEST_ID=synthetic-id"])
        self.make("deploy-ui", ["VERSION=sha-" + NEW_SHA])
        self.assertIn("UI_VERSION=sha-" + NEW_SHA, (self.work / ".env.qa").read_text())
        self.make("rollback-ui")
        self.assertIn("UI_VERSION=sha-" + OLD_SHA, (self.work / ".env.qa").read_text())
        self.make("deploy-api", ["VERSION=sha-" + NEW_SHA])
        self.make("rollback-api")
        self.assertIn("API_VERSION=sha-" + OTHER_SHA, (self.work / ".env.qa").read_text())
        self.make("backup-db")
        archives = list((self.work / "backups/qa").glob("*.dump"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].stat().st_mode & 0o777, 0o600)
        self.assertFalse(list((self.work / "backups/qa").glob("*.tmp")))
        commands = self.trace_commands()
        volumes = {cmd[2] for cmd in commands if cmd[:2] == ["volume", "create"]}
        self.assertEqual(volumes, {f"boero-{name}-qa" for name in
                                  ("ui-next-cache", "api-postgres-data", "api-redis-data", "api-logs", "api-enrollment-storage")})
        for args in commands:
            if args[0] == "compose":
                self.assertIn("compose.qa.yaml", args)
                self.assertNotIn("compose.staging.yaml", args)
                self.assertNotIn("compose.production.yaml", args)
        self.assertTrue(any("--remove-orphans" in args for args in commands))
        self.assertFalse(any("--volumes" in args for args in commands))
        self.assert_preserved(baseline)

    def test_bootstrap_order(self):
        self.mocked_workspace()
        self.make("bootstrap")
        commands = self.trace_commands()
        self.assertEqual(commands[0][-2:], ["config", "--quiet"])
        self.assertEqual(commands[1][:2], ["volume", "create"])
        pull = next(i for i, cmd in enumerate(commands) if cmd[-1:] == ["pull"])
        initialize = next(i for i, cmd in enumerate(commands) if "api-storage-init" in cmd)
        up = next(i for i, cmd in enumerate(commands) if "up" in cmd)
        self.assertLess(pull, initialize)
        self.assertLess(initialize, up)
        self.assertIn("--wait", commands[up])

    def test_invalid_inputs(self):
        self.mocked_workspace()
        baseline = self.preserved() | {".env.qa": (self.work / ".env.qa").read_bytes()}
        targets = ("prepare", "preflight", "bootstrap", "deploy-api", "deploy-ui", "rollback-api", "rollback-ui", "backup-db", "status", "logs", "down")
        for environment in ("bad", "../staging", "%", "qa staging"):
            for target in targets:
                result = command(["make", target, "ENV=" + environment, "VERSION=sha-" + NEW_SHA], self.work, self.env, False)
                self.assertNotEqual(result.returncode, 0)
        for script in ("prepare-volumes.sh", "bootstrap.sh", "deploy-service.sh", "rollback-service.sh", "backup-postgres.sh"):
            self.assertNotEqual(command(["./scripts/" + script, "unknown", "api", "sha-" + NEW_SHA], self.work, self.env, False).returncode, 0)
        for version in ("", "develop", "sha-bbbb", "sha-" + "B" * 40, "sha-" + "z" * 40, "sha-" + "a" * 41,
                        "sha-abcd;touch injected", "sha-$$(touch injected)"):
            self.assertNotEqual(self.make("deploy-api", ["VERSION=" + version], check=False).returncode, 0)
        self.assertEqual(self.trace_commands(), [])
        self.assertFalse((self.work / "injected").exists())
        self.assertFalse((self.work / ".deploy").exists())
        self.assert_preserved(baseline)

    def test_preflight_preservation(self):
        self.mocked_workspace()
        baseline = self.preserved() | {".env.qa": (self.work / ".env.qa").read_bytes()}
        for target in ("bootstrap", "deploy-api", "deploy-ui"):
            self.assertNotEqual(self.make(target, ["VERSION=sha-" + NEW_SHA], {"FAIL_PREFLIGHT": "true"}, False).returncode, 0)
        self.assertTrue(all(args[-2:] == ["config", "--quiet"] for args in self.trace_commands()))
        self.assertFalse((self.work / ".deploy").exists())
        self.assert_preserved(baseline)

    def test_unhealthy_rollback(self):
        self.mocked_workspace()
        baseline = self.preserved()
        for service, previous in (("ui", OLD_SHA), ("api", OTHER_SHA)):
            with self.subTest(service=service):
                before = (self.work / ".env.qa").read_bytes()
                start = len(self.trace_commands())
                self.assertNotEqual(self.make("deploy-" + service, ["VERSION=sha-" + NEW_SHA],
                                              {"FAIL_NEW_VERSION": "true"}, False).returncode, 0)
                self.assertEqual((self.work / ".env.qa").read_bytes(), before)
                up_commands = [args for args in self.trace_commands()[start:] if "up" in args]
                self.assertEqual(len(up_commands), 2)
                self.assertIn("--wait", up_commands[0])
                self.assertEqual((self.work / f".deploy/qa/{service}.previous").read_text().strip(), "sha-" + previous)
                self.assert_preserved(baseline)

    def test_api_initialization_failure(self):
        self.mocked_workspace()
        before = (self.work / ".env.qa").read_bytes()
        self.assertNotEqual(self.make("deploy-api", ["VERSION=sha-" + NEW_SHA], {"FAIL_STORAGE_INIT": "true"}, False).returncode, 0)
        self.assertEqual((self.work / ".env.qa").read_bytes(), before)
        commands = self.trace_commands()
        # Failed candidate initializer never reaches startup; only restored image is started.
        self.assertEqual(sum("api-storage-init" in args for args in commands), 2)
        self.assertEqual(sum("up" in args for args in commands), 1)

    def test_publication_gates(self):
        for repo, expected in ((API, "[static-analysis, test]"), (UI, "verify")):
            workflow = (repo / ".github/workflows/ci.yaml").read_text()
            publish = job(workflow, "publish-image")
            self.assertEqual(scalar(publish, 4, "needs"), expected)
            for event in ("push", "pull_request", "workflow_dispatch"):
                for ref in ("develop", "staging", "main", "testing", "feature/example"):
                    self.assertEqual(condition(publish, event, ref), event == "push" and ref in ("develop", "staging", "main"))
            self.assertIn("sha-${{ github.sha }}", publish)
            self.assertIn("push: true", publish)
            if repo == API:
                self.assertIn("staticAnalysis", job(workflow, "static-analysis"))
                self.assertIn("spotlessCheck fastTest", job(workflow, "test"))
                self.assertIn("integrationTest", job(workflow, "test"))
                self.assertIn("spotlessCheck test", job(workflow, "test"))
            else:
                for gate in ("format:check", "lint", "typecheck", "test --runInBand", "build"):
                    self.assertIn("run: pnpm " + gate, job(workflow, "verify"))

    def test_existing_flows(self):
        for repo, app in ((API, "api"), (UI, "ui")):
            workflow_path = ".github/workflows/ci.yaml"
            before = command(["git", "show", "HEAD:" + workflow_path], repo).stdout
            after = (repo / workflow_path).read_text()
            self.assertEqual(job(before, "deploy-staging"), job(after, "deploy-staging"))
            for ref in ("develop", "staging", "main"):
                self.assertEqual(condition(job(after, "deploy-staging"), "push", ref), ref == "staging")
            self.assertIn(f"make deploy-{app} ENV=staging VERSION=sha-${{{{ github.sha }}}}", job(after, "deploy-staging"))
            production_path = ".github/workflows/deploy-production.yaml"
            self.assertEqual(command(["git", "show", "HEAD:" + production_path], repo).stdout, (repo / production_path).read_text())
        for name in ("compose.staging.yaml", "compose.production.yaml"):
            self.assertEqual(command(["git", "show", "HEAD:" + name], INFRA).stdout, (INFRA / name).read_text())

    def test_manual_qa_workflows(self):
        bare = self.work / "origin.git"
        gitdir = self.work / "repo"
        command(["git", "init", "--bare", str(bare)], self.work)
        command(["git", "init", "-b", "develop", str(gitdir)], self.work)
        command(["git", "config", "user.email", "qa@example.invalid"], gitdir)
        command(["git", "config", "user.name", "Synthetic QA"], gitdir)
        command(["git", "commit", "--allow-empty", "-m", "baseline"], gitdir)
        ancestor = command(["git", "rev-parse", "HEAD"], gitdir).stdout.strip()
        command(["git", "commit", "--allow-empty", "-m", "develop change"], gitdir)
        tip = command(["git", "rev-parse", "HEAD"], gitdir).stdout.strip()
        command(["git", "branch", "unrelated", ancestor], gitdir)
        command(["git", "checkout", "unrelated"], gitdir)
        command(["git", "commit", "--allow-empty", "-m", "unrelated change"], gitdir)
        unrelated = command(["git", "rev-parse", "HEAD"], gitdir).stdout.strip()
        command(["git", "remote", "add", "origin", str(bare)], gitdir)
        command(["git", "push", "origin", "develop"], gitdir)
        bindir = self.work / "workflow-bin"
        bindir.mkdir()
        docker = bindir / "docker"
        docker.write_text("#!/bin/sh\n[ \"${IMAGE_AVAILABLE:-false}\" = true ]\n")
        docker.chmod(0o755)
        ssh = bindir / "ssh"
        ssh.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SSH_TRACE\"\n")
        ssh.chmod(0o755)
        ssh_trace = self.work / "ssh.txt"
        for repo, app in ((API, "api"), (UI, "ui")):
            workflow = (repo / ".github/workflows/deploy-qa.yaml").read_text()
            trigger = workflow.split("\nconcurrency:", 1)[0]
            self.assertIn("  workflow_dispatch:", trigger)
            self.assertNotIn("  push:", trigger)
            self.assertNotIn("  pull_request:", trigger)
            self.assertEqual(scalar(job(workflow, "deploy"), 4, "environment"), "qa")
            self.assertIn("StrictHostKeyChecking=yes", workflow)
            self.assertIn(f"make deploy-{app} ENV=qa VERSION=sha-$COMMIT_SHA", workflow)
            self.assertNotIn("ENV=staging", workflow)
            self.assertNotIn("ENV=production", workflow)
            self.assertLess(workflow.index("Verify immutable image is available"), workflow.index("Configure SSH"))
            self.assertIn(f'docker buildx imagetools inspect "ghcr.io/typeitorg/boero-{app}:sha-$COMMIT_SHA"', workflow)
            validate = run_block(workflow, "Validate QA version")
            for sha, accepted in ((ancestor, True), (tip, True), (unrelated, False), ("d" * 40, False),
                                  (ancestor[:7], False), ("A" * 40, False), ("$(touch injected)", False)):
                result = command(["bash", "-e", "-o", "pipefail", "-c", validate], gitdir,
                                 cli_environment() | {"COMMIT_SHA": sha}, False)
                self.assertEqual(result.returncode == 0, accepted)
            self.assertFalse((gitdir / "injected").exists())
            inspect = re.search(r"^        run: (docker buildx imagetools inspect[^\n]+)$", workflow, re.M).group(1)
            deploy = run_block(workflow, f"Deploy {app} to QA")
            workflow_env = cli_environment() | {"PATH": str(bindir) + ":" + os.environ["PATH"],
                                               "COMMIT_SHA": tip, "DEPLOY_HOST": "synthetic.invalid",
                                               "DEPLOY_USER": "qa-test", "SSH_TRACE": str(ssh_trace)}
            # Execute extracted guards and deploy command with registry/SSH fakes: a missing
            # image must prevent any SSH invocation, while an existing image targets only QA.
            if ssh_trace.exists():
                ssh_trace.unlink()
            sequence = validate + "\n" + inspect + "\n" + deploy
            unavailable = command(["bash", "-e", "-o", "pipefail", "-c", sequence], gitdir,
                                  workflow_env | {"IMAGE_AVAILABLE": "false"}, False)
            self.assertNotEqual(unavailable.returncode, 0)
            self.assertFalse(ssh_trace.exists())
            command(["bash", "-e", "-o", "pipefail", "-c", sequence], gitdir,
                    workflow_env | {"IMAGE_AVAILABLE": "true"})
            invoked = ssh_trace.read_text()
            self.assertIn("ENV=qa VERSION=sha-" + tip, invoked)
            self.assertIn("StrictHostKeyChecking=yes", invoked)
            self.assertNotIn("ENV=staging", invoked)
            self.assertNotIn("ENV=production", invoked)


    def test_documented_contract(self):
        template = (INFRA / ".env.qa.example").read_text()
        values = dict(line.split("=", 1) for line in template.splitlines() if line and not line.startswith("#"))
        for key in ("FRONTEND_PUBLIC_URL", "PASSWORD_RECOVERY_FRONTEND_URL", "EMAIL_VERIFICATION_FRONTEND_URL"):
            self.assertEqual(values[key], "https://testing.typeit.com.ar")
        self.assertEqual(values["INSTITUTIONAL_BASE_DOMAIN"], "testing.typeit.com.ar")
        self.assertEqual(values["WEBAUTHN_RP_ID"], "testing.typeit.com.ar")
        self.assertEqual(values["WEBAUTHN_ALLOWED_ORIGINS"], "https://testing.typeit.com.ar")
        self.assertEqual(values["DB_NAME"], "boero_qa")
        self.assertEqual(values["DB_USER"], "boero_qa")
        for key in ("DB_PASSWORD", "JWT_SECRET", "REFRESH_REPLAY_ENCRYPTION_KEY", "PLATFORM_ADMIN_PASSWORD"):
            self.assertIn("independent-qa", values[key])
        qa_docs = (INFRA / "docs/QA.md").read_text()
        for required in ("rama predeterminada", "DEPLOY_SSH_KNOWN_HOSTS", "backup-db ENV=qa", "staging",
                         "PostgreSQL", "descartable", "no publica DNS", "WebAuthn", "Mailpit", "INSTITUTIONAL_BASE_DOMAIN"):
            self.assertIn(required, qa_docs)
        staging = (INFRA / "docs/STAGING.md").read_text()
        self.assertIn("ya no describe estos archivos", staging)
        for script in ("prepare-volumes.sh", "bootstrap.sh", "deploy-service.sh", "rollback-service.sh", "backup-postgres.sh"):
            command(["sh", "-n", str(INFRA / "scripts" / script)], INFRA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
