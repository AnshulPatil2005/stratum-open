from stratum_open.repo.graph import Import, Importers, importers, parse_imports, reach_note


def write(root, files):
    for path, text in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    return str(root)


def test_python_absolute_and_relative_imports(tmp_path):
    repo = write(tmp_path, {
        "app/__init__.py": "",
        "app/auth.py": "def login(): ...\n",
        "app/routes.py": "from app.auth import login\n",
        "app/admin.py": "from .auth import login\n",
        "app/other.py": "from . import auth\n",
        "app/sub/__init__.py": "",
        "app/sub/deep.py": "from ..auth import login\n",
        "cli.py": "import os, app.auth as auth\n",
        "app/plain.py": "import app\n",
    })

    found = importers(repo, ["app/auth.py", "app/__init__.py", "README.md"])
    assert found["app/auth.py"] == Importers(5, ["app/admin.py", "app/other.py", "app/routes.py", "app/sub/deep.py", "cli.py"])
    # `from app.auth import login` loads app/auth.py, not the package's __init__
    assert found["app/__init__.py"] == Importers(1, ["app/plain.py"])
    assert found["README.md"] == Importers(0, [])
    assert importers(repo, ["app/auth.py"], limit=2)["app/auth.py"] == Importers(5, ["app/admin.py", "app/other.py"])


def test_python_package_roots(tmp_path):
    repo = write(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/core.py": "",
        "tests/test_core.py": "from pkg.core import thing\n",
        "backend/app/__init__.py": "",
        "backend/app/models.py": "",
        "backend/app/main.py": "from app.models import User\n",
        "backend/tests/test_models.py": "from app import models\n",
        "app/models.py": "",  # another `app`: code under backend/ means its own
        "scripts/seed.py": "from app.models import User\n",
    })

    found = importers(repo, ["src/pkg/core.py", "backend/app/models.py", "app/models.py"])
    assert found["src/pkg/core.py"].examples == ["tests/test_core.py"]
    assert found["backend/app/models.py"].examples == ["backend/app/main.py", "backend/tests/test_models.py"]
    assert found["app/models.py"].examples == ["scripts/seed.py"]


def test_js_and_ts_imports(tmp_path):
    repo = write(tmp_path, {
        "web/src/api/client.ts": "export const get = 1\n",
        "web/src/api/index.ts": "export * from './client'\n",
        "web/src/pages/home.tsx": "import { get } from '../api/client'\n",
        "web/src/pages/about.jsx": 'const { get } = require("../api/client")\n',
        "web/src/lazy.ts": "const api = await import('./api/client.js')\n",
        "web/src/multi.ts": "import {\n  get,\n  type Options,\n} from './api/client'\n",
        "web/src/side.ts": "import './api'\n",
        "web/src/commented.ts": "// import { get } from './api/client'\n/* require('./api/client') */\n",
        "web/src/fixture.ts": "const code = `import { get } from './api/client'`\n",
    })

    found = importers(repo, ["web/src/api/client.ts", "web/src/api/index.ts"])
    assert found["web/src/api/client.ts"] == Importers(5, [
        "web/src/api/index.ts", "web/src/lazy.ts", "web/src/multi.ts", "web/src/pages/about.jsx", "web/src/pages/home.tsx",
    ])
    assert found["web/src/api/index.ts"].examples == ["web/src/side.ts"]


def test_packages_from_outside_the_repo_are_ignored(tmp_path):
    repo = write(tmp_path, {
        "json.py": "",  # shadows the standard library: still not an edge
        "lib/__init__.py": "",
        "lib/requests.py": "",  # shares a name with a PyPI package
        "lib/client.py": "import requests\nimport json\nfrom os import path\n",
        "web/react.ts": "",
        "web/lodash/index.ts": "",
        "web/app.ts": "import React from 'react'\nimport fp from 'lodash/fp'\nimport x from '@/react'\nimport y from '/web/react'\n",
    })

    found = importers(repo, ["json.py", "lib/requests.py", "web/react.ts", "web/lodash/index.ts"])
    assert {path: f.count for path, f in found.items()} == dict.fromkeys(found, 0)


def test_files_with_the_same_name_are_not_mixed_up(tmp_path):
    repo = write(tmp_path, {
        "api/__init__.py": "",
        "api/utils.py": "",
        "api/views.py": "from . import utils\nfrom api.utils import helper\n",
        "api/jobs.py": "import utils\n",  # no root has a utils.py: missing beats wrong
        "db/__init__.py": "",
        "db/utils.py": "",
        "web/a/index.ts": "",
        "web/a/view.ts": "import x from './index'\n",
        "web/b/index.ts": "",
    })

    found = importers(repo, ["api/utils.py", "db/utils.py", "web/a/index.ts", "web/b/index.ts"])
    assert found["api/utils.py"] == Importers(1, ["api/views.py"])
    assert found["db/utils.py"].count == 0
    assert found["web/a/index.ts"] == Importers(1, ["web/a/view.ts"])
    assert found["web/b/index.ts"].count == 0


def test_vendored_folders_and_big_files_are_skipped(tmp_path):
    repo = write(tmp_path, {
        "src/core.js": "",
        "src/small.js": "require('./core')\n",
        "src/big.js": "require('./core')\n" + "// padding\n" * 100_000,
        "node_modules/pkg/index.js": "require('../../src/core')\n",
        "dist/bundle.js": "require('../src/core')\n",
        ".cache/x.js": "require('../src/core')\n",
    })

    assert importers(repo, ["src/core.js"])["src/core.js"] == Importers(1, ["src/small.js"])


def test_docstrings_and_comments_are_not_imports():
    text = (
        '"""Usage::\n\n    from app.auth import login\n"""\n'
        "# import app.hidden\n"
        "from app import (\n    auth,  # the login code\n    db,\n)\n"
        "from ..models import User as U\n"
    )
    assert parse_imports("x.py", text) == [Import(6, "app", ("auth", "db")), Import(10, "models", ("User",), 2)]
    assert parse_imports("notes.md", "import app\n") == []


def test_reach_note_names_files_with_three_or_more_importers():
    notes = reach_note({
        "api/util.py": Importers(2, ["a.py", "b.py"]),
        "db/models.py": Importers(3, ["a.py", "b.py", "c.py"]),
        "api/auth.py": Importers(23, ["api/admin.py", "api/routes.py", "cli/main.py", "x.py", "y.py"]),
    })
    assert notes == [
        "`api/auth.py` is imported by 23 files (e.g. `api/admin.py`, `api/routes.py`, `cli/main.py`).",
        "`db/models.py` is imported by 3 files (e.g. `a.py`, `b.py`, `c.py`).",
    ]
