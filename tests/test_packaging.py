"""Tests for PyPI packaging configuration.

Validates that pyproject.toml, the build script, and the GitHub Actions
release workflow are correctly structured for `pip install agent-baton`.
"""

import os
import stat
import sys
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_pyproject() -> dict:
    path = os.path.join(REPO_ROOT, "pyproject.toml")
    with open(path, "rb") as f:
        return tomllib.load(f)


def _load_workflow() -> dict:
    path = os.path.join(REPO_ROOT, ".github", "workflows", "release-pypi.yml")
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# pyproject.toml metadata
# ---------------------------------------------------------------------------

class TestPyprojectRequiredMetadata:
    def test_name(self):
        data = _load_pyproject()
        assert data["project"]["name"] == "agent-baton"

    def test_version_present(self):
        data = _load_pyproject()
        assert data["project"]["version"]

    def test_description_present(self):
        data = _load_pyproject()
        assert data["project"]["description"]

    def test_requires_python(self):
        data = _load_pyproject()
        rp = data["project"]["requires-python"]
        assert rp.startswith(">=3.")

    def test_license_declared(self):
        data = _load_pyproject()
        # Accept either string or table form
        lic = data["project"].get("license")
        assert lic is not None, "license must be declared in [project]"

    def test_authors_present(self):
        data = _load_pyproject()
        authors = data["project"].get("authors", [])
        assert len(authors) > 0, "At least one author must be declared"
        for author in authors:
            assert "name" in author or "email" in author

    def test_dependencies_present(self):
        data = _load_pyproject()
        deps = data["project"].get("dependencies", [])
        assert isinstance(deps, list)

    def test_project_urls(self):
        data = _load_pyproject()
        urls = data["project"].get("urls", {})
        assert "Homepage" in urls or "Repository" in urls

    def test_entry_point_baton(self):
        data = _load_pyproject()
        scripts = data["project"].get("scripts", {})
        assert "baton" in scripts, "'baton' entry point must be declared"
        assert "agent_baton" in scripts["baton"], "baton entry point must reference agent_baton package"

    def test_build_system_declared(self):
        data = _load_pyproject()
        bs = data.get("build-system", {})
        assert "build-backend" in bs
        assert bs["requires"]

    def test_optional_dev_extras(self):
        data = _load_pyproject()
        extras = data["project"].get("optional-dependencies", {})
        assert "dev" in extras, "'dev' extras group required"
        assert any("pytest" in dep for dep in extras["dev"])

    def test_optional_pmo_extras(self):
        data = _load_pyproject()
        extras = data["project"].get("optional-dependencies", {})
        assert "pmo" in extras, "'pmo' extras group required for FastAPI deps"
        pmo_deps = extras["pmo"]
        dep_names = [d.split(">=")[0].split("[")[0] for d in pmo_deps]
        assert "fastapi" in dep_names
        assert "uvicorn" in dep_names

    def test_optional_daemon_extras(self):
        data = _load_pyproject()
        extras = data["project"].get("optional-dependencies", {})
        assert "daemon" in extras, "'daemon' extras group required"

    def test_optional_classify_extras(self):
        data = _load_pyproject()
        extras = data["project"].get("optional-dependencies", {})
        assert "classify" in extras, "'classify' extras group required"
        assert any("anthropic" in dep for dep in extras["classify"])


# ---------------------------------------------------------------------------
# Entry point resolvability
# ---------------------------------------------------------------------------

class TestEntryPointResolves:
    def test_entry_point_module_importable(self):
        """The baton entry point module must be importable."""
        import importlib
        # Entry point is agent_baton.cli.main:main
        mod = importlib.import_module("agent_baton.cli.main")
        assert hasattr(mod, "main"), "agent_baton.cli.main must expose a 'main' callable"
        assert callable(mod.main)

    def test_entry_point_string_format(self):
        data = _load_pyproject()
        ep = data["project"]["scripts"]["baton"]
        assert ":" in ep, "Entry point must be in 'module:callable' format"
        module_part, callable_part = ep.split(":", 1)
        assert module_part == "agent_baton.cli.main"
        assert callable_part == "main"


# ---------------------------------------------------------------------------
# Build script
# ---------------------------------------------------------------------------

class TestBuildDistScript:
    def _script_path(self):
        return os.path.join(REPO_ROOT, "scripts", "build_dist.sh")

    def test_script_exists(self):
        assert os.path.isfile(self._script_path()), "scripts/build_dist.sh must exist"

    def test_script_is_executable(self):
        path = self._script_path()
        mode = os.stat(path).st_mode
        assert mode & stat.S_IXUSR, "scripts/build_dist.sh must be executable (user)"

    def test_script_contains_build_command(self):
        with open(self._script_path()) as f:
            content = f.read()
        assert "python3 -m build" in content

    def test_script_contains_twine(self):
        with open(self._script_path()) as f:
            content = f.read()
        assert "twine" in content

    def test_script_has_set_euo(self):
        with open(self._script_path()) as f:
            content = f.read()
        assert "set -euo pipefail" in content, "build script must use set -euo pipefail"

    def test_script_cleans_dist(self):
        with open(self._script_path()) as f:
            content = f.read()
        assert "rm -rf dist/" in content, "build script must clean dist/ before building"


