from bayesly.source_video import SourceVideo, Clip, candidates
from pathlib import Path


def test_candidates_in_page_order_and_normalised():
    html = ('<p>intro</p><iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe>'
            '<video src="/media/demo.mp4"></video>'
            '<blockquote><a href="https://twitter.com/OpenAI/status/1234567890123">x</a></blockquote>'
            '<a href="https://x.com/intent/status/999999999">share</a>'
            '<iframe src="https://player.vimeo.com/video/76979871"></iframe>'
            '<iframe src="https://www.youtube.com/embed/abcdefghijk"></iframe>')
    got = candidates(html, "https://news.example.com/a/b")
    assert got == ["https://www.youtube.com/watch?v=abcdefghijk", "https://news.example.com/media/demo.mp4",
                   "https://x.com/OpenAI/status/1234567890123", "https://vimeo.com/76979871"]


def test_credit_names_the_owners():
    sv = SourceVideo([Clip(Path("a"), "@OpenAI", 10), Clip(Path("b"), "@OpenAI", 8), Clip(Path("c"), "NVIDIA", 30)])
    assert sv.credit == "Video: @OpenAI, NVIDIA"
