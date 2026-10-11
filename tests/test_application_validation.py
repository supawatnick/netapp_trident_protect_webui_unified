"""Regression tests for NetApp Trident Protect Application CR validation.

Run: python -m unittest discover -s tests -v
"""
import unittest
import yaml

from app.application_validation import validate_application_manifest


def manifest(**overrides):
    obj = {
        "apiVersion": "protect.trident.netapp.io/v1",
        "kind": "Application",
        "metadata": {"name": "demo", "namespace": "team-a"},
        "spec": {"includedNamespaces": [{"namespace": "team-a"}]},
    }
    for key, value in overrides.items():
        obj[key] = value
    return yaml.safe_dump(obj)


class ApplicationValidationTests(unittest.TestCase):
    def test_minimal(self):
        self.assertEqual(validate_application_manifest(manifest())["metadata"]["name"], "demo")

    def test_multi_namespace_selector_and_cluster_role(self):
        spec = {
            "includedNamespaces": [
                {"namespace": "team-a", "labelSelector": {"matchLabels": {"app": "api"}}},
                {"namespace": "team-b", "labelSelector": {"matchLabels": {"app": "database"}}},
            ],
            "includedClusterScopedResources": [{
                "groupVersionKind": {
                    "group": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "version": "v1",
                },
                "labelSelector": {"matchLabels": {"mylabel": "test"}},
            }],
        }
        md = {"name": "demo", "namespace": "team-a",
              "labels": {"env": "production"},
              "annotations": {"protect.trident.netapp.io/skip-vm-freeze": "false"}}
        result = validate_application_manifest(manifest(metadata=md, spec=spec))
        self.assertEqual(len(result["spec"]["includedNamespaces"]), 2)

    def test_pvc_only_requires_pv_and_snapshotclass(self):
        spec = {
            "includedNamespaces": [{"namespace": "team-a"}],
            "resourceFilter": {
                "resourceSelectionCriteria": "Include",
                "resourceMatchers": [{"kind": kind, "version": "v1"} for kind in
                                     ("PersistentVolumeClaim", "PersistentVolume", "VolumeSnapshotClass")],
            },
        }
        validate_application_manifest(manifest(spec=spec))
        spec["resourceFilter"]["resourceMatchers"].pop()
        with self.assertRaisesRegex(ValueError, "VolumeSnapshotClass"):
            validate_application_manifest(manifest(spec=spec))

    def test_missing_owner_namespace_rejected(self):
        with self.assertRaisesRegex(ValueError, "Application CR namespace"):
            validate_application_manifest(manifest(
                spec={"includedNamespaces": [{"namespace": "team-b"}]}))

    def test_duplicate_namespaces_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_application_manifest(manifest(
                spec={"includedNamespaces": [{"namespace": "team-a"}, {"namespace": "team-a"}]}))

    def test_invalid_label_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            validate_application_manifest(manifest(
                metadata={"name": "demo", "namespace": "team-a", "labels": {"bad key": "test"}}))

    def test_boolean_vm_annotation_rejected(self):
        with self.assertRaisesRegex(ValueError, "string"):
            validate_application_manifest(manifest(
                metadata={"name": "demo", "namespace": "team-a",
                          "annotations": {"protect.trident.netapp.io/skip-vm-freeze": False}}))

    def test_multi_document_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_application_manifest(manifest() + "---\n" + manifest())

    def test_wrong_kind_rejected(self):
        with self.assertRaisesRegex(ValueError, "Expected"):
            validate_application_manifest(manifest(kind="Backup"))

    def test_missing_filter_criteria_rejected(self):
        spec = {"includedNamespaces": [{"namespace": "team-a"}],
                "resourceFilter": {"resourceMatchers": [{"kind": "Pod"}]}}
        with self.assertRaisesRegex(ValueError, "Include or Exclude"):
            validate_application_manifest(manifest(spec=spec))


if __name__ == "__main__":
    unittest.main()
