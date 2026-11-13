"""Verify anonymous GHCR access and the exact manifest digest; never use a PAT."""
import argparse
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

IMAGE = re.compile(r"^ghcr\.io/([a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9._-]*)+)@sha256:([a-f0-9]{64})$")
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A caller may explicitly follow a safe, unauthenticated blob redirect.


def parse_image(image):
    match = IMAGE.fullmatch(image)
    if not match:
        raise ValueError("Use ghcr.io/owner/name@sha256:<64 lowercase hex digits>")
    return match.groups()


class Registry:
    def __init__(self, repository):
        self.repository = repository
        self.opener = urllib.request.build_opener(NoRedirect())
        # No credentials: a private package must fail this anonymous request.
        query = urllib.parse.urlencode({"service": "ghcr.io", "scope": "repository:%s:pull" % repository})
        with self.opener.open("https://ghcr.io/token?" + query, timeout=15) as response:
            self.token = json.load(response).get("token")
        if not isinstance(self.token, str) or not self.token:
            raise RuntimeError("GHCR did not issue an anonymous pull token")

    def fetch(self, kind, digest):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise RuntimeError("Unsupported descriptor digest")
        request = urllib.request.Request("https://ghcr.io/v2/%s/%s/%s" % (self.repository, kind, digest),
                                         headers={"Accept": ACCEPT, "Authorization": "Bearer " + self.token})
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            if kind != "blobs" or error.code not in (301, 302, 303, 307, 308):
                raise
            location = error.headers.get("Location", "")
            parts = urllib.parse.urlsplit(location)
            host = parts.hostname or ""
            if (parts.scheme != "https" or parts.username or parts.password or parts.port or
                    not (host == "pkg-containers.githubusercontent.com" or host.endswith(".blob.core.windows.net"))):
                raise RuntimeError("Unexpected GHCR blob destination")
            # No registry Authorization header is forwarded to blob storage.
            response = self.opener.open(urllib.request.Request(location), timeout=20)
        with response:
            body = response.read(4 * 1024 * 1024 + 1)
        if len(body) > 4 * 1024 * 1024 or "sha256:" + hashlib.sha256(body).hexdigest() != digest:
            raise RuntimeError("Registry bytes do not match the requested descriptor digest")
        return json.loads(body)


def verify(image):
    repository, digest = parse_image(image)
    registry = Registry(repository)
    parsed = registry.fetch("manifests", "sha256:" + digest)
    platforms = [m.get("platform", {}) for m in parsed.get("manifests", [])]
    if "manifests" in parsed:
        selected = [m for m in parsed["manifests"] if m.get("platform", {}).get("os") == "linux" and m.get("platform", {}).get("architecture") == "amd64"]
        if not selected:
            raise RuntimeError("Image index does not contain linux/amd64")
        parsed = registry.fetch("manifests", selected[0]["digest"])
    configuration = registry.fetch("blobs", parsed["config"]["digest"])
    if configuration.get("os") != "linux" or configuration.get("architecture") != "amd64":
        raise RuntimeError("Actual image configuration is not linux/amd64")
    return {"status": "PASS", "image": image, "anonymous_manifest_access": True,
            "manifest_sha256_verified": True, "image_config_platform_verified": "linux/amd64", "platforms": platforms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.image)))
    except (ValueError, KeyError, TypeError, RuntimeError, urllib.error.URLError) as error:
        # Never print request headers or anonymous bearer tokens.
        parser.exit(1, "Public image verification failed: %s\n" % type(error).__name__)


if __name__ == "__main__":
    main()
