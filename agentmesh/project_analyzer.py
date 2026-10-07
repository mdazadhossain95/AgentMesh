"""Repository analysis. Facts come from files on disk; nothing is assumed (no default state manager etc.)."""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import gitutil

IGNORED = {".git", "node_modules", "build", ".dart_tool", ".gradle", "Pods", "venv", ".venv", "__pycache__",
           "dist", "target", ".idea", ".agentmesh", ".agentmesh-worktrees", "vendor", ".next", "coverage",
           ".pub-cache", "ephemeral", ".symlinks", ".pytest_cache", ".mypy_cache", ".ruff_cache", "DerivedData"}

FLUTTER_STATE = {"flutter_riverpod": "riverpod", "riverpod": "riverpod", "hooks_riverpod": "riverpod",
                 "flutter_bloc": "bloc", "bloc": "bloc", "provider": "provider", "get": "getx", "getx": "getx",
                 "mobx": "mobx", "flutter_mobx": "mobx", "redux": "redux", "flutter_redux": "redux",
                 "signals": "signals", "stacked": "stacked", "get_it": "get_it (DI)", "injectable": "injectable (DI)"}
FLUTTER_ROUTING = {"go_router": "go_router", "auto_route": "auto_route", "beamer": "beamer",
                   "routemaster": "routemaster", "get": "getx routing", "fluro": "fluro"}
FLUTTER_NET = {"dio": "dio", "http": "http", "retrofit": "retrofit", "chopper": "chopper", "graphql_flutter": "graphql"}
FLUTTER_STORAGE = {"hive": "hive", "hive_flutter": "hive", "drift": "drift", "sqflite": "sqflite", "isar": "isar",
                   "shared_preferences": "shared_preferences", "floor": "floor", "objectbox": "objectbox",
                   "flutter_secure_storage": "flutter_secure_storage", "firebase_core": "firebase"}
FLUTTER_CODEGEN = {"freezed": "freezed", "json_serializable": "json_serializable", "build_runner": "build_runner"}
NODE_WEB = {"react": "react", "next": "next", "vue": "vue", "nuxt": "nuxt", "svelte": "svelte",
            "@sveltejs/kit": "sveltekit", "@angular/core": "angular", "solid-js": "solid", "astro": "astro",
            "vite": "vite", "remix": "remix"}
NODE_MOBILE = {"react-native": "react-native", "expo": "expo"}
NODE_BACKEND = {"express": "express", "@nestjs/core": "nestjs", "fastify": "fastify", "koa": "koa", "hono": "hono",
                "@hapi/hapi": "hapi", "apollo-server": "apollo", "graphql-yoga": "graphql-yoga", "trpc": "trpc",
                "@trpc/server": "trpc"}
NODE_DB = {"prisma": "prisma", "@prisma/client": "prisma", "typeorm": "typeorm", "sequelize": "sequelize",
           "mongoose": "mongoose", "drizzle-orm": "drizzle", "knex": "knex", "pg": "postgres", "mysql2": "mysql"}
NODE_TEST = {"jest": "jest", "vitest": "vitest", "mocha": "mocha", "@playwright/test": "playwright",
             "cypress": "cypress", "ava": "ava"}
PY_BACKEND = {"django": "django", "fastapi": "fastapi", "flask": "flask", "starlette": "starlette",
              "aiohttp": "aiohttp", "tornado": "tornado", "sanic": "sanic", "litestar": "litestar"}
PY_DB = {"sqlalchemy": "sqlalchemy", "alembic": "alembic", "psycopg2": "postgres", "psycopg": "postgres",
         "pymongo": "mongodb", "peewee": "peewee", "tortoise-orm": "tortoise", "sqlmodel": "sqlmodel"}
GO_BACKEND = {"gin-gonic/gin": "gin", "labstack/echo": "echo", "gofiber/fiber": "fiber", "go-chi/chi": "chi",
              "gorilla/mux": "gorilla"}
