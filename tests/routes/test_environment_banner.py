import pytest
from flask import Flask

from broker import config

PAGES = ["/pair", "/about"]


@pytest.mark.parametrize(
    ("environment", "label"), [(None, ""), ("prod", ""), ("PROD", ""), ("dev", "DEV"), (" qa ", "QA")]
)
def test_only_environments_other_than_prod_get_a_label(app, monkeypatch, environment, label):
    if environment is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", environment)
    loaded = Flask(__name__)

    config.load(loaded)

    assert loaded.config["ENVIRONMENT_LABEL"] == label


@pytest.mark.parametrize("path", PAGES)
def test_prod_pages_have_no_banner(client, path):
    html = client.get(path).get_data(as_text=True)

    assert "environment-banner" not in html
    assert "<title>[" not in html


@pytest.mark.parametrize("path", PAGES)
def test_dev_pages_say_so_in_a_banner_and_the_title(app, client, path):
    app.config["ENVIRONMENT_LABEL"] = "DEV"

    html = client.get(path).get_data(as_text=True)

    assert 'class="environment-banner"' in html
    assert "<strong>DEV</strong>" in html
    assert "<title>[DEV] " in html


def test_the_github_dashboard_says_so_too(app, client):
    app.config.update(
        ENVIRONMENT_LABEL="DEV",
        GITHUB_DASHBOARD_USER_ID="user-1",
        GITHUB_APP_CLIENT_ID="Iv1.client",
        GITHUB_APP_CLIENT_SECRET="shh",
    )

    html = client.get("/github").get_data(as_text=True)

    assert "<strong>DEV</strong>" in html
    assert "<title>[DEV] GitHub dashboard</title>" in html
