"""OpenShift release payload comparison tools (Sippy, RHCOS RPMs, component RPMs)."""
from __future__ import annotations

import base64
import html
import logging
import re
import subprocess
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
from langchain_core.tools import tool

from perf_keeper.config import get_config

logger = logging.getLogger(__name__)


def _get_json(url: str) -> dict:
    logger.info("GET %s", url)
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _fetch_rhcos_rpms(stream: str, version: str) -> list[dict]:
    payload = _get_json(f"{get_config().ocp_release_api_url}/releasestream/{stream}/release/{version}")
    version_url = None
    if "changeLogJson" in payload and "components" in payload["changeLogJson"]:
        for component in payload["changeLogJson"]["components"]:
            if "Red Hat Enterprise Linux CoreOS" in component.get("name", ""):
                version_url = component.get("versionUrl")
                break
    if not version_url and "changeLog" in payload:
        changelog_html = base64.b64decode(payload["changeLog"]).decode("utf-8")
        urls = re.findall(r'https://releases-rhcos[^"\'<>\s]+', changelog_html)
        if urls:
            version_url = html.unescape(urls[-1])
    if not version_url:
        raise ValueError(f"RHCOS version URL not found in release {version}")
    parsed = urlparse(version_url)
    params = parse_qs(parsed.query)
    rhcos_stream = unquote(params["stream"][0])
    rhcos_release = params["release"][0]
    rhcos_arch = params.get("arch", ["x86_64"])[0]
    rhcos_base_url = f"{parsed.scheme}://{parsed.netloc}"
    commitmeta_url = f"{rhcos_base_url}/storage/{rhcos_stream}/builds/{rhcos_release}/{rhcos_arch}/commitmeta.json"
    commitmeta = _get_json(commitmeta_url)
    pkglist = commitmeta.get("rpmostree.rpmdb.pkglist", [])
    return [
        {"name": pkg[0], "version": pkg[2], "release": pkg[3], "arch": pkg[4]}
        for pkg in pkglist
    ]


def _format_rpm(rpm: dict) -> str:
    return f"{rpm['name']}-{rpm['version']}-{rpm['release']} (arch={rpm['arch']})"


@tool
def compare_releases(payload1: str, payload2: str) -> str:
    """Compare two OCP payload versions via Sippy, showing PR differences between them.

    Args:
        payload1: The target release version to compare.
        payload2: The base release version to compare from.
    """
    try:
        q = urlencode({"fromPayload": payload2, "toPayload": payload1})
        diff = _get_json(f"{get_config().sippy_base_url}/payloads/diff?{q}")
        if not isinstance(diff, list):
            return f"Unexpected response type: {type(diff).__name__}"
        if not diff:
            return "No PR differences found between the two payloads."
        lines: list[str] = []
        for pr in diff:
            if not isinstance(pr, dict):
                continue
            url = pr.get("url", "")
            desc = pr.get("description", "")
            repo = pr.get("name", "")
            bug = pr.get("bug_url", "")
            entry = f"- [{repo}] {url}"
            if desc:
                entry += f"\n  {desc}"
            if bug:
                entry += f"\n  Bug: {bug}"
            lines.append(entry)
        return "\n".join(lines) if lines else "No PR differences found between the two payloads."
    except Exception as e:
        return f"Error comparing releases: {e}"


def _release_pullspec(payload: str) -> str:
    """Derive the registry pullspec from a payload tag."""
    if "nightly" in payload or ".ci-" in payload:
        major = payload.split(".")[0]
        try:
            if int(major) >= 5:
                return f"registry.ci.openshift.org/ocp/release-{major}:{payload}"
        except ValueError:
            pass
        return f"registry.ci.openshift.org/ocp/release:{payload}"
    return f"quay.io/openshift-release-dev/ocp-release:{payload}-x86_64"


