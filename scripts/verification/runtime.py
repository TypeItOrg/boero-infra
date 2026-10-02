#!/usr/bin/env python3
"""Disposable local QA/staging-simulation runtime. Never operates on existing environments.

start builds final prod trees, creates two namespace-labelled base+qa+override stacks,
a local TLS proxy and private synthetic fixtures. check emits only real runtime cases.
stop removes only the captured namespace/ownership label pair; no prune or image deletion.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
LABEL = "io.boero.acceptance.namespace"
OWNER = "io.boero.acceptance.owner"
SOURCE = "io.boero.acceptance.source"
DOCKER = ["docker", "--context", "default"]
RESOURCE_KEYS = ("ui-next-cache", "postgres-data", "redis-data", "api-logs", "api-storage", "mailpit-data")
SERVICES = ("ui", "api", "postgres", "redis", "mailpit")
SECRETS = set()
COMMANDS = []
CASES = {
    "Q01.isolated-runtime": ["Independent labelled projects/networks/volumes/loopback ports and credentials",
                              "PostgreSQL marker rows and API-storage marker files cannot cross stacks",
                              "Stopping QA leaves staging-simulation API/UI/DB healthy and unchanged; QA restarts with data"],
    "Q02.flyway-health": ["Both actual PostgreSQL18 databases have successful Flyway history including institutional public-access migration",
                          "API runs qa profile with actual PostgreSQL connection and validated schema columns/constraints",
                          "PostgreSQL, Redis, API, UI and capture-only Mailpit are healthy; HTTP readiness returns UP"],
    "Q03.backup-restore": ["Actual backup-postgres.sh makes private custom pg_dump and validates archive with pg_restore --list",
                            "pg_restore succeeds into a second newly created namespace-owned database, never an existing database",
                            "Restored institution rows, users, Flyway scripts and public_subdomain/logo constraint match source database"],
}


class RuntimeFailure(Exception):
    pass


def redact(value):
    text = str(value)
    for secret in sorted(SECRETS, key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted-jwt]", text)
    text = re.sub(r"([?&](?:token|code)=)[^\s\"'<>]+", r"\1[redacted]", text, flags=re.I)
    text = re.sub(r"(?i)((?:password|accessToken|refreshToken|authorization|cookie|JWT_SECRET|DB_PASSWORD)\s*[:=]\s*)[^\n,}]+",
                  r"\1[redacted]", text)
    text = re.sub(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", "[redacted-opaque-token]", text)
    return text


def register_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r"password|token|secret|encryption.key", key, re.I) and isinstance(item, str):
                SECRETS.add(item)
            register_secrets(item)
    elif isinstance(value, list):
        for item in value:
            register_secrets(item)


def env_for_commands():
    env = {key: value for key, value in os.environ.items() if key in
           {"PATH", "HOME", "XDG_RUNTIME_DIR", "DOCKER_CONFIG", "LANG", "LC_ALL"}}
    env["DOCKER_CONTEXT"] = "default"
    return env


def run(args, cwd=None, input_text=None, check=True, timeout=900):
    result = subprocess.run(list(map(str, args)), cwd=cwd, env=env_for_commands(),
                            input=input_text, text=True, capture_output=True, timeout=timeout)
    COMMANDS.append({"argv": list(map(str, args)), "cwd": str(cwd or ROOT), "exitCode": result.returncode})
    if check and result.returncode:
        raise RuntimeFailure(f"Command failed ({result.returncode}): {' '.join(map(str, args))}\n"
                             + redact((result.stderr or result.stdout)[-14000:]))
    return result


def docker(*args, **kwargs):
    return run([*DOCKER, *args], **kwargs)


def docker_json(*args):
    return json.loads(docker(*args).stdout)


def require_local_daemon(expected=None):
    if os.environ.get("DOCKER_CONTEXT", "default") != "default":
        raise RuntimeFailure("Only the default local Docker context is permitted")
    host = os.environ.get("DOCKER_HOST", "")
    if host and host not in ("unix:///var/run/docker.sock", "unix:///run/docker.sock"):
        raise RuntimeFailure("Remote Docker endpoints are forbidden")
    context = docker_json("context", "inspect", "default")[0]
    socket_url = context["Endpoints"]["docker"]["Host"]
    if socket_url not in ("unix:///var/run/docker.sock", "unix:///run/docker.sock"):
        raise RuntimeFailure("Default Docker endpoint must be the local Unix socket")
    socket_path = Path(socket_url.removeprefix("unix://"))
    if not stat.S_ISSOCK(socket_path.stat().st_mode):
        raise RuntimeFailure("Docker endpoint is not a local socket")
    info = docker_json("info", "--format", "{{json .}}")
    daemon = {"id": info["ID"], "endpoint": socket_url, "name": info["Name"]}
    if expected and daemon != expected:
        raise RuntimeFailure("Docker daemon identity changed; refusing resource operations")
    return daemon


def repo_fingerprint(path):
    digest = hashlib.sha256()
    digest.update(run(["git", "-C", path, "rev-parse", "HEAD"]).stdout.encode())
    digest.update(run(["git", "-C", path, "diff", "--binary", "HEAD"]).stdout.encode())
    names = run(["git", "-C", path, "ls-files", "--others", "--exclude-standard", "-z"]).stdout.split("\0")
    for name in sorted(filter(None, names)):
        digest.update(os.fsencode(name))
        source = path / name
        if source.is_file():
            digest.update(source.read_bytes())
    return digest.hexdigest()


def private_path(path):
    path = path.absolute()
    if path != path.resolve() or not path.is_relative_to(Path("/tmp")):
        raise RuntimeFailure("State must be a nonsymlink path beneath /tmp")
    return path


def private_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def load_state(path):
    path = private_path(path)
    if not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise RuntimeFailure("Private state is missing, not owned by caller, or readable by others")
    state = json.loads(path.read_text())
    if not re.fullmatch(r"boero-acceptance-[a-f0-9]{20}", state.get("namespace", "")):
        raise RuntimeFailure("Invalid acceptance namespace")
    if not re.fullmatch(r"[a-f0-9]{32}", state.get("owner", "")):
        raise RuntimeFailure("Invalid acceptance ownership marker")
    require_local_daemon(state["daemon"])
    for stack in state.get("stacks", {}).values():
        env_file = Path(stack["envFile"])
        if env_file.is_file():
            values = dict(line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line)
            register_secrets(values)
    fixture = Path(state.get("fixturePath", "/nonexistent"))
    if fixture.is_file():
        register_secrets(json.loads(fixture.read_text()))
    return state


def inventory():
    return {"containers": docker("ps", "--no-trunc", "-aq").stdout.split(),
            "volumes": docker("volume", "ls", "--format", "{{.Name}}").stdout.split(),
            "networks": docker("network", "ls", "--no-trunc", "--format", "{{.ID}}").stdout.split()}


def require_new_namespace(namespace):
    for kind, args in (("container", ("ps", "-a", "--format", "{{.Names}}")),
                       ("volume", ("volume", "ls", "--format", "{{.Name}}")),
                       ("network", ("network", "ls", "--format", "{{.Name}}"))):
        if any(name.startswith(namespace) for name in docker(*args).stdout.split()):
            raise RuntimeFailure(f"Namespace already has a {kind}; refusing reuse")


def owned(state, kind, identifier):
    if kind == "container":
        data = docker_json("inspect", identifier)[0]
        name = data["Name"].lstrip("/")
        labels = data.get("Config", {}).get("Labels", {})
        captured = data["Id"]
    else:
        data = docker_json(kind, "inspect", identifier)[0]
        name = data["Name"]
        labels = data.get("Labels", {})
        captured = data.get("Id", name)
    if not name.startswith(state["namespace"] + "-") or labels.get(LABEL) != state["namespace"] or labels.get(OWNER) != state["owner"]:
        raise RuntimeFailure(f"Refusing non-owned {kind}: {name}")
    baseline = state["baseline"][{"container": "containers", "volume": "volumes", "network": "networks"}[kind]]
    if captured in baseline or any(captured.startswith(old) for old in baseline if kind == "network"):
        raise RuntimeFailure(f"Refusing baseline {kind}: {name}")
    return data


def capture_owned(state, state_path):
    records = {}
    for kind, args in (("containers", ("ps", "-aq")), ("volumes", ("volume", "ls", "-q")),
                       ("networks", ("network", "ls", "-q"))):
        found = docker(*args, "--filter", f"label={LABEL}={state['namespace']}",
                       "--filter", f"label={OWNER}={state['owner']}").stdout.split()
        singular = {"containers": "container", "volumes": "volume", "networks": "network"}[kind]
        records[kind] = []
        for identifier in found:
            data = owned(state, singular, identifier)
            records[kind].append(data["Id"] if singular != "volume" else data["Name"])
    state["captured"] = records
    private_json(state_path, state)


def compose(state, stack, *args, **kwargs):
    item = state["stacks"][stack]
    return docker("compose", "--project-name", item["project"], "--env-file", item["envFile"],
                  "-f", ROOT / "compose.yaml", "-f", ROOT / "compose.qa.yaml", "-f", item["overrideFile"], *args, **kwargs)


def mapped_port(state, container, target):
    data = owned(state, "container", container)
    ports = data["NetworkSettings"]["Ports"].get(f"{target}/tcp", [])
    if len(ports) != 1 or ports[0]["HostIp"] != "127.0.0.1":
        raise RuntimeFailure("Acceptance ports must be single loopback-only mappings")
    return int(ports[0]["HostPort"])


def request(base, path, payload=None, token=None, expected=200, method=None):
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        SECRETS.add(token)
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=body, headers=headers,
                                 method=method or ("POST" if payload is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeFailure(f"HTTP {error.code} for {path}: " + redact(error.read().decode(errors="replace"))) from None
    if status != expected:
        raise RuntimeFailure(f"Expected HTTP {expected}, got {status} for {path}")
    value = json.loads(raw) if raw else None
    register_secrets(value)
    return value


def tls_request(state, host, path="/api/health"):
    connection = http.client.HTTPSConnection("127.0.0.1", state["tlsPort"],
                                             context=ssl._create_unverified_context(), timeout=20)
    try:
        connection.request("GET", path, headers={"Host": f"{host}:{state['tlsPort']}"})
        response = connection.getresponse()
        status, body = response.status, response.read()
        if status != 200:
            raise RuntimeFailure(f"TLS proxy {host} returned HTTP {status}")
        return json.loads(body) if path == "/api/health" else len(body)
    finally:
        connection.close()


def psql(state, stack, sql, database=None):
    item = state["stacks"][stack]
    owned(state, "container", item["containers"]["postgres"])
    dbname = database or item["dbName"]
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", dbname):
        raise RuntimeFailure("Unsafe disposable database name")
    return docker("exec", "-i", item["containers"]["postgres"], "psql", "-v", "ON_ERROR_STOP=1", "-U", item["dbUser"],
                  "-d", dbname, "-t", "-A", input_text=sql).stdout.strip()


def build_image(state, app, path, artifacts):
    fingerprint = repo_fingerprint(path)
    provided = os.environ.get(f"ACCEPTANCE_{app.upper()}_IMAGE")
    image = provided or f"{state['namespace']}-{app}:local"
    if provided:
        info = docker_json("image", "inspect", image)[0]
        labels = info.get("Config", {}).get("Labels", {})
        if labels.get(SOURCE) != fingerprint or labels.get("io.boero.acceptance.app") != app:
            raise RuntimeFailure(f"Explicit {app} image lacks the current local source fingerprint")
    else:
        result = docker("build", "--target", "prod", "--label", f"{SOURCE}={fingerprint}",
                        "--label", f"io.boero.acceptance.app={app}", "--label", f"{LABEL}={state['namespace']}",
                        "--tag", image, path, timeout=1800, check=False)
        (artifacts / f"build-{app}.log").write_text(redact(result.stdout + result.stderr))
        if result.returncode:
            raise RuntimeFailure(f"Local prod {app} build failed; see redacted build-{app}.log")
        info = docker_json("image", "inspect", image)[0]
    if repo_fingerprint(path) != fingerprint:
        raise RuntimeFailure(f"{app} working tree changed during build; image is stale")
    return {"tag": image, "id": info["Id"], "fingerprint": fingerprint, "repo": str(path)}


def make_stack(state, key, domain, private):
    project = f"{state['namespace']}-{key}"
    folder = private / key
    folder.mkdir(mode=0o700)
    admin = {"email": f"admin-{key}-{state['namespace'][-8:]}@example.invalid", "password": "Qa!9" + secrets.token_urlsafe(24)}
    public = f"https://{domain}:{state['tlsPort']}"
    values = {
        # The generated third overlay supplies exact verified local images. Base placeholders
        # satisfy required interpolation even for a provided digest or implicit latest tag.
        "UI_IMAGE": "boero-acceptance-unused-ui", "UI_VERSION": "local",
        "API_IMAGE": "boero-acceptance-unused-api", "API_VERSION": "local",
        "AUTH_COOKIE_SECURE": "true", "DB_NAME": f"acceptance_{key}", "DB_USER": "acceptance",
        "DB_PASSWORD": secrets.token_urlsafe(32), "JWT_SECRET": secrets.token_urlsafe(48),
        "REFRESH_REPLAY_ENCRYPTION_KEY": base64.b64encode(secrets.token_bytes(32)).decode(), "REFRESH_REPLAY_TTL": "5s",
        "FRONTEND_PUBLIC_URL": public, "INSTITUTIONAL_BASE_DOMAIN": domain, "WEBAUTHN_RP_ID": domain,
        "WEBAUTHN_ALLOWED_ORIGINS": public,
        "EMAIL_VERIFICATION_FRONTEND_URL": public, "PASSWORD_RECOVERY_FRONTEND_URL": public,
        "MAIL_HOST": "mailpit", "MAIL_PORT": "1025", "MAIL_USERNAME": "qa-unused", "MAIL_PASSWORD": "qa-unused",
        "MAIL_SMTP_AUTH": "false", "MAIL_SMTP_STARTTLS": "false", "MAIL_FROM": "no-reply@example.invalid",
        "PLATFORM_ADMIN_EMAIL": admin["email"], "PLATFORM_ADMIN_PASSWORD": admin["password"],
        "STORAGE_PROVIDER": "local", "API_MEMORY_LIMIT": "1024m", "UI_MEMORY_LIMIT": "1024m",
        "POSTGRES_MEMORY_LIMIT": "512m", "REDIS_MEMORY_LIMIT": "128m", "MAILPIT_MEMORY_LIMIT": "128m",
        "BACKUP_DIR": str(private / "backups"), "BACKUP_RETENTION_DAYS": "7",
    }
    register_secrets(values)
    env_file = folder / "synthetic.env"
    env_file.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
    env_file.chmod(0o600)
    labels = f'      {LABEL}: "{state["namespace"]}"\n      {OWNER}: "{state["owner"]}"\n'
    rows = [f"name: {project}", "services:"]
    containers = {}
    for service in (*SERVICES, "api-storage-init"):
        containers[service] = f"{project}-{service}"
        rows.extend([f"  {service}:", f"    container_name: {containers[service]}", "    labels:", labels.rstrip()])
        if service in ("api", "ui", "api-storage-init"):
            image = state["images"]["api" if service == "api-storage-init" else service]["tag"]
            rows.extend([f"    image: {image}", "    pull_policy: never"])
        if service in SERVICES:
            rows.append('    restart: "no"')
        if service in ("ui", "api", "mailpit"):
            target = {"ui": 3000, "api": 8080, "mailpit": 8025}[service]
            rows.extend(["    ports: !override", f'      - "127.0.0.1:0:{target}"'])
    rows.append("volumes:")
    volumes = {}
    for volume in RESOURCE_KEYS:
        volumes[volume] = f"{project}-{volume}"
        rows.extend([f"  {volume}:", "    external: false", f"    name: {volumes[volume]}", "    labels:", labels.rstrip()])
    network = f"{project}-network"
    rows.extend(["networks:", "  default:", f"    name: {network}", "    labels:", labels.rstrip()])
    override = folder / "acceptance.override.yaml"
    override.write_text("\n".join(rows) + "\n")
    override.chmod(0o600)
    item = {"project": project, "domain": domain, "publicBase": public, "envFile": str(env_file),
            "overrideFile": str(override), "network": network, "volumes": volumes, "containers": containers,
            "dbName": values["DB_NAME"], "dbUser": values["DB_USER"]}
    state["stacks"][key] = item
    return admin


def create_proxy(state, private):
    config = ["server { listen 443 ssl default_server; server_name _;",
              "ssl_certificate /etc/acceptance/cert.pem; ssl_certificate_key /etc/acceptance/key.pem; return 444; }"]
    for item in state["stacks"].values():
        config.extend(["server { listen 443 ssl;", f"server_name {item['domain']} *.{item['domain']};",
                       "ssl_certificate /etc/acceptance/cert.pem; ssl_certificate_key /etc/acceptance/key.pem;",
                       "client_max_body_size 15m;",
                       "location ^~ /api/v1 { return 404; } location ^~ /actuator { return 404; }",
                       f"location / {{ proxy_pass http://{item['containers']['ui']}:3000;",
                       "proxy_http_version 1.1; proxy_set_header Host $http_host;",
                       "proxy_set_header X-Forwarded-Proto https; proxy_set_header X-Forwarded-For $remote_addr;",
                       "proxy_buffering off; proxy_read_timeout 60s; } }"])
    path = private / "nginx.conf"
    path.write_text("\n".join(config) + "\n")
    path.chmod(0o600)
    image = "nginx:alpine"
    if docker("image", "inspect", image, check=False).returncode:
        docker("pull", image)
    name = state["namespace"] + "-tls"
    created = docker("create", "--name", name, "--label", f"{LABEL}={state['namespace']}",
                     "--label", f"{OWNER}={state['owner']}", "--network", state["stacks"]["qa"]["network"],
                     "--publish", f"127.0.0.1:{state['tlsPort']}:443", "--security-opt", "no-new-privileges:true",
                     "--volume", f"{path}:/etc/nginx/conf.d/default.conf:ro",
                     "--volume", f"{private / 'cert.pem'}:/etc/acceptance/cert.pem:ro",
                     "--volume", f"{private / 'key.pem'}:/etc/acceptance/key.pem:ro", image).stdout.strip()
    state["proxy"] = {"name": name, "id": created, "image": docker_json("image", "inspect", image)[0]["Id"]}
    owned(state, "container", created)
    docker("network", "connect", state["stacks"]["staging"]["network"], created)
    docker("start", created)
    if mapped_port(state, created, 443) != state["tlsPort"]:
        raise RuntimeFailure("TLS mapped port differs from canonical origin")


def seed_fixtures(state, admins):
    fixtures = {"namespace": state["namespace"], "stacks": {}}
    for key, item in state["stacks"].items():
        country, province, city = (str(uuid.uuid4()) for _ in range(3))
        psql(state, key, f"""INSERT INTO countries(country_id,name,iso_code,created_at,updated_at)
