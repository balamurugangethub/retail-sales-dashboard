"""Open a file from a web link, safely.

The server downloads the link for the person, so it must never be tricked into reaching private
addresses (the host's own services, cloud metadata, a home network). Every hop of a link is checked:
only http and https on the usual ports, only addresses on the public internet, a short time limit, a
size limit, and at most three redirects. The connection goes to the address that was checked, so a
name cannot change its answer between the check and the download.
"""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 10
PORTS = {"http": 80, "https": 443}
CHUNK = 64 * 1024


class SourceError(ValueError):
    pass


def public_address(host):
    """First public IP address for a host name, or a SourceError."""
    try:
        found = {info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as e:
        raise SourceError("That link's address could not be found.") from e
    for text in sorted(found):
        if not ipaddress.ip_address(text.split("%")[0]).is_global:
            raise SourceError("That link points to a private address, so it can't be opened.")
    if not found:
        raise SourceError("That link's address could not be found.")
    return sorted(found)[0]


def check_url(url):
    """Returns (scheme, host, port, path) for an acceptable link."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in PORTS or not parts.hostname:
        raise SourceError("Paste a full link that starts with http:// or https://.")
    if parts.username or parts.password:
        raise SourceError("Links with a user name or password in them are not supported.")
    port = parts.port or PORTS[parts.scheme]
    if port != PORTS[parts.scheme]:
        raise SourceError("Only the standard web ports are allowed.")
    path = parts.path or "/"
    return parts.scheme, parts.hostname, port, path + (f"?{parts.query}" if parts.query else "")


class Pinned:
    """Mixin: connect to a fixed, already checked address."""
    pinned = None

    def connect(self):
        sock = socket.create_connection((self.pinned, self.port), self.timeout)
        if isinstance(self, http.client.HTTPSConnection):
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        self.sock = sock


class PinnedHTTP(Pinned, http.client.HTTPConnection):
    pass


class PinnedHTTPS(Pinned, http.client.HTTPSConnection):
    pass


def fetch(url):
    """Download a link. Returns (bytes, file name)."""
    for _ in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = check_url(url)
        conn = (PinnedHTTPS if scheme == "https" else PinnedHTTP)(host, port, timeout=TIMEOUT)
        conn.pinned = public_address(host)
        try:
            conn.request("GET", path, headers={"User-Agent": "retail-sales-dashboard", "Accept": "*/*",
                                               "Accept-Encoding": "identity"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urljoin(url, resp.getheader("Location"))
                continue
            if resp.status != 200:
                raise SourceError(f"The link answered with an error ({resp.status}).")
            body, size = [], 0
            while chunk := resp.read(CHUNK):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise SourceError("That file is larger than 10 MB.")
                body.append(chunk)
        except (OSError, http.client.HTTPException) as e:
            raise SourceError("The link could not be downloaded right now.") from e
        finally:
            conn.close()
        name = path.split("?")[0].rstrip("/").rsplit("/", maxsplit=1)[-1] or host
        return b"".join(body), name
    raise SourceError("That link redirects too many times.")