RUST_BACKEND = {"axum": "axum", "actix-web": "actix", "rocket": "rocket", "warp": "warp"}
WORKSPACE_FILES = ("pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json", "melos.yaml", "go.work", "rush.json")
CI_FILES = (".gitlab-ci.yml", ".circleci/config.yml", "bitrise.yml", "codemagic.yaml", "Jenkinsfile",
            "azure-pipelines.yml", ".travis.yml")
BACKEND_DIRS = ("backend", "server", "api", "services", "functions", "cloud_functions")
WEB_DIRS = ("web", "frontend", "client", "admin", "dashboard", "website")
MOBILE_DIRS = ("app", "mobile")
SENSITIVE = {
    "security": ("auth", "oauth", "login", "session", "token", "crypto", "encrypt", "biometric", "keystore",
                 "jwt", "password", "secure", "permission"),
    "financial": ("payment", "billing", "checkout", "wallet", "ledger", "invoice", "transaction", "stripe",
                  "bank", "payout", "subscription"),
    "compliance": ("kyc", "aml", "hipaa", "gdpr", "pci", "compliance", "consent"),
}
SENSITIVE_DEPS = {
    "security": {"flutter_secure_storage", "local_auth", "firebase_auth", "passport", "jsonwebtoken", "bcrypt",
                 "pyjwt", "authlib", "argon2-cffi", "oauthlib"},
    "financial": {"stripe", "flutter_stripe", "in_app_purchase", "purchases_flutter", "braintree", "razorpay",
                  "stripe-python", "paypal"},
    "compliance": set(),
}


@dataclass
class Component:
    path: str                      # relative to repo root, "." for root
    type: str                      # mobile | web | backend | library | unknown
    stack: str                     # flutter | dart | node | python | go | rust | java | ...
    frameworks: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    verification: list[dict[str, str]] = field(default_factory=list)   # {name, cwd, run}
    inferred: bool = False         # from a directory name only, no manifest


@dataclass
class Question:
    id: str
    text: str
    choices: list[str]
    default: str


@dataclass
class ProjectProfile:
    root: str
    name: str
    is_git: bool
    kind: str = "generic"
    components: list[Component] = field(default_factory=list)
    workspace_markers: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    ci: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    contracts: list[str] = field(default_factory=list)
    migrations: list[str] = field(default_factory=list)
    localization: list[str] = field(default_factory=list)
    sensitive: dict[str, list[str]] = field(default_factory=dict)
    instruction_files: dict[str, bool] = field(default_factory=dict)
    security_review: bool = False
    compliance_review: bool = False
    threat_review: bool = False
    questions: list[Question] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def of_type(self, t: str) -> list[Component]:
        return [c for c in self.components if c.type == t]

    @property
    def has_mobile(self) -> bool: return bool(self.of_type("mobile"))
    @property
    def has_web(self) -> bool: return bool(self.of_type("web"))
    @property
    def has_backend(self) -> bool: return bool(self.of_type("backend"))
    @property
    def has_flutter(self) -> bool: return any(c.stack == "flutter" for c in self.components)


def _has_human_content(p: Path) -> bool:
    """True if the file has text outside AgentMesh's managed block (so our own output never counts as human input)."""
    if not p.is_file():
        return False
    text = re.sub(r"<!-- AGENTMESH:BEGIN.*?<!-- AGENTMESH:END -->", "", p.read_text(encoding="utf-8", errors="replace"), flags=re.S)
    return bool(text.strip())


# ---------------------------------------------------------------- walking

def _walk(root: Path, max_depth: int, max_entries: int = 40000):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = len(rel.parts)
        dirnames[:] = [d for d in dirnames if d not in IGNORED and not (d.startswith(".") and d not in (".github", ".circleci"))]
        if depth >= max_depth:
            dirnames[:] = []
        n += len(filenames) + len(dirnames)
        if n > max_entries:
            return
        yield rel, dirnames, filenames


def _rel(p: Path) -> str:
    s = p.as_posix()
    return "." if s in ("", ".") else s


