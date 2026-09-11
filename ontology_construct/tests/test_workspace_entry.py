from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from fastapi.testclient import TestClient

from ontology_construct import api as api_module


class PageAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        url = values.get('src') if tag == 'script' else values.get('href') if tag == 'link' else None
        if url and not urlparse(url).scheme:
            self.urls.append(url)


def test_default_workspace_and_legacy_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api_module, 'API_OUTPUT_ROOT', tmp_path / 'api_jobs')
    with TestClient(api_module.create_app()) as client:
        redirect = client.get('/', follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers['location'] == '/frontend/workspace-preview/index.html'
        page = client.get('/')
        assert page.status_code == 200
        assert 'workspace.js' in page.text
        assets = PageAssets()
        assets.feed(page.text)
        assert len(assets.urls) >= 10
        for asset in assets.urls:
            response = client.get(urljoin(str(page.url), asset))
            assert response.status_code == 200, asset
        legacy = client.get('/legacy')
        assert legacy.status_code == 200
        assert 'workspace.js' not in legacy.text
        assert 'frontend/app.js' in legacy.text
