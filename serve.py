import asyncio
import re
import os
from sanic import Sanic
from sanic.log import logger
from sanic import response
from sanic_cors import CORS
from sanic.response import HTTPResponse
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = os.environ.get("API_URL", "https://api.dandiarchive.org/api").rstrip("/")

API_LOCAL_URL = os.environ.get("API_LOCAL_URL", API_URL).rstrip("/")

GUI_URL = os.environ.get("GUI_URL", "https://gui.dandiarchive.org").rstrip("/")

ABOUT_URL = os.environ.get("ABOUT_URL", "https://www.dandiarchive.org").rstrip("/")

JUPYTERHUB_URL = os.environ.get(
    "JUPYTERHUB_URL", "https://hub.dandiarchive.org"
).rstrip("/")

dandiset_identifier_regex = "^[0-9]{6}$"

production = "DEV628cc89a6444" not in os.environ
sem = None
basedir = os.environ["HOME"] if production else os.getcwd()
logdir = os.path.join(basedir, "redirector")
if not os.path.exists(logdir):
    os.makedirs(logdir, exist_ok=True)

handler_dict = {
    "class": "logging.handlers.TimedRotatingFileHandler",
    "when": "D",
    "interval": 7,
    "backupCount": 140,
    "formatter": "generic",
}
LOG_SETTINGS = dict(
    version=1,
    disable_existing_loggers=False,
    loggers={
        "sanic.root": {"level": "INFO", "handlers": ["consolefile"]},
        "sanic.error": {
            "level": "INFO",
            "handlers": ["error_consolefile"],
            "propagate": True,
            "qualname": "sanic.error",
        },
        "sanic.access": {
            "level": "INFO",
            "handlers": ["access_consolefile"],
            "propagate": True,
            "qualname": "sanic.access",
        },
    },
    handlers={
        "consolefile": {
            **handler_dict,
            **{"filename": os.path.join(logdir, "console.log")},
        },
        "error_consolefile": {
            **handler_dict,
            **{"filename": os.path.join(logdir, "error.log")},
        },
        "access_consolefile": {
            **handler_dict,
            **{"filename": os.path.join(logdir, "access.log"), "formatter": "access"},
        },
    },
    formatters={
        "generic": {
            "format": "%(asctime)s [%(process)d] [%(levelname)s] %(message)s",
            "datefmt": "[%Y-%m-%d %H:%M:%S %z]",
            "class": "logging.Formatter",
        },
        "access": {
            "format": "%(asctime)s - (%(name)s)[%(levelname)s][%(host)s]: "
            + "%(request)s %(message)s %(status)d %(byte)d",
            "datefmt": "[%Y-%m-%d %H:%M:%S %z]",
            "class": "logging.Formatter",
        },
    },
)

if production:
    app = Sanic("redirector", log_config=LOG_SETTINGS)
else:
    app = Sanic("redirector")
CORS(app)


def make_header(url):
    import time

    gmtnow = time.strftime("%a, %d %b %Y %I:%M:%S %p %Z", time.gmtime())
    header = {
        "location": url,
        "content-type": "text/plain;charset=UTF-8",
        "content-length": 0,
        "date": gmtnow,
        "alt-svc": "clear",
    }
    return header


@app.listener("before_server_start")
async def init(app, loop):
    global sem
    sem = asyncio.Semaphore(100)


@app.route("/", methods=["GET"])
async def main(request):
    return response.redirect(GUI_URL + "/")


@app.route("/about", methods=["GET"])
async def about(request):
    return response.redirect(ABOUT_URL)


@app.route("/dandiset", methods=["GET"])
async def goto_public_dashboard(request):
    """Redirect to GUI public dandisets
    """
    return response.redirect(f"{GUI_URL}/#/dandiset")


@app.route("/dandiset/<dataset>", methods=["GET", "HEAD"])
async def goto_dandiset(request, dataset):
    """Redirect to GUI with dandiset identifier
    """
    if not re.fullmatch(dandiset_identifier_regex, dataset):
        return response.text(f"{dataset}: invalid Dandiset ID", status=400)
    req = requests.get(f"{API_LOCAL_URL}/dandisets/{dataset}")
    if req.reason == "OK":
        url = f"{GUI_URL}/#/dandiset/{dataset}/draft"
        if request.method == "HEAD":
            return response.html(None, status=302, headers=make_header(url))
        return response.redirect(url)
    return response.text(f"dandi:{dataset} not found.", status=404)


@app.route("/dandiset/<dataset>/<version>", methods=["GET", "HEAD"])
async def goto_dandiset_version(request, dataset, version):
    """Redirect to GUI with dandiset identifier and version
    """
    if not re.fullmatch(dandiset_identifier_regex, dataset):
        return response.text(f"{dataset}: invalid Dandiset ID", status=400)
    req = requests.get(f"{API_LOCAL_URL}/dandisets/{dataset}/versions/{version}")
    if req.reason == "OK":
        url = f"{GUI_URL}/#/dandiset/{dataset}/{version}"
        if request.method == "HEAD":
            return response.html(None, status=302, headers=make_header(url))
        return response.redirect(url)
    return response.text(f"dandi:{dataset}/{version} not found.", status=404)


@app.route("/server-info", methods=["GET"])
async def server_info(request):
    return response.json(
        {
            "version": "1.2.0",
            "cli-minimal-version": "0.14.2",
            "cli-bad-versions": [],
            "services": {
                "girder": {"url": None},
                "api": {"url": API_LOCAL_URL},
                "webui": {"url": GUI_URL},
                "jupyterhub": {"url": JUPYTERHUB_URL},
            },
        },
        indent=4,
    )


async def _fetch(url):
    querystring = {"page_size": "100"}
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    resp = requests.request("GET", url, headers=headers, params=querystring).json()
    results = resp["results"]
    while resp["next"]:
        resp = requests.request("GET", resp["next"], headers=headers).json()
        results.extend(resp["results"])
    return results


async def _sitemap():
    sitemapfile = Path("sitemap.xml")
    if sitemapfile.exists():
        modified = datetime.fromtimestamp(sitemapfile.stat().st_mtime, tz=timezone.utc)
        if (datetime.now(tz=timezone.utc) - modified) < timedelta(days=1):
            return sitemapfile.read_text()
    sitemap = [
        """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">"""
    ]

    for ds in await _fetch("https://api.dandiarchive.org/api/dandisets"):
        versions = await _fetch(
            f"https://api.dandiarchive.org/api/dandisets/"
            f"{ds['identifier']}/versions"
        )
        for version in versions:
            url = (
                f"https://dandiarchive.org/dandiset/{ds['identifier']}/"
                f"{version['version']}"
            )
            sitemap.append(
                f"""<url><loc>{url}</loc><lastmod>{version['modified']}</lastmod></url>"""
            )
    sitemap.append("</urlset>")
    sitemap = "\n".join(sitemap)
    sitemapfile.write_text(sitemap)
    return sitemap


@app.route("sitemap.xml", methods=["GET"])
async def sitemap(request):
    return HTTPResponse(
        await _sitemap(), status=200, headers=None, content_type="text/xml"
    )


if __name__ == "__main__":
    logger.info("Starting backend")
    app.run(host="0.0.0.0", port=8080)