def _read_json(p: Path) -> dict[str, Any]:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_yaml(p: Path) -> dict[str, Any]:
    try:
        v = yaml.safe_load(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def _hits(deps: set[str], table: dict[str, str]) -> list[str]:
    return sorted({v for k, v in table.items() if k in deps})


# ---------------------------------------------------------------- per-stack inspectors

def _flutter(root: Path, rel: Path, data: dict[str, Any]) -> Component:
    base = root / rel
    deps = set((data.get("dependencies") or {}).keys()) | set((data.get("dev_dependencies") or {}).keys())
    is_flutter = "flutter" in (data.get("dependencies") or {}) or "flutter" in data
    backend_pkgs = {"shelf", "dart_frog", "serverpod", "conduit"}
    if not is_flutter:
        typ = "backend" if deps & backend_pkgs else "library"
        comp = Component(_rel(rel), typ, "dart", sorted(deps & backend_pkgs))
        comp.verification = [{"name": "analyze", "cwd": _rel(rel), "run": "dart analyze"}]
        if (base / "test").is_dir():
            comp.verification.append({"name": "test", "cwd": _rel(rel), "run": "dart test"})
        return comp
    platforms = [p for p in ("android", "ios", "web", "macos", "linux", "windows") if (base / p).is_dir()]
    is_plugin = "plugin" in (data.get("flutter") or {}) if isinstance(data.get("flutter"), dict) else False
    typ = "mobile" if ({"android", "ios"} & set(platforms) or not platforms) and not is_plugin else (
        "web" if platforms == ["web"] else "library" if is_plugin else "mobile")
    details: dict[str, Any] = {
        "platforms": platforms,
        "state_management": _hits(deps, FLUTTER_STATE) or ["none detected (inspect lib/ before assuming)"],
        "routing": _hits(deps, FLUTTER_ROUTING) or ["navigator/none detected"],
        "networking": _hits(deps, FLUTTER_NET),
        "storage": _hits(deps, FLUTTER_STORAGE),
        "codegen": _hits(deps, FLUTTER_CODEGEN),
        "sdk_constraint": (data.get("environment") or {}).get("sdk"),
        "has_l10n": (base / "l10n.yaml").is_file() or "flutter_localizations" in (data.get("dependencies") or {}),
        "top_level_lib_dirs": sorted(d.name for d in (base / "lib").iterdir() if d.is_dir())[:20] if (base / "lib").is_dir() else [],
    }
    comp = Component(_rel(rel), typ, "flutter", ["flutter"], details)
    dirs = [d for d in ("lib", "test") if (base / d).is_dir()]
    cwd = _rel(rel)
    comp.verification = [
        {"name": "format", "cwd": cwd, "run": f"dart format --output=none --set-exit-if-changed {' '.join(dirs) or '.'}"},
        {"name": "analyze", "cwd": cwd, "run": "flutter analyze"},
    ]
    if (base / "test").is_dir() and any((base / "test").rglob("*_test.dart")):
        comp.verification.append({"name": "test", "cwd": cwd, "run": "flutter test"})
    if (base / "integration_test").is_dir():
        details["integration_tests"] = "integration_test/ exists (needs a device/emulator; not in default verification)"
    return comp


def _node(root: Path, rel: Path, pkg: dict[str, Any]) -> Component:
    base = root / rel
    deps = set((pkg.get("dependencies") or {}).keys()) | set((pkg.get("devDependencies") or {}).keys())
    mobile, web, backend = _hits(deps, NODE_MOBILE), _hits(deps, NODE_WEB), _hits(deps, NODE_BACKEND)
    typ = "mobile" if mobile else "backend" if backend and not (set(web) - {"vite"}) else "web" if web else (
        "backend" if backend else "library")
    pm = next((n for f, n in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
                              ("package-lock.json", "npm")) if (base / f).is_file() or (root / f).is_file()), "npm")
    details = {"package_manager": pm, "typescript": "typescript" in deps or (base / "tsconfig.json").is_file(),
               "test_runners": _hits(deps, NODE_TEST), "database": _hits(deps, NODE_DB),
               "scripts": sorted((pkg.get("scripts") or {}).keys())}
    comp = Component(_rel(rel), typ, "node", mobile + web + backend, details)
    run = "npm run" if pm == "npm" else f"{pm} run"
    scripts = pkg.get("scripts") or {}
    for want in ("lint", "typecheck", "type-check", "test", "build"):
        if want in scripts:
            comp.verification.append({"name": want, "cwd": _rel(rel), "run": f"{run} {want}" if want != "test" or pm != "npm" else "npm test"})
    return comp


