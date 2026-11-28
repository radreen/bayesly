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


def test_official_links_and_product_terms():
    from bayesly.source_video import official_links, story_terms
    html = ('<a href="https://openai.com/index/gpt-6-2/">x</a><a href="https://openai.com/">home</a>'
            '<a href="https://news.example.com/other">same site</a><a href="https://random.blog/post">no</a>')
    assert official_links(html, "https://news.example.com/a") == ["https://openai.com/index/gpt-6-2/"]
    assert story_terms("OpenAI releases new prompt tips for GPT-6.2 Astra and Sol", "OpenAI") == ["GPT-6.2", "Astra", "Sol"]
    assert story_terms("Employees at OpenAI and Anthropic say unreleased AI models pose risks", "OpenAI") == []


def test_footage_search_keeps_the_subject_and_drops_talk(monkeypatch):
    import sys, types
    from bayesly import source_video as sv
    entries = [
        {"id": "a", "title": "GTA 6 PC PORT is FULLY PLAYABLE", "duration": 483, "view_count": 379569, "channel": "Nerpah"},
        {"id": "b", "title": "GTA 6 news podcast", "duration": 3000, "view_count": 9e6, "channel": "Pod"},
        {"id": "c", "title": "Cyberpunk 2077 gameplay", "duration": 600, "view_count": 5e6, "channel": "X"},
        {"id": "d", "title": "GTA 6 #shorts", "duration": 30, "view_count": 8e6, "channel": "S"},
        {"id": "e", "title": "GTA VI 4K driving", "duration": 900, "view_count": 1000, "channel": "Y"},
    ]

    class Y:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"entries": entries}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Y))
    hits = sv.footage_search("GTA 6 PC gameplay", sv.footage_terms("GTA 6 PC gameplay"))
    assert [h["url"][-1] for h in hits] == ["a", "e"]          # GTA VI counts too; most watched first
