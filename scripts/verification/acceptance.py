#!/usr/bin/env python3
"""Fail-closed acceptance registry; never certifies source-only checks as runtime."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("requirements.json")
EXPECTED_IDS = {"Q01", "Q02", "Q03", "Q04", "C01", "C02", "I01", "I02",
                "A01", "A02", "A03", "A04", "A05", "A06", "L01", "L02", "L03", "S01", "D01", "V01"}


def fingerprint(path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args])
    digest = hashlib.sha256()
    digest.update(git("rev-parse", "HEAD"))
    digest.update(git("diff", "--binary", "HEAD"))
    for name in sorted(git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
        if name:
            p = path / os.fsdecode(name)
            digest.update(name)
            if p.is_file():
                digest.update(p.read_bytes())
    return digest.hexdigest()


def audit(manifest, evidence, current, run_id=None):
    errors = []
    requirements = manifest.get("requirements", {})
    if set(requirements) != EXPECTED_IDS:
        errors.append("El manifiesto no contiene exactamente los 20 requisitos aprobados.")
    if evidence.get("fingerprints") != current:
        errors.append("La evidencia no corresponde al estado actual de los repositorios.")
    if not isinstance(evidence.get("runId"), str) or not evidence["runId"].strip():
        errors.append("La evidencia no identifica una ejecución.")
    if run_id is not None and evidence.get("runId") != run_id:
        errors.append("La evidencia no pertenece a esta ejecución.")
    if evidence.get("runnerExitCode") != 0:
        errors.append("El ejecutor no finalizó correctamente.")
    cases = evidence.get("cases", {})

    def checked_artifact(case_id, path, expected_hash):
        if not isinstance(path, str) or not path:
            errors.append(f"{case_id}: no tiene evidencia de ejecución.")
            return None
        artifact = Path(path)
        if not artifact.is_absolute() or not artifact.is_file() or artifact.stat().st_size == 0:
            errors.append(f"{case_id}: archivo de evidencia ausente o vacío.")
            return None
        contents = artifact.read_bytes()
        if expected_hash != hashlib.sha256(contents).hexdigest():
            errors.append(f"{case_id}: la evidencia fue modificada o no tiene huella.")
            return None
        return contents

    for requirement, names in requirements.items():
        if not names:
            errors.append(f"{requirement}: no tiene casos obligatorios.")
        for name in names:
            case_id = f"{requirement}.{name}"
            result = cases.get(case_id, {})
            if result.get("status") != "passed":
                errors.append(f"{case_id}: pendiente, omitido o fallido.")
            tests = result.get("tests")
            if not isinstance(tests, list) or not tests or not all(isinstance(test, str) and test.strip() for test in tests):
                errors.append(f"{case_id}: no identifica las comprobaciones ejecutadas.")
            required_kinds = manifest.get("requiredKinds", {}).get(case_id, [])
            if isinstance(required_kinds, str):
                required_kinds = [required_kinds]
            actual_kinds = result.get("kinds", [result.get("kind")])
            for kind in required_kinds:
                if kind not in actual_kinds:
                    errors.append(f"{case_id}: requiere evidencia {kind}, no sólo código/mocks.")
            contents = checked_artifact(case_id, result.get("artifact"), result.get("sha256"))
            if contents is None:
                continue
            try:
                wrapper = json.loads(contents)
                checks = wrapper["checks"]
                if (wrapper.get("runId") != evidence.get("runId") or wrapper.get("fingerprints") != current
                        or wrapper.get("case") != case_id or not isinstance(checks, list) or not checks):
                    raise ValueError("Invalid provenance or empty checks")
            except (KeyError, TypeError, ValueError):
                errors.append(f"{case_id}: artefacto sin comprobaciones o procedencia vigente.")
                continue
            checked_names, checked_kinds, screenshots = [], set(), set()
            for check in checks:
                check_tests = check.get("tests", [])
                if (check.get("status") != "passed" or not isinstance(check_tests, list) or not check_tests
                        or any(not isinstance(test, dict) or test.get("status") != "passed"
                               or not isinstance(test.get("name"), str) or not test["name"].strip() for test in check_tests)):
                    errors.append(f"{case_id}: comprobación real fallida, omitida o sin ejecutar.")
                    continue
                checked_names.extend(test["name"] for test in check_tests)
                checked_kinds.add(check.get("kind"))
                report_bytes = checked_artifact(case_id, check.get("artifact"), check.get("sha256"))
                if report_bytes is None:
                    continue
                try:
                    report = json.loads(report_bytes)
                    if report.get("passed") is False or report.get("status", "passed") != "passed" or report.get("processExitCode", 0) != 0:
                        raise ValueError("Report failed")
                    for executed in report.get("tests", []):
                        if executed.get("status") != "passed":
                            raise ValueError("Report contains failed/skipped check")
                        for attachment in executed.get("attachments", []):
                            if checked_artifact(case_id, attachment.get("path"), attachment.get("sha256")) is not None:
                                screenshots.add(attachment.get("name"))
                except (AttributeError, TypeError, ValueError):
                    errors.append(f"{case_id}: reporte de ejecución inválido, fallido u omitido.")
            if set(actual_kinds) != checked_kinds or not isinstance(tests, list) or Counter(tests) != Counter(checked_names):
                errors.append(f"{case_id}: índice y comprobaciones reales no coinciden.")
            if not set(manifest.get("requiredScreenshots", {}).get(case_id, [])).issubset(screenshots):
                errors.append(f"{case_id}: faltan capturas reales obligatorias.")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    repos = {"infra": ROOT, "api": Path(os.environ["API_REPO"]).resolve(),
             "ui": Path(os.environ["UI_REPO"]).resolve()}
    for path in repos.values():
        if not (path / ".git").exists():
            parser.error(f"Repositorio no disponible: {path}")
    out = ROOT / "build/verification/qa-institutional-access"
    out.mkdir(parents=True, exist_ok=True)
    evidence_path = args.evidence or out / "evidence.json"
    if not args.audit_only and not evidence_path.resolve().is_relative_to(ROOT / "build/verification"):
        parser.error("La evidencia de ejecución debe permanecer en build/verification ignorado.")
    run_id = None
    runner_code = None
    if not args.audit_only:
        run_id = uuid.uuid4().hex
        os.environ["ACCEPTANCE_RUN_ID"] = run_id
        initial = {name: fingerprint(path) for name, path in repos.items()}
        os.environ["ACCEPTANCE_FINGERPRINTS"] = json.dumps(initial)
        # A crashed/unavailable runner must never leave a previously green index as
        # the apparent result of this attempt, including for a later audit-only call.
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps({"runId": run_id, "fingerprints": initial,
                                            "runnerExitCode": None, "cases": {}}, indent=2) + "\n")
        runner = Path(__file__).with_name("run-local-acceptance.sh")
        if not runner.is_file():
            print("BLOQUEADO: el ejecutor de pruebas locales aún no está implementado.", file=sys.stderr)
        else:
            result = subprocess.run(["sh", str(runner), str(evidence_path)], cwd=ROOT)
            runner_code = result.returncode
            if result.returncode:
                print("El ejecutor informó fallos; se conserva el cierre incompleto.", file=sys.stderr)
    current = {name: fingerprint(path) for name, path in repos.items()}
    manifest = json.loads(MANIFEST.read_text())
    try:
        evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
    except (OSError, ValueError):
        evidence = {}
    errors = audit(manifest, evidence, current, run_id)
    if not args.audit_only and runner_code != 0:
        errors.append("Esta ejecución no completó todos los controles obligatorios.")
    report = {"time": datetime.now(timezone.utc).isoformat(), "boundary": "local-only",
              "complete": not errors, "errors": errors, "fingerprints": current}
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = ["# Aceptación QA y acceso institucional", "", "Ámbito: local; no desplegado.", "",
             "Resultado: " + ("VERIFICADO" if not errors else "INCOMPLETO"), ""]
    lines.extend(["| ID | Estado | Casos verificados |", "| --- | --- | --- |"])
    global_errors = [error for error in errors if not any(
        error.startswith(requirement + ".") or error.startswith(requirement + ":") for requirement in EXPECTED_IDS)]
    for requirement, names in manifest["requirements"].items():
        passed = sum(evidence.get("cases", {}).get(f"{requirement}.{name}", {}).get("status") == "passed" for name in names)
        relevant_errors = [error for error in errors if error.startswith(requirement + ".") or error.startswith(requirement + ":")]
        provenance_ok = (not global_errors and evidence.get("fingerprints") == current and evidence.get("runnerExitCode") == 0
                         and (run_id is None or evidence.get("runId") == run_id)
                         and (args.audit_only or runner_code == 0))
        verified = passed == len(names) and not relevant_errors and provenance_ok
        lines.append(f"| {requirement} | {'verificado' if verified else 'pendiente/bloqueado'} | {passed}/{len(names)} |")
    lines.extend(["", "## Controles pendientes o fallidos", ""])
    lines.extend(f"- {error}" for error in errors)
    lines.extend(["", "## Código, pruebas y evidencia", ""])
    for requirement, names in manifest["requirements"].items():
        lines.extend([f"### {requirement}", ""])
        for source in manifest.get("sourcePaths", {}).get(requirement, []):
            repo, relative = source.split("/", 1)
            lines.append(f"- Código: [{source}]({repos[repo] / relative})")
        for name in names:
            case_id = f"{requirement}.{name}"
            case = evidence.get("cases", {}).get(case_id, {})
            artifact = case.get("artifact")
            link = f"[evidencia y pruebas]({artifact})" if artifact else "sin evidencia"
            lines.append(f"- `{case_id}`: {case.get('status', 'pending')}; {link}.")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"Informe: {out / 'report.md'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