def _python(root: Path, rel: Path) -> Component | None:
    base = root / rel
    deps: set[str] = set()
    pyproject: dict[str, Any] = {}
    if (base / "pyproject.toml").is_file():
        try:
            pyproject = tomllib.loads((base / "pyproject.toml").read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            pyproject = {}
        proj = pyproject.get("project", {})
        raw = list(proj.get("dependencies", []))
        for group in (proj.get("optional-dependencies") or {}).values():
            raw += group
        raw += list((pyproject.get("tool", {}).get("poetry", {}).get("dependencies") or {}).keys())
        deps |= {re.split(r"[<>=!~\[; ]", d.strip().lower(), maxsplit=1)[0] for d in raw if isinstance(d, str)}
    for req in base.glob("requirements*.txt"):
        for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lower()
            if line and not line.startswith(("#", "-")):
                deps.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0])
    if not deps and not pyproject:
        return None
    fw = _hits(deps, PY_BACKEND)
    comp = Component(_rel(rel), "backend" if fw else "library", "python", fw,
                     {"database": _hits(deps, PY_DB), "has_pytest": "pytest" in deps or (base / "tests").is_dir(),
                      "migrations_tool": "alembic" if "alembic" in deps else "django" if "django" in deps else None})
    tool = pyproject.get("tool", {})
    if "ruff" in tool or (base / "ruff.toml").is_file():
        comp.verification.append({"name": "lint", "cwd": _rel(rel), "run": "ruff check ."})
    if "mypy" in tool or (base / "mypy.ini").is_file():
        comp.verification.append({"name": "typecheck", "cwd": _rel(rel), "run": "mypy ."})
    if comp.details["has_pytest"]:
        comp.verification.append({"name": "test", "cwd": _rel(rel), "run": "pytest"})
    return comp


def _simple(root: Path, rel: Path, manifest: str) -> Component | None:
    base = root / rel
    text = (base / manifest).read_text(encoding="utf-8", errors="replace")
    c = _rel(rel)
    if manifest == "go.mod":
        fw = [v for k, v in GO_BACKEND.items() if k in text]
        return Component(c, "backend" if fw else "library", "go", fw, {}, [
            {"name": "vet", "cwd": c, "run": "go vet ./..."}, {"name": "test", "cwd": c, "run": "go test ./..."}])
    if manifest == "Cargo.toml":
        fw = [v for k, v in RUST_BACKEND.items() if re.search(rf"^{k}\s*=", text, re.M)]
        return Component(c, "backend" if fw else "library", "rust", fw, {}, [
            {"name": "check", "cwd": c, "run": "cargo check"}, {"name": "test", "cwd": c, "run": "cargo test"}])
    if manifest in ("pom.xml", "build.gradle", "build.gradle.kts"):
        spring = "spring-boot" in text or "org.springframework" in text
        android = "com.android" in text
        runner = "./gradlew" if manifest != "pom.xml" and (base / "gradlew").is_file() else "mvn" if manifest == "pom.xml" else "gradle"
        return Component(c, "mobile" if android else "backend" if spring else "library", "java/kotlin",
                         ["spring"] if spring else [], {}, [{"name": "test", "cwd": c, "run": f"{runner} test"}])
    return None


# ---------------------------------------------------------------- main entry

