"""Application CR validation aligned to the Trident Protect 26.06 documentation.

Pure functions: no Kubernetes side effects. Server validates even when UI forms
provide the manifest, since the YAML editor and REST clients bypass form checks.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
LABEL_NAME = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?)$")
LABEL_PREFIX = re.compile(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?)$")
SKIP_VM_FREEZE = "protect.trident.netapp.io/skip-vm-freeze"
PVC_ONLY = {("PersistentVolumeClaim", "v1"), ("PersistentVolume", "v1"),
            ("VolumeSnapshotClass", "v1")}


def _dns_name(value: Any, field: str) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 63 or not DNS_LABEL.fullmatch(value):
        raise ValueError(f"{field} must be a valid Kubernetes DNS label (1-63 characters)")


def _labels(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a key/value mapping")
    for key, val in value.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise ValueError(f"{field} keys and values must be strings")
        prefix, sep, name = key.rpartition("/")
        if sep and (len(prefix) > 253 or not LABEL_PREFIX.fullmatch(prefix)):
            raise ValueError(f"{field} has invalid key prefix: {key}")
        if not 1 <= len(name) <= 63 or not LABEL_NAME.fullmatch(name):
            raise ValueError(f"{field} has invalid key: {key}")
        if len(val) > 63 or (val and not LABEL_NAME.fullmatch(val)):
            raise ValueError(f"{field}[{key}] is not a valid label value")


def _selector(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    if "matchLabels" in value:
        _labels(value["matchLabels"], field + ".matchLabels")
    if "matchExpressions" in value:
        expressions = value["matchExpressions"]
        if not isinstance(expressions, list):
            raise ValueError(f"{field}.matchExpressions must be a list")
        for item in expressions:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                raise ValueError(f"{field}.matchExpressions requires a key")
            if item.get("operator") not in ("In", "NotIn", "Exists", "DoesNotExist"):
                raise ValueError(f"{field}.matchExpressions has invalid operator")
            values = item.get("values", [])
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError(f"{field}.matchExpressions.values must be a string list")
            if item["operator"] in ("In", "NotIn") and not values:
                raise ValueError(f"{field}.matchExpressions.values required for In/NotIn")
            if item["operator"] in ("Exists", "DoesNotExist") and values:
                raise ValueError(f"{field}.matchExpressions.values must be empty for Exists/DoesNotExist")


def validate_application_manifest(raw: str) -> dict:
    """Parse and validate a single Application manifest; return the original dict."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 256_000:
        raise ValueError("Application YAML is empty or exceeds 256 KB")
    try:
        docs = list(yaml.safe_load_all(raw))
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML syntax error: {exc}") from exc
    if len(docs) != 1 or not isinstance(docs[0], dict):
        raise ValueError("Expected exactly one Application YAML mapping")
    doc = docs[0]
    if doc.get("apiVersion") != "protect.trident.netapp.io/v1" or doc.get("kind") != "Application":
        raise ValueError("Expected protect.trident.netapp.io/v1 Application")
    md = doc.get("metadata")
    if not isinstance(md, dict):
        raise ValueError("metadata must be a mapping")
    _dns_name(md.get("name"), "metadata.name")
    _dns_name(md.get("namespace"), "metadata.namespace")
    if "labels" in md:
        _labels(md["labels"], "metadata.labels")
    annotations = md.get("annotations", {})
    if not isinstance(annotations, dict):
        raise ValueError("metadata.annotations must be a mapping")
    if SKIP_VM_FREEZE in annotations and annotations[SKIP_VM_FREEZE] not in ("true", "false"):
        raise ValueError(f"{SKIP_VM_FREEZE} must be the string 'true' or 'false'")
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("spec must be a mapping")
    includes = spec.get("includedNamespaces")
    if not isinstance(includes, list) or not includes:
        raise ValueError("spec.includedNamespaces must be a nonempty list")
    selected = set()
    for i, entry in enumerate(includes):
        if not isinstance(entry, dict):
            raise ValueError(f"spec.includedNamespaces[{i}] must be a mapping")
        ns = entry.get("namespace")
        _dns_name(ns, f"spec.includedNamespaces[{i}].namespace")
        if ns in selected:
            raise ValueError(f"Duplicate included namespace: {ns}")
        selected.add(ns)
        if "labelSelector" in entry:
            _selector(entry["labelSelector"], f"spec.includedNamespaces[{i}].labelSelector")
    if md["namespace"] not in selected:
        raise ValueError("Application CR namespace must also be in includedNamespaces")
    scoped = spec.get("includedClusterScopedResources", [])
    if not isinstance(scoped, list):
        raise ValueError("spec.includedClusterScopedResources must be a list")
    for i, entry in enumerate(scoped):
        if not isinstance(entry, dict) or not isinstance(entry.get("groupVersionKind"), dict):
            raise ValueError(f"includedClusterScopedResources[{i}] requires groupVersionKind")
        gvk = entry["groupVersionKind"]
        if not all(isinstance(gvk.get(x), str) and gvk[x] for x in ("kind", "version")):
            raise ValueError(f"includedClusterScopedResources[{i}] requires kind and version")
        if not isinstance(gvk.get("group", ""), str):
            raise ValueError(f"includedClusterScopedResources[{i}].group must be a string")
        if "labelSelector" in entry:
            _selector(entry["labelSelector"], f"includedClusterScopedResources[{i}].labelSelector")
    resource_filter = spec.get("resourceFilter")
    if resource_filter is not None:
        if not isinstance(resource_filter, dict):
            raise ValueError("spec.resourceFilter must be a mapping")
        criteria = resource_filter.get("resourceSelectionCriteria")
        if criteria not in ("Include", "Exclude"):
            raise ValueError("resourceSelectionCriteria must be Include or Exclude")
        matchers = resource_filter.get("resourceMatchers")
        if not isinstance(matchers, list) or not matchers:
            raise ValueError("resourceMatchers must be a nonempty list")
        for i, match in enumerate(matchers):
            if not isinstance(match, dict) or not any(match.get(k) for k in
                    ("group", "kind", "version", "names", "namespaces", "labelSelectors")):
                raise ValueError(f"resourceMatchers[{i}] requires at least one selector")
            for key in ("group", "kind", "version"):
                if key in match and not isinstance(match[key], str):
                    raise ValueError(f"resourceMatchers[{i}].{key} must be a string")
            for key in ("names", "namespaces", "labelSelectors"):
                if key in match and (not isinstance(match[key], list) or
                                     any(not isinstance(v, str) for v in match[key])):
                    raise ValueError(f"resourceMatchers[{i}].{key} must be a string list")
        # A PVC-only matcher must include both dependencies. This catches
        # accidental incomplete PVC-only presets, not all generic include filters.
        selected_kinds = {(m.get("kind"), m.get("version")) for m in matchers}
        if criteria == "Include" and ("PersistentVolumeClaim", "v1") in selected_kinds:
            missing = PVC_ONLY - selected_kinds
            if missing:
                raise ValueError("PVC filtering requires PersistentVolume and VolumeSnapshotClass (version v1)")
    return doc
