import json
import unittest
import zipfile
from io import BytesIO
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend.app.shared.crm.views import CrmConversation, CrmMessage, CrmResolver
from backend.app.shared.export_zip import (
    build_export_zip,
    clean_filename,
    dialogue_count,
)


def _message(mid: str, text: str, **kwargs) -> CrmMessage:
    params: dict = {
        "table_name": "msg_table",
        "cid": "10001-20002",
        "mid": mid,
        "sender_id": "20002@icbu",
        "created_at": 1756720000,
        "user_content_type": 0,
        "content_label": text,
        "content": text.encode("utf-8"),
    }
    params.update(kwargs)
    return CrmMessage(**params)


def _conv(contact: str = "20002") -> CrmConversation:
    return CrmConversation(
        contact_ali_id=contact,
        messages=[
            _message("m1", "hello"),
            _message("m2", "hi", sender_id="10001@icbu"),
            _message("m3", "sys", is_system=True),
        ],
        last_created_at=1756720000,
        last_content_label="hi",
        sid=7,
        key="alibaba_icbu:10001:20002",
        participants=(11, 22),
    )


class ExportZipTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.crm_path = str(Path(self.temp_dir.name) / "crm.sqlite")
        self.enterContext(mock.patch.dict(environ, {
            "MAA_CRM_DB_PATH": self.crm_path,
            "MAA_POOLS_DB_PATH": str(Path(self.temp_dir.name) / "pools.db"),
        }))

    def tearDown(self) -> None:
        from backend.app.shared.crm.sync import CRMAdapter

        try:
            CRMAdapter(database_path=self.crm_path).engine.dispose()
        finally:
            self.temp_dir.cleanup()

    def test_dialogue_count_skips_system(self) -> None:
        self.assertEqual(dialogue_count(_conv()), 2)

    def test_clean_filename(self) -> None:
        self.assertEqual(clean_filename('a/b:c<d>e|"f?g*h'), "a_b_c_d_e__f_g_h")
        self.assertEqual(clean_filename(""), "unknown")

    def test_build_export_zip_names_and_content(self) -> None:
        payload, archive_name = build_export_zip([_conv(), _conv()], CrmResolver("10001"))
        self.assertTrue(archive_name.startswith("Chats-Export-"))
        self.assertTrue(archive_name.endswith(".zip"))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            text = archive.read(names[0]).decode("utf-8")
        self.assertEqual(len(names), 2)
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all(name.endswith(".md") for name in names))
        self.assertIn("## 会话记录", text)
        self.assertIn("---", text)
        self.assertIn("客户ID: 20002", text)
        self.assertIn("我**", text)
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_export_renders_cards_as_markdown(self) -> None:
        conv = CrmConversation(
            contact_ali_id="20002",
            messages=[
                _message(
                    "m1",
                    "产品目录.pdf",
                    user_content_type=10010,
                    content=json.dumps({
                        "cardType": 12,
                        "params": {
                            "name": "产品目录.pdf",
                            "extensionType": "pdf",
                            "size": "1258291",
                            "downloadUrl": "https://download.example/f.pdf",
                        },
                    }).encode("utf-8"),
                ),
            ],
            last_created_at=1756720000,
            last_content_label="产品目录.pdf",
            sid=7,
            key="alibaba_icbu:10001:20002",
            participants=(11, 22),
        )
        payload, _ = build_export_zip([conv], CrmResolver("10001"))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
        self.assertIn("**产品目录.pdf**：PDF · 1.2 MB", text)
        self.assertIn("- [下载文件](https://download.example/f.pdf)", text)


if __name__ == "__main__":
    unittest.main()
