from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


CHECK_KINDS = ("build", "test", "lint", "typecheck")
READY = "READY"
NOT_DETECTED = "NOT_DETECTED"
SETUP_REQUIRED = "SETUP_REQUIRED"
AMBIGUOUS = "AMBIGUOUS"
DISABLED = "DISABLED"
INVALID_CONFIG = "INVALID_CONFIG"
DETECTED_UNSUPPORTED = "DETECTED_UNSUPPORTED"


@dataclass
class BuildProfileConfig:
    java_enabled: bool = True
    java_home: str = "auto"
    java_target_version: str = "8"
    python_enabled: bool = True
    python_interpreter: str = "current"
    check_selection: dict[str, str] = field(
        default_factory=lambda: {kind: "auto" for kind in CHECK_KINDS}
    )
    safe_profiles_auto: bool = True
    config_errors: tuple[str, ...] = field(default=(), repr=False)

    @classmethod
    def from_json(cls, value: object) -> "BuildProfileConfig":
        if not isinstance(value, dict):
            return cls()
        errors: list[str] = []
        java = value.get("java", {})
        python = value.get("python", {})
        if not isinstance(java, dict):
            errors.append("build_profiles.java must be an object")
            java = {}
        if not isinstance(python, dict):
            errors.append("build_profiles.python must be an object")
            python = {}
        java_enabled = java.get("enabled", True)
        python_enabled = python.get("enabled", True)
        java_home = java.get("java_home", "auto")
        java_target = java.get("target_version", "8")
        python_interpreter = python.get("interpreter", "current")
        if not isinstance(java_enabled, bool):
            errors.append("java.enabled must be true or false")
        if not isinstance(python_enabled, bool):
            errors.append("python.enabled must be true or false")
        if not isinstance(java_home, str):
            errors.append("java.java_home must be text")
        if not isinstance(java_target, (str, int)) or isinstance(java_target, bool):
            errors.append("java.target_version must be text or an integer")
        if not isinstance(python_interpreter, str):
            errors.append("python.interpreter must be text")
        selection_value = value.get("check_selection", {})
        selections = {kind: "auto" for kind in CHECK_KINDS}
        if isinstance(selection_value, dict):
            for kind in CHECK_KINDS:
                selected = selection_value.get(kind)
                if isinstance(selected, str) and selected.strip():
                    selections[kind] = selected.strip()
                elif selected is not None:
                    errors.append(f"check_selection.{kind} must be text")
        else:
            errors.append("check_selection must be an object")
        policy = value.get("execution_policy", {})
        if not isinstance(policy, dict):
            errors.append("execution_policy must be an object")
            policy = {}
        safe_policy = policy.get("safe_profiles", "auto")
        project_policy = policy.get("project_code_profiles", "ask")
        if safe_policy not in {"auto", "ask"}:
            errors.append("execution_policy.safe_profiles must be auto or ask")
        if project_policy != "ask":
            errors.append("execution_policy.project_code_profiles must remain ask")
        return cls(
            java_enabled=java_enabled if isinstance(java_enabled, bool) else True,
            java_home=java_home if isinstance(java_home, str) and java_home else "auto",
            java_target_version=str(java_target) if isinstance(java_target, (str, int)) and not isinstance(java_target, bool) else "8",
            python_enabled=python_enabled if isinstance(python_enabled, bool) else True,
            python_interpreter=python_interpreter if isinstance(python_interpreter, str) and python_interpreter else "current",
            check_selection=selections,
            safe_profiles_auto=safe_policy == "auto",
            config_errors=tuple(errors),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "java": {
                "enabled": self.java_enabled,
                "java_home": self.java_home,
                "target_version": self.java_target_version,
            },
            "python": {
                "enabled": self.python_enabled,
                "interpreter": self.python_interpreter,
            },
            "check_selection": {
                kind: self.check_selection.get(kind, "auto") for kind in CHECK_KINDS
            },
            "execution_policy": {
                "safe_profiles": "auto" if self.safe_profiles_auto else "ask",
                "project_code_profiles": "ask",
            },
        }


