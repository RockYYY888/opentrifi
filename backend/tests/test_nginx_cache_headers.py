from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
	return (ROOT / relative_path).read_text(encoding="utf-8")


def test_nginx_templates_cache_assets_immutably_and_keep_html_dynamic() -> None:
	for relative_path in [
		"nginx/default.conf.template",
		"nginx/default.conf.ssl.template",
	]:
		contents = _read(relative_path)
		assert 'location ^~ /assets/' in contents
		assert 'Cache-Control "public, max-age=31536000, immutable"' in contents
		assert 'location /api/' in contents
		assert 'location / {' in contents
		assert contents.count('Cache-Control "no-store" always;') >= 2


def test_nginx_templates_serve_mobile_icon_fallbacks_without_404() -> None:
	for relative_path in [
		"nginx/default.conf.template",
		"nginx/default.conf.ssl.template",
	]:
		contents = _read(relative_path)
		assert "location = /favicon.ico" in contents
		assert "location = /apple-touch-icon.png" in contents
		assert "location = /apple-touch-icon-precomposed.png" in contents
		assert "proxy_pass http://frontend:80/pwa-icon.svg;" in contents
