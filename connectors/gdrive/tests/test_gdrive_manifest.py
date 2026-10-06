"""The Drive seed manifest: building it from a Drive with its own IDs, what it reports, and the file round trip.

The shared contract tests run through it as the `gdrive-manifest` backend; these cover the rest.
"""
from connectors.gdrive.build_manifest import build
from connectors.gdrive.manifest import SeedManifest
from connectors.gdrive.testing import SeededDrive

POSTMORTEM = "gdrive:postmortem-pay-outage"
VENDOR = "gdrive:vendor-integration-notes"


def _renamed() -> SeededDrive:
    return SeededDrive(rename=lambda native_id: f"1Real{native_id.replace('-', '')}")


def test_build_maps_every_fixture_file_and_folder_to_the_drives_own_ids():
    d = _renamed()
    manifest, problems = build(d, d.data)
    assert problems == []
    assert manifest.files == {POSTMORTEM: "1Realpostmortempayoutage", VENDOR: "1Realvendorintegrationnotes"}
    assert manifest.folders == {"folder-incidents": "1Realfolderincidents", "folder-vendor": "1Realfoldervendor"}
    assert manifest.versions == {POSTMORTEM: "1", VENDOR: "1"}


def test_translation_goes_both_ways_and_unknown_ids_are_none():
    d = _renamed()
    manifest, _ = build(d, d.data)
    assert manifest.real_doc(POSTMORTEM) == "gdrive:1Realpostmortempayoutage"
    assert manifest.fixture_doc("gdrive:1Realpostmortempayoutage") == POSTMORTEM
    assert manifest.real_container("gdrive:folder-vendor") == "gdrive:1Realfoldervendor"
    assert manifest.fixture_container("gdrive:1Realfoldervendor") == "gdrive:folder-vendor"
    assert manifest.real_doc("gdrive:no-such-file") is None
    assert manifest.fixture_doc("gdrive:1RealUnknown") is None
    assert manifest.fixture_container("gdrive:1RealUnknown") is None


def test_build_reports_a_missing_file_and_a_duplicate_title():
    d = _renamed()
    d.drive.files["1Realvendorintegrationnotes"]["trashed"] = True
    d.drive.add("1RealCopy", "Postmortem: payment outage", owner=d.drive.files["1Realpostmortempayoutage"]["owner"],
                parent="1Realfolderincidents", content="a copy")
    manifest, problems = build(d, d.data)
    assert len(problems) == 2
    assert any(p.startswith(f"{POSTMORTEM}: found 2 files") for p in problems)
    assert any(p.startswith(f"{VENDOR}: found 0 files") for p in problems)
    assert manifest.files == {}


def test_build_reports_files_of_one_fixture_folder_found_in_two_folders():
    d = _renamed()
    data = {**d.data, "documents": [{**doc, "parent_id": "gdrive:folder-incidents"} if doc["doc_id"] == VENDOR else doc
                                    for doc in d.data["documents"]]}
    _, problems = build(d, data)
    assert problems == [f"{VENDOR}: in a different folder from the other files of gdrive:folder-incidents"]


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    d = _renamed()
    manifest, _ = build(d, d.data)
    path = tmp_path / "seed-manifest.local.json"
    manifest.save(path)
    assert SeedManifest.load(path) == manifest
    monkeypatch.setenv("GDRIVE_SEED_MANIFEST_PATH", str(path))
    assert SeedManifest.load() == manifest
    assert SeedManifest.load(tmp_path / "missing.json") == SeedManifest()