@dataclass(frozen=True)
class ProfileDetection:
    profile_id: str
    project_root: Path
    status: str
    evidence: tuple[str, ...]
    reason: str
    capabilities: frozenset[str]
    executable: Path | None = None
    executable_source: str = ""
    version: str = ""
    inputs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class CheckPlan:
    profile_id: str
    kind: str
    mode: str
    project_root: Path
    argv: tuple[str, ...] = ()
    description: str = ""
    safe_auto: bool = False
    config_hash: str = ""
    input_hash: str = ""
    inputs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class PlanResolution:
    status: str
    reason: str
    plan: CheckPlan | None = None
    candidates: tuple[str, ...] = ()


def javac_from_java_home(java_home: Path | str | None) -> Path | None:
    if not java_home or str(java_home).casefold() == "auto":
        return None
    candidate = Path(str(java_home).strip().strip('"')).expanduser()
    candidate = candidate / "bin" / ("javac.exe" if os.name == "nt" else "javac")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() else None


def _outside_roots(candidate: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            candidate.relative_to(root)
            return False
        except ValueError:
            continue
    return True


def discover_java_compiler(
    configured_java_home: Path | str | None,
    workspace: Path,
    protected_roots: tuple[Path, ...] = (),
) -> tuple[Path | None, str]:
    """Resolve javac without ever searching the current working directory."""
    forbidden = (workspace.resolve(), *(root.resolve() for root in protected_roots))
    explicit = bool(configured_java_home and str(configured_java_home).casefold() != "auto")
    if explicit:
        compiler = javac_from_java_home(configured_java_home)
        if compiler is None or not _outside_roots(compiler, forbidden):
            return None, "config-invalid"
        return compiler, "config"

    environment = javac_from_java_home(os.environ.get("JAVA_HOME"))
    if environment and _outside_roots(environment, forbidden):
        return environment, "JAVA_HOME"

    executable = "javac.exe" if os.name == "nt" else "javac"
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = raw_directory.strip().strip('"')
        if not directory:
            continue
        try:
            candidate = Path(os.path.expandvars(directory)).expanduser() / executable
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and _outside_roots(resolved, forbidden):
            return resolved, "PATH"
    return None, "unavailable"


def _java_version(compiler: Path) -> tuple[str, int | None]:
    release_file = compiler.parent.parent / "release"
    try:
        text = release_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown", None
    match = re.search(r'^JAVA_VERSION="([^"]+)"', text, flags=re.M)
    if not match:
        return "unknown", None
    version = match.group(1)
    major_text = version.split(".", 2)[1] if version.startswith("1.") else version.split(".", 1)[0]
    try:
        return version, int(major_text)
    except ValueError:
        return version, None


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_inputs(root: Path, paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            content = path.read_bytes()
        except (OSError, ValueError):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


class BuildProfileRegistry:
    def __init__(
        self,
        workspace: Path,
        visible: Callable[[Path], bool],
        protected_roots: tuple[Path, ...],
        config: BuildProfileConfig,
    ) -> None:
        self.workspace = workspace.resolve()
        self.visible = visible
        self.protected_roots = tuple(root.resolve() for root in protected_roots)
        self.config = config
        self._scanned_files: tuple[Path, ...] | None = None
        self._detections: tuple[ProfileDetection, ...] | None = None
        self._input_hashes: dict[tuple[Path, tuple[Path, ...]], str] = {}

    def detections(self) -> tuple[ProfileDetection, ...]:
        if self._detections is None:
            self._detections = (*self._java_detections(), *self._python_detections())
        return self._detections

    def resolve(self, kind: str) -> PlanResolution:
        if kind not in CHECK_KINDS:
            return PlanResolution(INVALID_CONFIG, f"Unknown check kind: {kind}.")
        if self.config.config_errors:
            return PlanResolution(INVALID_CONFIG, "; ".join(self.config.config_errors))
        detections = tuple(item for item in self.detections() if kind in item.capabilities)
        selected = self.config.check_selection.get(kind, "auto")
        executable_profiles = {"java.javac", "python.compile", "python.unittest"}

        if selected != "auto":
            detection = next((item for item in detections if item.profile_id == selected), None)
            if detection is None:
                return PlanResolution(
                    INVALID_CONFIG,
                    f"Configured profile {selected!r} does not support {kind} in this workspace.",
                )
            if detection.profile_id not in executable_profiles:
                return PlanResolution(
                    SETUP_REQUIRED,
                    f"{detection.profile_id} was detected but execution is not supported yet.",
                    candidates=(detection.profile_id,),
                )
            if detection.status not in {READY, AMBIGUOUS}:
                return PlanResolution(detection.status, detection.reason, candidates=(detection.profile_id,))
            return PlanResolution(READY, "Explicit configured profile selected.", self._plan(detection, kind))

        ready = tuple(
            item for item in detections
            if item.status == READY and item.profile_id in executable_profiles
        )
        blocking = tuple(
            item for item in detections
            if item.status in {INVALID_CONFIG, AMBIGUOUS}
        )
        if blocking:
            return PlanResolution(
                blocking[0].status,
                blocking[0].reason,
                candidates=tuple(item.profile_id for item in detections),
            )
        if len(ready) == 1:
            return PlanResolution(READY, "One ready profile detected.", self._plan(ready[0], kind))
        if len(ready) > 1:
            names = tuple(item.profile_id for item in ready)
            return PlanResolution(
                AMBIGUOUS,
                f"Multiple profiles support {kind}: {', '.join(names)}. Choose one in Build & Check Setup.",
                candidates=names,
            )
        detected = tuple(item for item in detections if item.status != NOT_DETECTED)
        if detected:
            item = detected[0]
            return PlanResolution(item.status, item.reason, candidates=tuple(x.profile_id for x in detected))
        return PlanResolution(NOT_DETECTED, f"No built-in {kind} profile was detected.")

    def _visible_files(self, pattern: str) -> tuple[Path, ...]:
        if self._scanned_files is None:
            found: list[Path] = []
            for current, directories, files in os.walk(self.workspace):
                current_path = Path(current)
                directories[:] = sorted(
                    name for name in directories if self.visible(current_path / name)
                )
                for name in files:
                    path = current_path / name
                    if self.visible(path):
                        found.append(path)
            self._scanned_files = tuple(sorted(found))
        return tuple(path for path in self._scanned_files if path.match(pattern))

    def _java_detections(self) -> tuple[ProfileDetection, ...]:
        java_files = self._visible_files("*.java")
        markers = tuple(sorted({
            *self._visible_files("pom.xml"),
            *self._visible_files("build.gradle"),
            *self._visible_files("build.gradle.kts"),
        }))
        evidence = tuple(path.relative_to(self.workspace).as_posix() for path in (*java_files, *markers))
        if not self.config.java_enabled:
            plain_status, plain_reason = DISABLED, "Java profiles are disabled in configuration."
            compiler, source, version = None, "", ""
        elif not java_files:
            plain_status, plain_reason = NOT_DETECTED, "No visible Java source files were found."
            compiler, source, version = None, "", ""
        else:
            compiler, source = discover_java_compiler(
                self.config.java_home,
                self.workspace,
                self.protected_roots,
            )
            version, major = _java_version(compiler) if compiler else ("", None)
            target = self.config.java_target_version
            if not target.isdigit() or int(target) < 7:
                plain_status, plain_reason = INVALID_CONFIG, "Java target version must be an integer of 7 or greater."
            elif source == "config-invalid":
                plain_status, plain_reason = INVALID_CONFIG, "Configured Java home is invalid or inside a protected directory."
            elif compiler is None:
                plain_status, plain_reason = SETUP_REQUIRED, "Java sources were found, but no trusted javac executable is available."
            elif major is not None and int(target) > major:
                plain_status, plain_reason = INVALID_CONFIG, f"Java target {target} exceeds compiler version {major}."
            elif markers:
                plain_status, plain_reason = AMBIGUOUS, "Java build files were found. Select plain javac explicitly or configure that build system later."
            else:
                plain_status, plain_reason = READY, "Plain Java sources and a trusted compiler are available."

        detections = [ProfileDetection(
            "java.javac",
            self.workspace,
            plain_status,
            evidence,
            plain_reason,
            frozenset({"build", "lint", "typecheck"}),
            compiler,
            source,
            version,
            (*java_files, *markers),
        )]
        for marker in markers:
            relative = marker.relative_to(self.workspace).as_posix()
            profile_id = "java.maven" if marker.name == "pom.xml" else "java.gradle"
            detections.append(ProfileDetection(
                profile_id,
                marker.parent,
                DETECTED_UNSUPPORTED,
                (relative,),
                f"{marker.name} was detected, but project-controlled build execution is not enabled yet.",
                frozenset({"build", "test", "lint"}),
                inputs=(marker,),
            ))
        return tuple(detections)

    def _python_detections(self) -> tuple[ProfileDetection, ...]:
        python_files = self._visible_files("*.py")
        evidence = tuple(path.relative_to(self.workspace).as_posix() for path in python_files)
        if not self.config.python_enabled:
            status, reason = DISABLED, "Python profiles are disabled in configuration."
        elif self.config.python_interpreter != "current":
            status, reason = INVALID_CONFIG, "Only the controller's current Python interpreter is supported."
        elif not python_files:
            status, reason = NOT_DETECTED, "No visible Python source files were found."
        else:
            status, reason = READY, "Visible Python sources are available."
        executable = Path(sys.executable).resolve()
        compile_profile = ProfileDetection(
            "python.compile",
            self.workspace,
            status,
            evidence,
            reason,
            frozenset({"build", "lint"}),
            executable,
            "current-interpreter",
            f"Python {sys.version_info.major}.{sys.version_info.minor}",
            python_files,
        )
        test_profile = ProfileDetection(
            "python.unittest",
            self.workspace,
            status,
            evidence,
            reason,
            frozenset({"test"}),
            executable,
            "current-interpreter",
            f"Python {sys.version_info.major}.{sys.version_info.minor}",
            python_files,
        )
        return compile_profile, test_profile

    def _plan(self, detection: ProfileDetection, kind: str) -> CheckPlan:
        config_hash = _hash_json({"profile": detection.profile_id, "kind": kind, **self.config.to_json()})
        input_key = (detection.project_root, detection.inputs)
        input_hash = self._input_hashes.get(input_key)
        if input_hash is None:
            input_hash = _hash_inputs(detection.project_root, detection.inputs)
            self._input_hashes[input_key] = input_hash
        if detection.profile_id == "java.javac":
            assert detection.executable is not None
            _, major = _java_version(detection.executable)
            target = self.config.java_target_version
            target_args = ("--release", target) if major and major >= 9 else ("-source", target, "-target", target)
            lint_args = ("-Xlint:all",) if kind == "lint" else ()
            argv = (
                str(detection.executable), "-proc:none", *target_args, *lint_args,
                "-d", "<agent-temp>", "@<agent-argfile>",
            )
            description = f"Java {kind} with javac targeting Java {target} ({len(detection.inputs)} inputs)"
            return CheckPlan(
                detection.profile_id, kind, "subprocess", detection.project_root, argv,
                description, self.config.safe_profiles_auto, config_hash, input_hash, detection.inputs,
            )
        if detection.profile_id == "python.compile":
            return CheckPlan(
                detection.profile_id, kind, "python_compile", detection.project_root, (),
                f"Compile {len(detection.inputs)} visible Python files without executing them",
                self.config.safe_profiles_auto, config_hash, input_hash, detection.inputs,
            )
        if detection.profile_id == "python.unittest":
            assert detection.executable is not None
            return CheckPlan(
                detection.profile_id, kind, "subprocess", detection.project_root,
                (str(detection.executable), "-m", "unittest", "discover"),
                "Python unittest discovery", False, config_hash, input_hash, detection.inputs,
            )
        raise ValueError(f"Unsupported executable profile: {detection.profile_id}")