# ---------------------------------------------------------------------------
# GitHub Actions workflow
# ---------------------------------------------------------------------------

class TestWorkflowYaml:
    def test_workflow_file_exists(self):
        path = os.path.join(REPO_ROOT, ".github", "workflows", "release-pypi.yml")
        assert os.path.isfile(path)

    def test_workflow_yaml_is_valid(self):
        wf = _load_workflow()
        assert isinstance(wf, dict)
        assert "jobs" in wf

    def test_workflow_triggers_on_version_tag(self):
        wf = _load_workflow()
        # PyYAML parses the bare `on:` key as boolean True
        on_block = wf.get(True) or wf.get("on") or {}
        tags = on_block.get("push", {}).get("tags", [])
        assert any("v*" in t for t in tags), "Workflow must trigger on v*.*.* tags"

    def test_workflow_triggers_on_release_branch(self):
        wf = _load_workflow()
        on_block = wf.get(True) or wf.get("on") or {}
        branches = on_block.get("push", {}).get("branches", [])
        assert any("release" in b for b in branches), "Workflow must trigger on release/* branches"

    def test_workflow_has_build_job(self):
        wf = _load_workflow()
        assert "build" in wf["jobs"]

    def test_workflow_has_testpypi_job(self):
        wf = _load_workflow()
        job_names = [j.lower() for j in wf["jobs"]]
        assert any("test" in j for j in job_names), "Workflow must include a TestPyPI publish job"

    def test_workflow_has_pypi_job(self):
        wf = _load_workflow()
        job_names = [j.lower() for j in wf["jobs"]]
        assert any("pypi" in j and "test" not in j for j in job_names), \
            "Workflow must include a PyPI (non-test) publish job"

    def test_pypi_job_only_on_tags(self):
        wf = _load_workflow()
        # Find the job that publishes to real PyPI (not testpypi)
        for name, job in wf["jobs"].items():
            if "testpypi" not in name.lower() and "pypi" in name.lower():
                condition = job.get("if", "")
                assert "tags/v" in condition, \
                    f"PyPI publish job '{name}' must be gated to version tags only"

    def test_workflow_uses_oidc_permissions(self):
        """At least one publish job should declare id-token: write for OIDC."""
        wf = _load_workflow()
        has_oidc = any(
            job.get("permissions", {}).get("id-token") == "write"
            for job in wf["jobs"].values()
        )
        assert has_oidc, "At least one job must declare 'id-token: write' for OIDC trusted publishing"


# ---------------------------------------------------------------------------
# Excluded paths
# ---------------------------------------------------------------------------

class TestNoDevOnlyPathsInPackagedData:
    def _manifest_content(self) -> str:
        path = os.path.join(REPO_ROOT, "MANIFEST.in")
        with open(path) as f:
            return f.read()

    def test_manifest_excludes_tests(self):
        content = self._manifest_content()
        assert "prune tests" in content, "MANIFEST.in must prune tests/"

    def test_manifest_excludes_docs(self):
        content = self._manifest_content()
        assert "prune docs" in content, "MANIFEST.in must prune docs/"

    def test_manifest_excludes_claude(self):
        content = self._manifest_content()
        assert "prune .claude" in content, "MANIFEST.in must prune .claude/"

    def test_manifest_excludes_audit_reports(self):
        content = self._manifest_content()
        assert "prune audit-reports" in content, "MANIFEST.in must prune audit-reports/"

    def test_manifest_excludes_proposals(self):
        content = self._manifest_content()
        assert "prune proposals" in content, "MANIFEST.in must prune proposals/"

    def test_manifest_includes_agents(self):
        content = self._manifest_content()
        assert "agents" in content, "MANIFEST.in must include agents/*.md"

    def test_manifest_includes_references(self):
        content = self._manifest_content()
        assert "references" in content, "MANIFEST.in must include references/*.md"

    def test_package_data_only_bundled_agents(self):
        """Package data must only reference paths that exist under agent_baton/."""
        data = _load_pyproject()
        pkg_data = data.get("tool", {}).get("setuptools", {}).get("package-data", {})
        agent_baton_data = pkg_data.get("agent_baton", [])
        for pattern in agent_baton_data:
            # Verify path starts with a known bundled subdir
            assert pattern.startswith("_bundled_"), \
                f"Package data pattern '{pattern}' must reference a _bundled_* subdirectory"

    def test_find_packages_excludes_tests(self):
        """setuptools.packages.find must not include test packages."""
        data = _load_pyproject()
        find_cfg = data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
        include = find_cfg.get("include", [])
        # If include is specified, it should only cover agent_baton
        if include:
            for pattern in include:
                assert "test" not in pattern.lower(), \
                    f"packages.find include pattern '{pattern}' must not include test packages"