def _diff_rpm_lists(rpms_new: list[dict], rpms_old: list[dict]) -> str:
    """Diff two RPM lists and return formatted text for added/removed/updated packages."""
    old_by_name = {rpm["name"]: rpm for rpm in rpms_old}
    new_by_name = {rpm["name"]: rpm for rpm in rpms_new}
    added, removed, updated = [], [], []
    for name, rpm in new_by_name.items():
        if name not in old_by_name:
            added.append(rpm)
        else:
            old = old_by_name[name]
            if rpm["version"] != old["version"] or rpm["release"] != old["release"]:
                updated.append({"name": name, "old": old, "new": rpm})
    for name, rpm in old_by_name.items():
        if name not in new_by_name:
            removed.append(rpm)
    lines: list[str] = []
    if added:
        lines.append(f"Added ({len(added)}):")
        lines.extend(f"  + {_format_rpm(r)}" for r in added)
    if removed:
        lines.append(f"Removed ({len(removed)}):")
        lines.extend(f"  - {_format_rpm(r)}" for r in removed)
    if updated:
        lines.append(f"Updated ({len(updated)}):")
        for u in updated:
            lines.append(f"  ~ {u['name']}: {u['old']['version']}-{u['old']['release']} -> {u['new']['version']}-{u['new']['release']}")
    return "\n".join(lines) if lines else "no differences found."


def _host_os_image_name(payload: str) -> str:
    """Return the host OS overlay image name for a payload tag."""
    major = payload.split(".")[0]
    try:
        if int(major) >= 5:
            return "rhel-coreos-10"
    except ValueError:
        pass
    return "rhel-coreos"


@tool
def compare_rhcos_rpms(stream: str, version1: str, version2: str) -> str:
    """Compare RHCOS RPM packages between two OpenShift release payloads, showing added, removed, and updated packages.

    This compares both the base RHCOS compose RPMs and the host OS overlay image
    RPMs (rhel-coreos / rhel-coreos-10) which include layered packages such as
    cri-o, openvswitch, kubelet, and other OpenShift-specific RPMs.

    Args:
        stream: The release stream, e.g. '4.22.0-0.nightly', '4-stable', '4.18.0-0.ci'.
        version1: The newer release version tag.
        version2: The older release version tag to compare against.
    """
    try:
        rpms_new = _fetch_rhcos_rpms(stream, version1)
        rpms_old = _fetch_rhcos_rpms(stream, version2)
        base_result = _diff_rpm_lists(rpms_new, rpms_old)
    except Exception as e:
        base_result = f"Error comparing RHCOS RPMs: {e}"

    image_name = _host_os_image_name(version1)
    try:
        overlay_new = _list_component_rpms(version1, image_name)
        overlay_old = _list_component_rpms(version2, image_name)
        overlay_result = _diff_rpm_lists(overlay_new, overlay_old)
    except Exception as e:
        overlay_result = f"skipped ({e})"

    return f"Base RHCOS RPMs:\n{base_result}\n\nHost OS overlay RPMs ({image_name}):\n{overlay_result}"


def _list_component_rpms(payload: str, component: str) -> list[dict]:
    """List RPMs in a component image of a release payload."""
    pullspec = _release_pullspec(payload)
    cmd = ["oc", "adm", "release", "info", pullspec, f"--image-for={component}"]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=120)
    image_ref = result.stdout.strip()
    cmd = ["podman", "run", "--rm", "--entrypoint", "rpm", image_ref,
           "-qa", "--queryformat", "%{NAME} %{VERSION} %{RELEASE} %{ARCH}\n"]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
    rpms: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            rpms.append({"name": parts[0], "version": parts[1], "release": parts[2], "arch": parts[3]})
    return rpms


@tool
def get_component_rpms(payload: str, component: str) -> str:
    """Get the list of RPMs included in a component of an OCP release payload.

    Requires 'oc' and 'podman' CLI tools.

    Args:
        payload: The release version, e.g. '4.22.0-ec.4', '4.22.0-0.nightly-2026-03-23-022245'.
        component: The component name, e.g. 'ovn-kubernetes', 'etcd'.
    """
    try:
        rpms = _list_component_rpms(payload, component)
        if rpms:
            return "\n".join(_format_rpm(r) for r in rpms)
        return f"No RPMs found in component image for {component} in {payload}."
    except subprocess.CalledProcessError as e:
        return f"Error getting component RPMs: {e.stderr or e}"
    except Exception as e:
        return f"Error getting component RPMs: {e}"