def analyze(root: Path) -> ProjectProfile:
    root = root.resolve()
    prof = ProjectProfile(root=str(root), name=root.name, is_git=gitutil.repo_root(root) is not None)
    manifests = {"pubspec.yaml", "package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml",
                 "pom.xml", "build.gradle", "build.gradle.kts"}
    hint_dirs: dict[str, str] = {}
    seen_py: set[str] = set()
    for rel, dirnames, files in _walk(root, 3):
        names = set(files)
        if rel == Path("."):
            for d in dirnames:
                for hints, typ in ((BACKEND_DIRS, "backend"), (WEB_DIRS, "web"), (MOBILE_DIRS, "mobile")):
                    if d.lower() in hints:
                        hint_dirs[d] = typ
            prof.workspace_markers += [f for f in WORKSPACE_FILES if f in names]
            for f in names:
                if re.match(r"(README|ARCHITECTURE|SPEC|PRD|DESIGN|CONTRIBUTING)", f, re.I):
                    prof.docs.append(f)
            prof.instruction_files = {f: _has_human_content(root / f) for f in ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "QWEN.md")}
        for f in names:
            full = rel / f
            if re.match(r"(openapi|swagger).*\.(ya?ml|json)$", f, re.I) or f.endswith(".proto") or f.endswith(".graphql") or f == "schema.graphql":
                prof.contracts.append(full.as_posix())
            if f == "l10n.yaml" or f.endswith(".arb"):
                loc = rel.as_posix()
                if loc not in prof.localization:
                    prof.localization.append(loc if f != "l10n.yaml" else (full.as_posix()))
            if f in ("prisma.schema",) or f == "schema.prisma" or f in ("firestore.rules", "firebase.json"):
                prof.migrations.append(full.as_posix())
        for d in dirnames:
            full = rel / d
            low = d.lower()
            if low in ("docs", "doc", "specs", "adr", "adrs", "requirements"):
                prof.docs.append(full.as_posix() + "/")
            if low in ("test", "tests", "__tests__", "spec", "integration_test", "e2e"):
                prof.test_dirs.append(full.as_posix() + "/")
            if low in ("migrations", "alembic", "migrate") or (low == "db" and (root / full / "migrate").exists()):
                prof.migrations.append(full.as_posix() + "/")
        mf = manifests & names
        if "pubspec.yaml" in mf:
            prof.components.append(_flutter(root, rel, _read_yaml(root / rel / "pubspec.yaml")))
        if "package.json" in mf:
            pkg = _read_json(root / rel / "package.json")
            if rel == Path(".") and pkg.get("workspaces"):
                prof.workspace_markers.append("package.json#workspaces")
            if pkg.get("dependencies") or pkg.get("devDependencies") or pkg.get("scripts"):
                prof.components.append(_node(root, rel, pkg))
        if mf & {"pyproject.toml", "requirements.txt"} and rel.as_posix() not in seen_py:
            seen_py.add(rel.as_posix())
            if (c := _python(root, rel)):
                prof.components.append(c)
        for m in ("go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts"):
            if m in mf and not (m.startswith("build.gradle") and (root / rel / "pubspec.yaml").is_file()) \
                    and rel.parts[:1] not in (("android",), ("ios",)):
                if (c := _simple(root, rel, m)):
                    prof.components.append(c)
                break
    # CI
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        prof.ci += [f".github/workflows/{p.name}" for p in sorted(wf.glob("*.y*ml"))]
    prof.ci += [f for f in CI_FILES if (root / f).exists()]
    # directory-name-only components (e.g. backend/ with an unrecognized stack)
    covered = {c.path.split("/")[0] for c in prof.components}
    for d, typ in hint_dirs.items():
        if d not in covered and typ in ("backend", "web") and any((root / d).iterdir()):
            prof.components.append(Component(d, typ, "unknown", [], {"note": "inferred from directory name"}, [], True))
    # dedupe android/ios gradle noise inside flutter projects
    prof.components = [c for c in prof.components if not (c.stack == "java/kotlin" and
                       any(f.stack == "flutter" and c.path.startswith(f.path.rstrip(".") + "android") for f in prof.components))]
    prof.docs = sorted(set(prof.docs))[:15]
    prof.contracts, prof.migrations = sorted(set(prof.contracts))[:10], sorted(set(prof.migrations))[:10]
    prof.test_dirs = sorted(set(prof.test_dirs))[:10]
    _sensitivity(root, prof)
    _classify(prof, hint_dirs)
    return prof


