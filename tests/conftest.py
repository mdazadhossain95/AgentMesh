from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
    assert r.returncode == 0, r.stderr
    return r.stdout


def commit_all(root: Path, msg: str = "c") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", msg)


def _system_dirs() -> list[str]:
    if os.name != "nt":
        return ["/usr/bin", "/bin"]
    git = shutil.which("git")
    return [d for d in (os.path.dirname(git) if git else "", os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")) if d]


def restricted_path(first) -> str:
    """PATH with `first` ahead of only the system dirs (and git): hides real coding CLIs from discovery."""
    return os.pathsep.join([str(first), *_system_dirs()])


def make_script(bindir: Path, name: str, body: str) -> Path:
    if os.name == "nt":
        pytest.skip("shell-script fake CLIs need a POSIX shell")
    bindir.mkdir(parents=True, exist_ok=True)
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return p


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Isolated AgentMesh home; no real CLI or mock leaks into tests."""
    monkeypatch.setenv("AGENTMESH_HOME", str(tmp_path / "amhome"))
    monkeypatch.delenv("AGENTMESH_MOCK", raising=False)
    monkeypatch.delenv("AGENTMESH_MOCK_WRITE", raising=False)
    monkeypatch.delenv("AGENTMESH_DEPTH", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    return tmp_path / "amhome"


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "README.md").write_text("# proj\n")
    commit_all(root, "init")
    return root


def write(root: Path, rel: str, text: str = "") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


PUBSPEC = """name: govio
environment:
  sdk: ^3.4.0
dependencies:
  flutter:
    sdk: flutter
  flutter_localizations:
    sdk: flutter
  flutter_riverpod: ^2.5.0
  go_router: ^14.0.0
  flutter_secure_storage: ^9.0.0
dev_dependencies:
  flutter_test:
    sdk: flutter
flutter:
  uses-material-design: true
"""


@pytest.fixture
def flutter_only(tmp_path) -> Path:
    root = tmp_path / "fl"
    write(root, "pubspec.yaml", PUBSPEC.replace("  flutter_secure_storage: ^9.0.0\n", ""))
    write(root, "lib/main.dart", "void main(){}")
    write(root, "lib/features/home/home.dart", "")
    write(root, "test/a_test.dart", "")
    (root / "android").mkdir(); (root / "ios").mkdir()
    write(root, "l10n.yaml", "arb-dir: lib/l10n\n")
    git(root, "init", "-q", "-b", "main")
    commit_all(root)
    return root


@pytest.fixture
def flutter_backend(tmp_path) -> Path:
    root = tmp_path / "govio"
    write(root, "pubspec.yaml", PUBSPEC)
    write(root, "lib/main.dart", "void main(){}")
    write(root, "lib/features/auth/login.dart", "")
    write(root, "test/a_test.dart", "")
    (root / "android").mkdir(); (root / "ios").mkdir()
    write(root, "backend/requirements.txt", "fastapi\nsqlalchemy\nalembic\npytest\n")
    write(root, "backend/tests/test_x.py", "")
    write(root, "docs/openapi.yaml", "openapi: 3.0.0\n")
    write(root, "CLAUDE.md", "# Human rules\nBe careful.\n")
    git(root, "init", "-q", "-b", "main")
    commit_all(root)
    return root


@pytest.fixture
def android_native(tmp_path) -> Path:
    """Standalone native Android app (no Flutter): root build.gradle + app/build.gradle with the Android plugin."""
    root = tmp_path / "androidapp"
    write(root, "build.gradle", "allprojects {\n    repositories { google(); mavenCentral() }\n}\n")
    write(root, "settings.gradle", "include ':app'\n")
    write(root, "gradlew", "#!/bin/sh\necho gradle\n")
    write(root, "app/build.gradle",
          "plugins {\n    id 'com.android.application'\n    id 'org.jetbrains.kotlin.android'\n}\n"
          "dependencies {\n    implementation 'androidx.core:core-ktx:1.13.0'\n}\n")
    write(root, "app/src/main/java/com/example/app/MainActivity.kt", "class MainActivity\n")
    write(root, "app/src/test/java/com/example/app/ExampleTest.kt", "class ExampleTest\n")
    git(root, "init", "-q", "-b", "main")
    commit_all(root)
    return root


@pytest.fixture
def react_web(tmp_path) -> Path:
    """React + Vite single-page app, no backend."""
    root = tmp_path / "reactapp"
    write(root, "package.json", json.dumps({
        "name": "reactapp", "private": True,
        "scripts": {"dev": "vite", "build": "vite build", "test": "vitest"},
        "dependencies": {"react": "^18.3.0", "react-dom": "^18.3.0"},
        "devDependencies": {"vite": "^5.4.0", "vitest": "^2.1.0", "typescript": "^5.6.0"},
    }))
    write(root, "tsconfig.json", "{}\n")
    write(root, "src/App.tsx", "export default function App() { return null }\n")
    write(root, "src/App.test.tsx", "")
    git(root, "init", "-q", "-b", "main")
    commit_all(root)
    return root


@pytest.fixture
def node_backend(tmp_path) -> Path:
    """Express API, no frontend."""
    root = tmp_path / "nodeapi"
    write(root, "package.json", json.dumps({
        "name": "nodeapi", "private": True,
        "scripts": {"start": "node src/server.js", "test": "jest"},
        "dependencies": {"express": "^4.21.0", "pg": "^8.13.0"},
        "devDependencies": {"jest": "^29.7.0"},
    }))
    write(root, "src/server.js", "const express = require('express');\n")
    write(root, "src/server.test.js", "")
    git(root, "init", "-q", "-b", "main")
    commit_all(root)
    return root


@pytest.fixture
def ios_native(tmp_path) -> Path:
    """Standalone native iOS app (Xcode project + CocoaPods, no Flutter/React Native).

    AgentMesh's analyzer has no detector for Podfile/.xcodeproj today: this fixture
    documents that gap (see test_multi_stack.py) rather than hiding it.
    """
    root = tmp_path / "iosapp"
    write(root, "Podfile", "platform :ios, '16.0'\ntarget 'iosapp' do\nend\n")
    (root / "iosapp.xcodeproj").mkdir(parents=True)
    write(root, "iosapp.xcodeproj/project.pbxproj", "// Xcode project\n")
    write(root, "iosapp/AppDelegate.swift", "import UIKit\n")
    write(root, "iosappTests/AppDelegateTests.swift", "import XCTest\n")
    git(root, "init", "-q", "-b", "main")
    commit_all(root)
    return root