VALUES ('{country}','Acceptance Country','ZZZ',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
INSERT INTO provinces(province_id,country_id,name,georef_id,created_at,updated_at)
VALUES ('{province}','{country}','Acceptance Province','99',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
INSERT INTO cities(city_id,province_id,name,georef_id,department_georef_id,municipality_georef_id,created_at,updated_at)
VALUES ('{city}','{province}','Acceptance City','99999','999','999',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);""")
        admin = admins[key]
        login = request(item["apiBase"], "/api/v1/admin/auth/login", admin | {"rememberMe": False})
        token = login["tokens"]["accessToken"]
        entry = {"publicBase": item["publicBase"], "apiBase": item["apiBase"], "mailBase": item["mailBase"], "admin": admin}
        document = str(secrets.randbelow(90_000_000) + 10_000_000)
        for tenant, subdomain, name in (("tenantA", "cboero", "Conservatorio Boero " + key.upper()),
                                       ("tenantB", "otra", "Institución Otra " + key.upper())):
            created = request(item["apiBase"], "/api/v1/admin/institutions", {"name": name, "slug": f"catalog-{subdomain}-{key}",
                              "cityId": city, "street": "Calle Prueba"}, token, 201)
            institution = str(uuid.UUID(created["id"]))
            request(item["apiBase"], f"/api/v1/admin/institutions/{institution}/public-access",
                    {"publicSubdomain": subdomain}, token, method="PATCH")
            email = f"student-{key}-{subdomain}-{state['namespace'][-8:]}@example.invalid"
            password = "Qa!9" + secrets.token_urlsafe(24)
            SECRETS.add(password)
            registered = request(item["apiBase"], "/api/v1/auth/register", {"institutionId": institution, "documentNumber": document,
                                 "name": "Nombre", "lastName": "Prueba", "birthDate": "2000-01-01", "email": email,
                                 "password": password}, expected=201)
            user = str(uuid.UUID(registered["userId"]))
            person = str(uuid.UUID(psql(state, key, f"SELECT person_id FROM users WHERE user_id='{user}';")))
            # This is setup of only the new synthetic fixture identity, not browser/email proof.
            psql(state, key, f"UPDATE users SET email_verification_status='VERIFIED',email_verified_at=CURRENT_TIMESTAMP WHERE user_id='{user}';")
            request(item["apiBase"], f"/api/v1/admin/institutions/{institution}/authority/{person}", token=token,
                    expected=201, method="POST")
            entry[tenant] = {"id": institution, "name": name, "publicSubdomain": subdomain, "document": document,
                             "password": password, "email": email, "userId": user, "personId": person}
        fixtures["stacks"][key] = entry
    return fixtures


def diagnostics(state, artifacts):
    artifacts.mkdir(parents=True, exist_ok=True)
    for item in state.get("stacks", {}).values():
        for service in SERVICES:
            name = item["containers"][service]
            try:
                owned(state, "container", name)
                result = docker("logs", "--tail", "300", name, check=False)
                (artifacts / f"{item['project']}-{service}.log").write_text(redact(result.stdout + result.stderr))
            except RuntimeFailure:
                pass
    if state.get("proxy"):
        try:
            owned(state, "container", state["proxy"]["id"])
            result = docker("logs", "--tail", "200", state["proxy"]["id"], check=False)
            (artifacts / "tls-proxy.log").write_text(redact(result.stdout + result.stderr))
        except RuntimeFailure:
            pass


def start(args):
    state_path = private_path(args.state)
    if state_path.exists():
        raise RuntimeFailure("State already exists; stop and choose a fresh state path for a new run")
    artifacts = args.artifacts.resolve()
    if not artifacts.is_relative_to(ROOT / "build/verification"):
        raise RuntimeFailure("Artifacts must be in ignored infra build/verification, never a private env directory")
    artifacts.mkdir(parents=True, exist_ok=True)
    namespace = "boero-acceptance-" + uuid.uuid4().hex[:20]
    daemon = require_local_daemon()
    require_new_namespace(namespace)
    private = Path(tempfile.mkdtemp(prefix=namespace + "-", dir="/tmp"))
    private.chmod(0o700)
    # Reserve the TLS origin port before building; keep it reserved until proxy creation.
    import socket
    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    tls_port = reservation.getsockname()[1]
    state = {"version": 1, "namespace": namespace, "owner": uuid.uuid4().hex, "daemon": daemon,
             "baseline": inventory(), "privateDir": str(private), "artifacts": str(artifacts), "status": "starting",
             "tlsPort": tls_port, "stacks": {}, "images": {}, "fixturePath": str(private / "fixtures.json")}
    private_json(private / "ownership.json", {"namespace": namespace, "owner": state["owner"]})
    private_json(state_path, state)
    try:
        repos = {"api": Path(os.environ.get("API_REPO", ROOT.parent / "boero-api")).resolve(),
                 "ui": Path(os.environ.get("UI_REPO", ROOT.parent / "boero-ui")).resolve()}
        for app, repo in repos.items():
            state["images"][app] = build_image(state, app, repo, artifacts)
            private_json(state_path, state)
        run(["openssl", "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "2", "-subj", "/CN=boero-acceptance-local",
             "-addext", "subjectAltName=DNS:testing.typeit.com.ar,DNS:*.testing.typeit.com.ar,DNS:staging.typeit.com.ar,DNS:*.staging.typeit.com.ar",
             "-keyout", private / "key.pem", "-out", private / "cert.pem"], timeout=30)
        (private / "key.pem").chmod(0o600)
        admins = {key: make_stack(state, key, domain, private) for key, domain in
                  (("qa", "testing.typeit.com.ar"), ("staging", "staging.typeit.com.ar"))}
        private_json(state_path, state)
        for key, item in state["stacks"].items():
            compose(state, key, "config", "--quiet")
            compose(state, key, "up", "-d", "--wait", "--wait-timeout", "180", "postgres", "redis", "mailpit")
            capture_owned(state, state_path)
            compose(state, key, "run", "--rm", "--no-deps", "api-storage-init")
            compose(state, key, "up", "-d", "--wait", "--wait-timeout", "600", "api", "ui", timeout=750)
            capture_owned(state, state_path)
            item["apiBase"] = f"http://127.0.0.1:{mapped_port(state, item['containers']['api'], 8080)}"
            item["uiBase"] = f"http://127.0.0.1:{mapped_port(state, item['containers']['ui'], 3000)}"
            item["mailBase"] = f"http://127.0.0.1:{mapped_port(state, item['containers']['mailpit'], 8025)}"
            request(item["apiBase"], "/actuator/health/readiness")
            request(item["uiBase"], "/api/health")
            private_json(state_path, state)
        reservation.close()
        create_proxy(state, private)
        capture_owned(state, state_path)
        for item in state["stacks"].values():
            deadline = time.monotonic() + 30
            while True:
                try:
                    tls_request(state, item["domain"])
                    break
                except (OSError, RuntimeFailure):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
        fixtures = seed_fixtures(state, admins)
        private_json(Path(state["fixturePath"]), fixtures)
        state["status"] = "ready"
        private_json(state_path, state)
        (artifacts / "runtime-start.json").write_text(json.dumps({"namespace": namespace, "status": "ready",
             "images": state["images"], "projects": {key: item["project"] for key, item in state["stacks"].items()},
             "commands": COMMANDS, "boundary": "local disposable stacks; fixture identities database-verified only for setup"}, indent=2) + "\n")
        print(json.dumps({"namespace": namespace, "fixturePath": state["fixturePath"]}))
        return 0
    except Exception as error:
        reservation.close()
        state["status"] = "failed-start"
        private_json(state_path, state)
        try:
            capture_owned(state, state_path)
        except RuntimeFailure as capture_error:
            state["captureError"] = redact(capture_error)
            private_json(state_path, state)
        diagnostics(state, artifacts)
        (artifacts / "runtime-start-failure.json").write_text(json.dumps({"namespace": namespace, "error": redact(error), "commands": COMMANDS}, indent=2) + "\n")
        raise RuntimeFailure("Disposable start failed; redacted diagnostics saved. Cleanup with stop --state " + str(state_path)) from None


def check_health(state, observations):
    expected = [path.name for path in (Path(state["images"]["api"]["repo"]) / "src/main/resources/db/migration").glob("*institution_public_access*.sql")]
    if not expected:
        # Filename is generated by repository migration tooling; select by the migration's real contract.
        expected = [path.name for path in (Path(state["images"]["api"]["repo"]) / "src/main/resources/db/migration").glob("*.sql")
                    if "institutions_public_subdomain_unique" in path.read_text()]
    if len(expected) != 1:
        raise RuntimeFailure("Could not identify exactly one new institutional public-access migration")
    for key, item in state["stacks"].items():
        statuses = {}
        for service in SERVICES:
            data = owned(state, "container", item["containers"][service])
            health = data["State"].get("Health", {}).get("Status")
            if not data["State"]["Running"] or health != "healthy":
                raise RuntimeFailure(f"{key}/{service} not healthy: {health}")
            statuses[service] = health
        api = owned(state, "container", item["containers"]["api"])
        values = dict(line.split("=", 1) for line in api["Config"]["Env"] if "=" in line)
        if values.get("SPRING_PROFILES_ACTIVE") != "qa" or values.get("DB_HOST") != "postgres":
            raise RuntimeFailure("Acceptance API does not use real QA/PostgreSQL profile")
        if api["Image"] != state["images"]["api"]["id"]:
            raise RuntimeFailure("API is not running the locally built final prod image")
        if owned(state, "container", item["containers"]["ui"])["Image"] != state["images"]["ui"]["id"]:
            raise RuntimeFailure("UI is not running the locally built final prod image")
        version = psql(state, key, "SHOW server_version;")
        if not version.startswith("18"):
            raise RuntimeFailure("Expected real PostgreSQL18")
        history = json.loads(psql(state, key, "SELECT json_agg(t) FROM (SELECT script,success FROM flyway_schema_history ORDER BY installed_rank) t;"))
        if not all(row["success"] for row in history) or expected[0] not in {row["script"] for row in history}:
            raise RuntimeFailure("Flyway failed or omitted the new institutional migration")
        columns = psql(state, key, "SELECT count(*) FROM information_schema.columns WHERE table_name='institutions' AND column_name IN ('public_subdomain','logo_key','logo_content_type','logo_version','logo_size');")
        if columns != "5":
            raise RuntimeFailure("New schema columns not present")
        if request(item["apiBase"], "/actuator/health/readiness")["status"] != "UP":
            raise RuntimeFailure("API readiness is not UP")
        request(item["uiBase"], "/api/health")
        tls_request(state, item["domain"])
        observations[key] = {"healthyServices": statuses, "postgresVersion": version, "migration": expected[0], "flywaySuccessCount": len(history), "newColumns": 5}


def check_backup(state, observations):
    private = Path(state["privateDir"])
    key, item = "qa", state["stacks"]["qa"]
    workspace = private / "backup-workspace"
    workspace.mkdir(mode=0o700, exist_ok=True)
    (workspace / "scripts").mkdir(mode=0o700, exist_ok=True)
    shutil.copy2(ROOT / "scripts/backup-postgres.sh", workspace / "scripts/backup-postgres.sh")
    # Snapshot is private: rendered environment contains synthetic credentials. No existing .env is used.
    rendered = compose(state, key, "config", "--format", "json").stdout
    (workspace / "compose.yaml").write_text(rendered)
    (workspace / "compose.qa.yaml").write_text("{}\n")
    shutil.copy2(item["envFile"], workspace / ".env.qa")
    for file in (workspace / "compose.yaml", workspace / "compose.qa.yaml", workspace / ".env.qa"):
        file.chmod(0o600)
    run(["sh", workspace / "scripts/backup-postgres.sh", "qa"], cwd=workspace)
    archives = sorted((private / "backups/qa").glob("*.dump"))
    if not archives or not archives[-1].stat().st_size or archives[-1].stat().st_mode & 0o077:
        raise RuntimeFailure("Backup archive missing/empty or readable by others")
    archive = archives[-1]
    # pg_restore consumes binary archives, so use a binary subprocess rather than text orchestration.
    def restore_command(*args):
        owned(state, "container", item["containers"]["postgres"])
        argv = [*DOCKER, "exec", "-i", item["containers"]["postgres"], "pg_restore", *args]
        result = subprocess.run(argv, input=archive.read_bytes(), capture_output=True, env=env_for_commands(), timeout=180)
        COMMANDS.append({"argv": argv, "exitCode": result.returncode})
        if result.returncode:
            raise RuntimeFailure("Disposable pg_restore failed: " + redact(result.stderr.decode(errors="replace")))
        return result.stdout.decode(errors="replace")
    listing = restore_command("--list")
    if "institutions" not in listing or "flyway_schema_history" not in listing:
        raise RuntimeFailure("Custom backup list omits required schema")
    restored = "acceptance_restore_" + secrets.token_hex(4)
    if psql(state, key, f"SELECT count(*) FROM pg_database WHERE datname='{restored}';", "postgres") != "0":
        raise RuntimeFailure("Restore database already exists; refusing overwrite")
    psql(state, key, f"CREATE DATABASE {restored};", "postgres")
    try:
        restore_command("--exit-on-error", "--no-owner", "--username", item["dbUser"], "--dbname", restored)
        query = """SELECT json_build_object('institutions',(SELECT json_agg(t ORDER BY id) FROM
(SELECT institution_id::text AS id,name,public_subdomain FROM institutions) t),
'users',(SELECT count(*) FROM users),'flyway',(SELECT json_agg(script ORDER BY installed_rank) FROM flyway_schema_history),
'logoConstraint',(SELECT count(*) FROM pg_constraint WHERE conname='institutions_logo_metadata_check'));"""
        original = json.loads(psql(state, key, query))
        recovered = json.loads(psql(state, key, query, restored))
        if original != recovered or recovered["users"] < 2 or recovered["logoConstraint"] != 1:
            raise RuntimeFailure("Restored rows/schema differ from source")
        observations.update({"archiveBytes": archive.stat().st_size, "archiveMode": "0600", "customArchiveValidated": True,
                             "restoredDatabase": restored, "restoredUsers": recovered["users"], "restoredInstitutionCount": len(recovered["institutions"]),
                             "matchingFlywayScripts": len(recovered["flyway"]), "logoConstraintPresent": True,
                             "backupHelperSha256": hashlib.sha256((workspace / "scripts/backup-postgres.sh").read_bytes()).hexdigest()})
    finally:
        psql(state, key, f"DROP DATABASE {restored};", "postgres")


def check_isolation(state, observations):
    stacks = state["stacks"]
    volumes = [{owned(state, "volume", name)["Name"] for name in item["volumes"].values()} for item in stacks.values()]
    if not volumes[0].isdisjoint(volumes[1]):
        raise RuntimeFailure("Stack volumes overlap")
    networks = {owned(state, "network", item["network"])["Id"] for item in stacks.values()}
    if len(networks) != 2:
        raise RuntimeFailure("Stack networks overlap")
    ports = {state["tlsPort"]}
    secrets_per_stack = []
    for key, item in stacks.items():
        values = dict(line.split("=", 1) for line in Path(item["envFile"]).read_text().splitlines() if "=" in line)
        secrets_per_stack.append([values[name] for name in ("DB_PASSWORD", "JWT_SECRET", "REFRESH_REPLAY_ENCRYPTION_KEY", "PLATFORM_ADMIN_PASSWORD")])
        for service in SERVICES:
            data = owned(state, "container", item["containers"][service])
            if set(data["NetworkSettings"]["Networks"]) != {item["network"]}:
                raise RuntimeFailure("Application service attached outside its own stack network")
        for service, target in (("api", 8080), ("ui", 3000), ("mailpit", 8025)):
            port = mapped_port(state, item["containers"][service], target)
            if port in ports:
                raise RuntimeFailure("Acceptance ports overlap")
            ports.add(port)
        psql(state, key, "CREATE TABLE IF NOT EXISTS acceptance_runtime_marker (environment text PRIMARY KEY, marker text NOT NULL);")
        psql(state, key, f"INSERT INTO acceptance_runtime_marker VALUES ('{key}','{state['namespace']}-{key}') ON CONFLICT DO NOTHING;")
        if psql(state, key, "SELECT count(*) FROM acceptance_runtime_marker;") != "1" or psql(state, key, "SELECT environment FROM acceptance_runtime_marker;") != key:
            raise RuntimeFailure("Database marker isolation failed")
        own_file = f"/app/storage/{state['namespace']}-{key}.txt"
        other_key = "staging" if key == "qa" else "qa"
        script = f"printf '%s' '{key}' > '{own_file}'\ntest ! -e '/app/storage/{state['namespace']}-{other_key}.txt'\ncat '{own_file}'\n"
        output = docker("exec", "-i", item["containers"]["api"], "sh", "-e", input_text=script).stdout
        if output != key:
            raise RuntimeFailure("Storage marker isolation failed")
    if any(left == right for left, right in zip(*secrets_per_stack)):
        raise RuntimeFailure("QA reused staging-simulation secret material")
    before = {service: owned(state, "container", stacks["staging"]["containers"][service])["State"]["StartedAt"] for service in SERVICES}
    try:
        compose(state, "qa", "stop", timeout=120)
        for service in SERVICES:
            data = owned(state, "container", stacks["staging"]["containers"][service])
            if not data["State"]["Running"] or data["State"].get("Health", {}).get("Status") != "healthy" or data["State"]["StartedAt"] != before[service]:
                raise RuntimeFailure("Stopping QA interrupted staging simulation")
        request(stacks["staging"]["apiBase"], "/actuator/health/readiness")
        tls_request(state, stacks["staging"]["domain"])
    finally:
        compose(state, "qa", "up", "-d", "--wait", "--wait-timeout", "600", timeout=750)
    if psql(state, "qa", "SELECT environment FROM acceptance_runtime_marker;") != "qa":
        raise RuntimeFailure("QA database marker lost on restart")
    output = docker("exec", stacks["qa"]["containers"]["api"], "cat", f"/app/storage/{state['namespace']}-qa.txt").stdout
    if output != "qa":
        raise RuntimeFailure("QA storage marker lost on restart")
    observations.update({"independentVolumeCount": len(volumes[0] | volumes[1]), "independentNetworkCount": 2,
                         "distinctLoopbackPortCount": len(ports), "independentSecrets": True, "databaseMarkersIsolated": True,
                         "storageMarkersIsolated": True, "stagingUninterruptedWhileQaStopped": True, "qaMarkersPersistAfterRestart": True})


def check(args):
    evidence = args.evidence.resolve()
    if not evidence.is_relative_to(ROOT / "build/verification"):
        raise RuntimeFailure("Runtime evidence must be in ignored infra build/verification")
    # A failed prerequisite must not leave a previously passed case mapping in place.
    evidence.unlink(missing_ok=True)
    state = load_state(args.state)
    artifacts = Path(state["artifacts"])
    report = artifacts / "runtime-report.json"
    observations = {"health": {}, "backup": {}, "isolation": {}}
    success, error = False, None
    try:
        if state["status"] != "ready":
            raise RuntimeFailure("Runtime fixtures are not ready")
        for image in state["images"].values():
            if repo_fingerprint(Path(image["repo"])) != image["fingerprint"]:
                raise RuntimeFailure("Application source changed since build; restart with fresh final images")
        check_health(state, observations["health"])
        check_backup(state, observations["backup"])
        check_isolation(state, observations["isolation"])
        success = True
    except Exception as failure:
        error = redact(failure)
        diagnostics(state, artifacts)
    report.write_text(json.dumps({"namespace": state["namespace"], "time": datetime.now(timezone.utc).isoformat(),
                                  "passed": success, "error": error, "observations": observations, "commands": COMMANDS}, indent=2) + "\n")
    cases = {case: {"status": "passed" if success else "failed", "artifact": str(report.resolve()),
                    "tests": tests, "kind": "runtime"} for case, tests in CASES.items()}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(cases, indent=2) + "\n")
    print(json.dumps({"namespace": state["namespace"], "evidence": str(args.evidence.resolve()), "passed": success}))
    if not success:
        print("Runtime checks failed: " + (error or "unknown error"), file=sys.stderr)
    return 0 if success else 1


def stop(args):
    state_path = private_path(args.state)
    state = load_state(state_path)
    if state["status"] == "stopped":
        print(json.dumps({"namespace": state["namespace"], "status": "stopped"}))
        return 0
    capture_owned(state, state_path)
    for identifier in state["captured"]["containers"]:
        owned(state, "container", identifier)
        docker("rm", "-f", identifier)
    for identifier in state["captured"]["networks"]:
        owned(state, "network", identifier)
        docker("network", "rm", identifier)
    for identifier in state["captured"]["volumes"]:
        owned(state, "volume", identifier)
        docker("volume", "rm", identifier)
    private = Path(state["privateDir"])
    expected = {"namespace": state["namespace"], "owner": state["owner"]}
    if private.parent != Path("/tmp") or not private.name.startswith(state["namespace"] + "-") or private != private.resolve():
        raise RuntimeFailure("Refusing unsafe private fixture directory cleanup")
    if json.loads((private / "ownership.json").read_text()) != expected:
        raise RuntimeFailure("Private credential directory ownership proof mismatch")
    shutil.rmtree(private)
    state["status"] = "stopped"
    state.pop("fixturePath", None)
    state.pop("privateDir", None)
    state["stacks"] = {}
    private_json(state_path, state)
    print(json.dumps({"namespace": state["namespace"], "status": "stopped"}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for name in ("start", "check", "stop"):
        child = subparsers.add_parser(name)
        child.add_argument("--state", required=True, type=Path)
        if name == "start":
            child.add_argument("--artifacts", required=True, type=Path)
        if name == "check":
            child.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    try:
        return {"start": start, "check": check, "stop": stop}[args.operation](args)
    except Exception as error:
        print(redact(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
