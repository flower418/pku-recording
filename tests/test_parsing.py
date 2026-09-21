"""离线单元测试：不联网、不需要账号即可验证解析逻辑。

运行：python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pku_recording import aes  # noqa: E402
from pku_recording.blackboard import parse_courses, parse_videos  # noqa: E402
from pku_recording.hls import parse_media_playlist, pick_best_variant  # noqa: E402
from pku_recording.util import parse_selection, sanitize_filename  # noqa: E402

HOMEPAGE_HTML = """
<div class="portlet clearfix  " id="module:_141_1">
  <span class="moduleTitle" >当前学期课程</span>
  <ul class="portletList-img courseListing coursefakeclass ">
    <li>
      <a href="/webapps/blackboard/execute/launcher?type=Course&id=PkId{key=_104204_1, dataType=blackboard.data.course.Course}&url=">
        26271-00048-04835390-2206187171-00-1: 生成模型基础(26-27学年第1学期)</a>
    </li>
    <li>
      <a href="/webapps/blackboard/execute/launcher?type=Course&id=PkId{key=_104185_1, dataType=blackboard.data.course.Course}&url=">
        26271-00048-04835030-2206189157-00-1: 计算机视觉(26-27学年第1学期)</a>
    </li>
  </ul>
</div>
<div class="portlet clearfix  " id="module:_142_1">
  <span class="moduleTitle" >历史课程</span>
  <ul class="portletList-img courseListing coursefakeclass ">
    <li>
      <a href="/webapps/blackboard/execute/launcher?type=Course&id=PkId{key=_74465_1, dataType=blackboard.data.course.Course}&url=">
        24251-00024-02430150-1403891303-00-1: 中国政治概论(24-25学年第1学期)</a>
    </li>
  </ul>
</div>
"""

VIDEO_LIST_HTML = """
<tbody id="listContainer_databody">
  <tr>
    <th scope="row" valign="top">2026-09-18第3-4节</th>
    <td><span class="mobile-table-label">时间: </span>
        <span class="table-data-cell-value">2026-09-18 10:10:00</span></td>
    <td><span class="mobile-table-label">教师: </span>
        <span class="table-data-cell-value">贺笛</span></td>
    <td><span class="table-data-cell-value">
        <a class="inlineAction" target="_blank"
           href="playVideo.action?token=abc&amp;x=1">观看</a></span></td>
  </tr>
  <tr>
    <th scope="row" valign="top">2026-09-15第1-2节</th>
    <td><span class="table-data-cell-value">2026-09-15 08:00:00</span></td>
    <td><span class="table-data-cell-value">贺笛</span></td>
    <td><span class="table-data-cell-value">
        <a class="inlineAction" href="playVideo.action?token=def">观看</a></span></td>
  </tr>
</tbody>
"""

MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-TARGETDURATION:13
#EXT-X-KEY:METHOD=AES-128,URI="/taskflow/key/abcd"
#EXTINF:12.240000,
segment_0.ts
#EXTINF:8.760000,
segment_1.ts
#EXT-X-ENDLIST
"""

MASTER_PLAYLIST = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
360p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
1080p/index.m3u8
"""


class TestCourseParsing(unittest.TestCase):
    def test_parse_courses(self):
        courses = parse_courses(HOMEPAGE_HTML)
        self.assertEqual(len(courses), 3)
        current = [c for c in courses if c.is_current]
        self.assertEqual(len(current), 2)
        self.assertEqual(current[0].key, "_104204_1")
        self.assertEqual(current[0].short_name, "生成模型基础(26-27学年第1学期)")
        self.assertEqual(current[0].folder_name, "生成模型基础")
        self.assertEqual(current[0].semester, "26-27学年第1学期")

    def test_parse_videos(self):
        videos = parse_videos(VIDEO_LIST_HTML, None)
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].title, "2026-09-18第3-4节")
        self.assertEqual(videos[0].time, "2026-09-18 10:10:00")
        self.assertEqual(videos[0].teacher, "贺笛")
        self.assertTrue(videos[0].url.startswith("https://course.pku.edu.cn/"))
        self.assertIn("token=abc&x=1", videos[0].url)  # &amp; 已还原


class TestPlaylistParsing(unittest.TestCase):
    def test_media_playlist(self):
        pl = parse_media_playlist(MEDIA_PLAYLIST, "https://cdn.example.com/a/playlist.m3u8")
        self.assertEqual(len(pl.segments), 2)
        self.assertEqual(pl.segments[0][0], "https://cdn.example.com/a/segment_0.ts")
        key = pl.segments[0][1]
        self.assertIsNotNone(key)
        self.assertEqual(key.method, "AES-128")
        self.assertEqual(key.uri, "https://cdn.example.com/taskflow/key/abcd")

    def test_master_playlist_picks_highest_bandwidth(self):
        best = pick_best_variant(MASTER_PLAYLIST, "https://cdn.example.com/master.m3u8")
        self.assertEqual(best, "https://cdn.example.com/1080p/index.m3u8")


class TestUtils(unittest.TestCase):
    def test_parse_selection(self):
        self.assertEqual(parse_selection("all", 5), [0, 1, 2, 3, 4])
        self.assertEqual(parse_selection("1,3-5", 5), [0, 2, 3, 4])
        self.assertEqual(parse_selection("2，4", 5), [1, 3])
        self.assertEqual(parse_selection("9", 5), [])
        self.assertEqual(parse_selection("3-1", 5), [0, 1, 2])

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename('a/b:c*?"<>|'), "a_b_c______")
        self.assertEqual(sanitize_filename("  多个   空格  "), "多个 空格")


class TestAES(unittest.TestCase):
    def test_nist_aes128cbc_vector(self):
        if aes.backend_name() is None:
            self.skipTest("没有可用的 AES 后端")
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plaintext = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        ciphertext = bytes.fromhex("7649abac8119b246cee98e9b12e9197d")
        self.assertEqual(aes.decrypt_aes128_cbc(ciphertext, key, iv), plaintext)

    def test_pkcs7_padding_removed(self):
        if aes.backend_name() is None:
            self.skipTest("没有可用的 AES 后端")
        key = b"0" * 16
        iv = b"0" * 16
        from pku_recording.aes import _decrypt_openssl  # noqa: F401  (仅确保模块可导入)
        # 手工构造：用 openssl 加密后解密，验证填充被去掉
        import shutil
        import subprocess

        if not shutil.which("openssl"):
            self.skipTest("没有 openssl")
        data = b"hello pku replay"
        enc = subprocess.run(
            ["openssl", "enc", "-aes-128-cbc", "-e", "-K", key.hex(), "-iv", iv.hex()],
            input=data,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertEqual(aes.decrypt_aes128_cbc(enc, key, iv), data)


if __name__ == "__main__":
    unittest.main()
