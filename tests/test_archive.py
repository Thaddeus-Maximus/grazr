"""Export and import of the whole data folder."""
import io
import zipfile

import pytest

from grazr import archive
from grazr.cli import main


def names(data: bytes):
    return set(zipfile.ZipFile(io.BytesIO(data)).namelist())


def test_export_has_every_data_file_and_no_state(farm, farm_root):
    (farm_root / ".trash").mkdir()
    (farm_root / ".trash" / "old.md").write_text("x", encoding="utf-8")
    got = names(archive.export_bytes(farm_root))
    assert "grazr.yaml" in got and "animals/orange-010.md" in got and "attachments/gate.png" in got
    assert not any(n.startswith(".") for n in got)   # .grazr/ index, .trash/


def test_round_trip_into_empty_folder(farm_root, tmp_path):
    data = archive.export_bytes(farm_root)
    dst = tmp_path / "new"
    dst.mkdir()
    got = archive.import_(dst, io.BytesIO(data))
    assert got["trash"] is None and got["written"] == len(names(data))
    for n in names(data):
        assert (dst / n).read_bytes() == (farm_root / n).read_bytes()


def test_import_trashes_what_it_replaces(farm_root):
    data = archive.export_bytes(farm_root)
    note = farm_root / "notes" / "2026Oct12-0707.md"
    old = note.read_bytes()
    note.write_text("changed", encoding="utf-8")
    (farm_root / "extra.md").write_text("mine", encoding="utf-8")
    got = archive.import_(farm_root, io.BytesIO(data))
    assert note.read_bytes() == old
    assert (farm_root / got["trash"] / "notes" / "2026Oct12-0707.md").read_text(encoding="utf-8") == "changed"
    assert (farm_root / "extra.md").exists()   # merge keeps files the zip lacks

    archive.import_(farm_root, io.BytesIO(data), replace=True)
    assert not (farm_root / "extra.md").exists()
    assert set(archive.data_files(farm_root)) == names(data)


def test_unwraps_a_zipped_folder(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("farm/grazr.yaml", "id_digits: 3\n")
        z.writestr("farm/notes/a.md", "hi")
        z.writestr("farm/.grazr/index.sqlite", "junk")
    archive.import_(tmp_path, io.BytesIO(buf.getvalue()))
    assert (tmp_path / "notes" / "a.md").exists() and not (tmp_path / ".grazr").exists()


@pytest.mark.parametrize("bad", ["../evil.md", "/abs.md", "C:/x.md", "a/../../x.md"])
def test_refuses_unsafe_paths(tmp_path, bad):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ok.md", "")
        z.writestr(bad, "x")
    with pytest.raises(archive.ArchiveError):
        archive.import_(tmp_path / "root", io.BytesIO(buf.getvalue()))


def test_not_a_zip(tmp_path):
    with pytest.raises(archive.ArchiveError):
        archive.import_(tmp_path, io.BytesIO(b"nope"))


def test_cli(farm_root, tmp_path):
    out = tmp_path / "b.zip"
    assert main(["--root", str(farm_root), "export", "-o", str(out)]) == 0
    dst = tmp_path / "restored"
    dst.mkdir()
    assert main(["--root", str(dst), "import", str(out)]) == 0
    assert (dst / "grazr.yaml").exists()


def test_cli_export_into_root_skips_itself(farm_root):
    out = farm_root / "backup.zip"
    assert main(["--root", str(farm_root), "export", "-o", str(out)]) == 0
    assert "backup.zip" not in names(out.read_bytes())
