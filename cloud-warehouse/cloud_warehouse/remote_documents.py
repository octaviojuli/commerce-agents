"""Credential-free HTTPS attachment retrieval with a pinned public destination."""

import http.client
import ipaddress
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from .assets import MAX_BYTES
from .integrations import SourceError


@dataclass(frozen=True)
class Download:
    body: bytes
    etag: str = ""
    last_modified: str = ""


def destination(url, allowed_hosts):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 for c in url):
        raise SourceError("ATTACHMENT_URL_INVALID")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        valid = (
            parts.scheme == "https"
            and parts.port in (None, 443)
            and not parts.username
            and not parts.password
            and not parts.fragment
            and "@" not in parts.netloc
            and host in allowed_hosts
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise SourceError("ATTACHMENT_DESTINATION_DENIED")
    # Every returned address must be public; the selected address is used directly for
    # connect(), so a second DNS lookup cannot rebind the request to a private service.
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise SourceError("ATTACHMENT_NETWORK_FAILED") from None
    try:
        addresses = sorted({item[4][0] for item in records}, key=lambda item: ":" in item)
        if not addresses:
            raise ValueError("empty DNS answer")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                raise ValueError("non-public address")
            if ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
                raise ValueError("transition address")
    except ValueError:
        raise SourceError("ATTACHMENT_ADDRESS_DENIED") from None
    target = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parts.query:
        target += "?" + quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    return host, addresses[0], target


def _download(url, allowed_hosts):
    host, address, target = destination(url, allowed_hosts)
    connection = http.client.HTTPSConnection(host, timeout=10)
    try:
        raw = socket.create_connection((address, 443), timeout=10)
        try:
            # Certificate validation and SNI continue to use the approved hostname.
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        except BaseException:
            raw.close()
            raise
        connection.request("GET", target, headers={"Accept-Encoding": "identity"})
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise SourceError("ATTACHMENT_REDIRECT_DENIED")
        if response.status != 200:
            raise SourceError("ATTACHMENT_HTTP_FAILED")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise SourceError("ATTACHMENT_ENCODING_DENIED")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or not 0 < int(length) <= MAX_BYTES):
            raise SourceError("ATTACHMENT_SIZE_INVALID")
        chunks, size = [], 0
        while chunk := response.read(min(65536, MAX_BYTES + 1 - size)):
            size += len(chunk)
            if size > MAX_BYTES:
                raise SourceError("ATTACHMENT_SIZE_INVALID")
            chunks.append(chunk)
        if size == 0 or (length is not None and size != int(length)):
            raise SourceError("ATTACHMENT_BODY_INCOMPLETE")
        return Download(
            b"".join(chunks),
            (response.getheader("ETag") or "")[:500],
            (response.getheader("Last-Modified") or "")[:500],
        )
    except (OSError, http.client.HTTPException, UnicodeError):
        raise SourceError("ATTACHMENT_NETWORK_FAILED") from None
    finally:
        connection.close()


def fetch(url: str, allowed_hosts: set[str]) -> Download:
    """Bound DNS, TLS and slow streaming together; subprocess receives no ERP secrets."""
    if not allowed_hosts:
        raise SourceError("ATTACHMENT_HOSTS_UNCONFIGURED")
    environment = {key: value for key, value in os.environ.items() if key in ("PATH", "LANG")}
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output:
        source.write(json.dumps({"url": url, "hosts": sorted(allowed_hosts)}).encode())
        source.seek(0)
        process = subprocess.Popen(
            [sys.executable, "-m", "cloud_warehouse.remote_documents"],
            stdin=source,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise SourceError("ATTACHMENT_TIMEOUT") from None
        if process.returncode:
            raise SourceError("ATTACHMENT_PROCESS_FAILED")
        output.seek(0)
        metadata = json.loads(output.readline(4096))
        if metadata.get("error"):
            raise SourceError(metadata["error"])
        body = output.read(MAX_BYTES + 1)
        if not 0 < len(body) <= MAX_BYTES:
            raise SourceError("ATTACHMENT_SIZE_INVALID")
        return Download(body, metadata["etag"], metadata["last_modified"])


def main():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES + 4096, MAX_BYTES + 4096))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    try:
        request = json.loads(sys.stdin.buffer.read(20000))
        result = _download(request["url"], set(request["hosts"]))
    except SourceError as error:
        sys.stdout.write(json.dumps({"error": error.code}) + "\n")
        return
    sys.stdout.buffer.write(
        json.dumps({"etag": result.etag, "last_modified": result.last_modified}).encode()
        + b"\n"
        + result.body
    )


if __name__ == "__main__":
    main()