def _sensitivity(root: Path, prof: ProjectProfile) -> None:
    found: dict[str, set[str]] = {k: set() for k in SENSITIVE}
    for rel, dirnames, files in _walk(root, 6):
        for name in dirnames + [Path(f).stem for f in files if f.endswith((".dart", ".py", ".ts", ".js", ".go", ".kt", ".java", ".swift", ".rs"))]:
            low = name.lower()
            for group, words in SENSITIVE.items():
                if any(re.search(rf"(^|[_\-.]){w}(s|ing|ion)?($|[_\-.])", low) for w in words):
                    found[group].add((rel / name).as_posix())
    deps: set[str] = set()
    for c in prof.components:
        base = root / c.path
        for mf in ("pubspec.yaml", "package.json", "requirements.txt", "pyproject.toml"):
            if (base / mf).is_file():
                deps |= {w.lower() for w in re.findall(r"[A-Za-z0-9_.@/-]+", (base / mf).read_text(encoding="utf-8", errors="replace"))}
    for group, names in SENSITIVE_DEPS.items():
        for dep in names & deps:
            found[group].add(f"dependency:{dep}")
    prof.sensitive = {g: sorted(v)[:8] for g, v in found.items() if v}
    sec, fin, comp = (len(found[g]) for g in ("security", "financial", "compliance"))
    prof.security_review = sec >= 2 or fin >= 1 or comp >= 1
    prof.compliance_review = fin >= 2 or comp >= 1
    prof.threat_review = fin >= 2 and prof.has_backend


def _classify(prof: ProjectProfile, hint_dirs: dict[str, str]) -> None:
    comps = [c for c in prof.components if c.type != "library" or c.stack in ("flutter",)]
    n_top = len({c.path.split("/")[0] for c in prof.components})
    front = prof.has_mobile or prof.has_web
    if len(prof.components) >= 3 and (prof.workspace_markers or n_top >= 3):
        prof.kind = "monorepo"
    elif prof.workspace_markers and len(prof.components) >= 2:
        prof.kind = "monorepo"
    elif front and prof.has_backend:
        prof.kind = "full-stack"
    elif prof.has_mobile:
        prof.kind = "flutter" if prof.has_flutter else "mobile"
    elif prof.has_web:
        prof.kind = "web"
    elif prof.has_backend:
        prof.kind = "backend"
    else:
        prof.kind = "generic"
    # --- questions: only what files could not settle
    unknown_back = [c for c in prof.components if c.inferred and c.type == "backend"]
    if unknown_back:
        prof.questions.append(Question(
            "backend_dir", f"Directory '{unknown_back[0].path}' looks like a backend but its stack is unrecognised. Is it the backend?",
            ["yes", "no"], "yes"))
    if prof.kind == "generic" and not prof.components:
        prof.questions.append(Question(
            "project_kind", "No known manifest found. What kind of project is this?",
            ["generic", "flutter", "web", "backend", "full-stack", "monorepo"], "generic"))
    sec = prof.sensitive.get("security", [])
    if len(sec) == 1 and not prof.sensitive.get("financial") and not prof.sensitive.get("compliance"):
        prof.questions.append(Question(
            "security_review", "One security-related module was found. Add a security-reviewer role?", ["yes", "no"], "no"))
    if len(prof.of_type("backend")) > 1:
        prof.questions.append(Question(
            "primary_backend", "Multiple backends found. Which one owns the API contract?",
            [c.path for c in prof.of_type("backend")], prof.of_type("backend")[0].path))


def apply_answers(prof: ProjectProfile, answers: dict[str, str]) -> None:
    """Fold answers to `prof.questions` back into the profile."""
    if answers.get("backend_dir") == "no":
        prof.components = [c for c in prof.components if not (c.inferred and c.type == "backend")]
        saved, prof.questions = prof.questions, []
        _classify(prof, {})
        prof.questions = saved
    if "project_kind" in answers:
        prof.kind = answers["project_kind"]
    if answers.get("security_review") == "yes":
        prof.security_review = True
    if answers.get("primary_backend"):
        prof.assumptions.append(f"primary backend (API contract owner): {answers['primary_backend']}")
