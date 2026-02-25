#!/usr/bin/env python3
# -*- coding:utf-8 -*-

"""
Author: Sneh Kr
Github: https://github.com/snehkr/GrabFreeProxy
Copyright (c) 2025 snehkr
This script fetches proxies from multiple sources, checks their availability by attempting to access common websites,
"""

from urllib.request import urlopen
from urllib.error import URLError, HTTPError
from operator import itemgetter
from datetime import datetime, timezone
import json, requests, aiohttp, re, asyncio, time, ipaddress, os
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

# Configuration
TIMEOUT = 5  # seconds
CPU = os.cpu_count() or 4
MAX_CONCURRENT_CHECKS = min(150, CPU * 40)
CONNECTOR_LIMIT = MAX_CONCURRENT_CHECKS * 2
REQUEST_TIMEOUT_OBJ = aiohttp.ClientTimeout(
    sock_connect=2,
    sock_read=3,
    total=4,
)
CHECK_URLS = [
    (
        "jiotv",
        "http://jiotvapi.cdn.jio.com/apis/v1.3/getepg/get?channel_id=144&offset=0",
    ),
    (
        "tplay_1",
        "https://tm.tapi.videoready.tv/portal-search/pub/api/v1/channels/schedule?date=&languageFilters=&genreFilters=&limit=1&offset=0",
    ),
    (
        "tplay_2",
        "https://ts-api.videoready.tv/portal-search/pub/api/v1/channels/schedule?date=&languageFilters=&genreFilters=&limit=1&offset=0",
    ),
]


async def check_single_url(session, ip, port, website_name, url):
    """Check one website through proxy"""
    start = time.perf_counter()

    try:
        async with session.get(
            url,
            proxy=f"http://{ip}:{port}",
            timeout=REQUEST_TIMEOUT_OBJ,
        ) as resp:
            await resp.release()
            total_time = int((time.perf_counter() - start) * 1000)

            return {
                website_name + "_status": resp.status,
                website_name + "_error": "no",
                website_name + "_total_time": total_time,
            }

    except asyncio.TimeoutError:
        return {
            website_name + "_status": 408,
            website_name + "_error": "timeout error",
            website_name + "_total_time": None,
        }

    except aiohttp.ClientProxyConnectionError:
        return {
            website_name + "_status": 503,
            website_name + "_error": "connection error",
            website_name + "_total_time": None,
        }

    except Exception as e:
        return {
            website_name + "_status": 503,
            website_name + "_error": f"unknown error: {e}",
            website_name + "_total_time": None,
        }


def verify_ip_port(ip: str, port: str) -> bool:
    """Validate IP address and port."""
    try:
        ipaddress.ip_address(ip)
        port_num = int(port)
        return 1 <= port_num <= 65535
    except (ValueError, ipaddress.AddressValueError):
        return False


class Source:
    url = ""
    ip_pat = re.compile(r"\s?\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}.*")

    def read_url(self):
        data = None
        if self.url:
            try:
                with urlopen(self.url) as handler:
                    data = handler.read().decode("utf-8")
            except (HTTPError, URLError):
                pass
        return data

    def read_mech_url(self, extra_headers=None):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1"
            )
        }

        if extra_headers:
            headers.update(dict(extra_headers))

        try:
            response = requests.get(self.url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            print(f"Error fetching {self.url}: {e}")
            return None

    def get_data(self):
        raise NotImplementedError


class FreeProxyWorldList(Source):
    """Get proxies from freeproxy.world (paginated)"""

    base_url = "https://www.freeproxy.world/"
    max_pages = 3

    def read_url(self):
        return self.read_mech_url()

    def get_data(self):
        result = []
        seen = set()

        original_url = self.url if hasattr(self, "url") else self.base_url

        for page in range(1, self.max_pages + 1):
            try:
                if page == 1:
                    self.url = self.base_url
                else:
                    self.url = f"{self.base_url}?page={page}"

                html = self.read_url()
                if not html:
                    continue

                soup = BeautifulSoup(html, "lxml")
                tbody = soup.find("tbody")
                if not tbody:
                    continue

                rows = tbody.find_all("tr")

                for row in rows:
                    cols = row.find_all("td")
                    if len(cols) < 7:
                        continue

                    try:
                        ip = cols[0].get_text(strip=True)
                        port = cols[1].get_text(strip=True)

                        ptype = cols[5].get_text(strip=True).lower()
                        # anonymity = cols[6].get_text(strip=True).lower()

                        # keep only HTTP proxies (optional but recommended)
                        if "http" not in ptype:
                            continue

                        if self.ip_pat.match(ip):
                            key = f"{ip}:{port}"
                            if key not in seen:
                                seen.add(key)
                                result.append((ip, port))

                    except Exception:
                        continue

            except Exception as e:
                print(f"⚠️ FreeProxyWorld page {page} error: {e}")

        # restore original URL (clean practice)
        self.url = original_url

        return result


class OpenProxyList(Source):
    """Get proxies from https://github.com/roosterkid/openproxylist"""

    url = "https://raw.githubusercontent.com/roosterkid/openproxylist/refs/heads/main/HTTPS_RAW.txt"

    def get_data(self):
        data = self.read_url()
        result = list()
        if data:
            for line in data.split("\n"):
                if self.ip_pat.match(line):
                    try:
                        ip, port = line.split()[0].split(":")
                        result.append((ip, port))
                    except ValueError:
                        pass
        return result if result else []


class ProxyDBList(Source):
    """Get proxies from proxydb.net (HTML parser version)"""

    url = "https://proxydb.net/?protocol=http&protocol=https&country=&offset=0"

    def get_data(self):
        html = self.read_url()
        result = []

        if not html:
            return result

        try:
            soup = BeautifulSoup(html, "lxml")

            # find table body
            tbody = soup.find("tbody")
            if not tbody:
                return result

            rows = tbody.find_all("tr")

            for row in rows:
                cols = row.find_all("td")
                if len(cols) < 2:
                    continue

                # IP is in first column <a>
                ip_tag = cols[0].find("a")
                # Port is in second column <a>
                port_tag = cols[1].find("a")

                if not ip_tag or not port_tag:
                    continue

                ip = ip_tag.text.strip()
                port = port_tag.text.strip()

                # optional validation using your existing regex
                if self.ip_pat.match(f"{ip}:{port}"):
                    result.append((ip, port))

        except Exception as e:
            print(f"⚠️ ProxyDB parse error: {e}")

        return result


class FreeProxyList(Source):
    """Get proxies from free-proxy-list.net"""

    url = "https://free-proxy-list.net/en/"

    def read_url(self):
        return self.read_mech_url()

    def get_data(self):
        data = self.read_url()
        if not data:
            return []

        soup = BeautifulSoup(data, "lxml")

        table = soup.find("table", class_="table table-striped table-bordered")
        if not table:
            print("❌ Table not found.")
            return []

        result = []
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if len(tds) >= 2:
                td_ip = tds[0].text.strip()
                td_port = tds[1].text.strip()
                if self.ip_pat.match(td_ip):
                    result.append((td_ip, td_port))
        return result if result else []


class ProxyDailyList(Source):
    """Get proxies from proxy-daily.com"""

    def get_data(self):

        headers = {
            "accept": "application/json",
            "referer": "https://proxy-daily.com/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }

        params = (
            ("draw", "0"),
            ("columns[0][data]", "ip"),
            ("columns[0][name]", "ip"),
            ("columns[0][searchable]", "true"),
            ("columns[0][orderable]", "false"),
            ("columns[0][search][value]", ""),
            ("columns[0][search][regex]", "false"),
            ("columns[1][data]", "port"),
            ("columns[1][name]", "port"),
            ("columns[1][searchable]", "true"),
            ("columns[1][orderable]", "false"),
            ("columns[1][search][value]", ""),
            ("columns[1][search][regex]", "false"),
            ("columns[2][data]", "protocol"),
            ("columns[2][name]", "protocol"),
            ("columns[2][searchable]", "true"),
            ("columns[2][orderable]", "false"),
            ("columns[2][search][value]", ""),
            ("columns[2][search][regex]", "false"),
            ("columns[3][data]", "speed"),
            ("columns[3][name]", "speed"),
            ("columns[3][searchable]", "true"),
            ("columns[3][orderable]", "false"),
            ("columns[3][search][value]", ""),
            ("columns[3][search][regex]", "false"),
            ("columns[4][data]", "anonymity"),
            ("columns[4][name]", "anonymity"),
            ("columns[4][searchable]", "true"),
            ("columns[4][orderable]", "false"),
            ("columns[4][search][value]", ""),
            ("columns[4][search][regex]", "false"),
            ("columns[5][data]", "country"),
            ("columns[5][name]", "country"),
            ("columns[5][searchable]", "true"),
            ("columns[5][orderable]", "false"),
            ("columns[5][search][value]", ""),
            ("columns[5][search][regex]", "false"),
            ("start", "0"),
            ("length", "50"),
            ("search[value]", ""),
            ("search[regex]", "false"),
            ("_", int(time.time() * 1000)),
        )

        try:
            response = requests.get(
                "https://proxy-daily.com/api/serverside/proxies",
                headers=headers,
                params=params,
                timeout=10,
            ).json()
        except Exception as e:
            print(f"ProxyDailyList error: {e}")
            return []

        result = []

        for item in response.get("data", []):
            ip = item["ip"]
            port = item["port"]
            if self.ip_pat.match(ip):
                result.append((ip, port))

        return result if result else []


async def check_proxy(proxy, session, sem, now_iso):
    ip, port = proxy

    async with sem:
        result = {
            "ip": ip,
            "port": int(port),
            "last_checked": now_iso,
        }

        tasks = [
            check_single_url(session, ip, port, name, url) for name, url in CHECK_URLS
        ]

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for (name, _), r in zip(CHECK_URLS, responses):
            if isinstance(r, Exception):
                result.update(
                    {
                        name + "_status": 503,
                        name + "_error": f"gather error: {type(r).__name__}",
                        name + "_total_time": None,
                    }
                )
            else:
                result.update(r)

        return result


async def runner(complete_list, batch_size=1000):
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    now_iso = datetime.now(timezone.utc).isoformat()

    connector = aiohttp.TCPConnector(
        ssl=False,
        limit=CONNECTOR_LIMIT,
        ttl_dns_cache=86400,
        enable_cleanup_closed=True,
        keepalive_timeout=30,
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
    headers = {
        "Connection": "keep-alive",
        "User-Agent": "Mozilla/5.0",
    }

    results = []

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers=headers,
    ) as session:

        for i in range(0, len(complete_list), batch_size):
            batch = complete_list[i : i + batch_size]

            tasks = [check_proxy(item, session, sem, now_iso) for item in batch]

            for coro in asyncio.as_completed(tasks):
                r = await coro
                if r:
                    results.append(r)

    return results


def main():
    result = []

    sources = [
        FreeProxyList().get_data,
        OpenProxyList().get_data,
        ProxyDBList().get_data,
        FreeProxyWorldList().get_data,
        ProxyDailyList().get_data,
    ]

    print("Fetching proxy lists from sources...")
    print(
        f"Using up to {CPU} CPU cores for concurrent checks (max {MAX_CONCURRENT_CHECKS} checks at once)"
    )
    print(f"Connector limit set to {CONNECTOR_LIMIT}")
    # print("FreProxyList :", sources[0]())
    # print("OpenProxyList :", sources[1]())
    # print("ProxyDBList :", sources[2]())
    # print("FreeProxyWorldList :", sources[3]())
    # print("ProxyDailyList :", sources[4]())

    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        results = list(ex.map(lambda f: f(), sources))

    for r in results:
        result.extend(r)

    # remove duplicates (ip, port)
    seen = set()
    filtered_list = []

    for ip, port in result:
        if (ip, port) in seen:
            continue
        if verify_ip_port(ip, port):
            seen.add((ip, port))
            filtered_list.append((ip, port))
    try:
        result = asyncio.run(runner(filtered_list))
    except KeyboardInterrupt:
        print("Interrupted by user")
        return []

    return result


if __name__ == "__main__":
    data = main()
    sorted_data = []

    try:
        errorless_measures = [
            sum(
                item.get(resource + "_error") == "no"
                for resource in map(itemgetter(0), CHECK_URLS)
            )
            for item in data
        ]

        # Sort proxies by number of "no" errors
        arg_sorted = sorted(
            range(len(errorless_measures)),
            key=errorless_measures.__getitem__,
            reverse=True,
        )

        sorted_data = [data[ind] for ind in arg_sorted]

    except (KeyError, TypeError, AttributeError) as e:
        print(f"Error while sorting proxy data: {e}")

    to_json = {
        "status": "success",
        "version": "2.0.0",
        "description": "List of free proxies with status information",
        "author": "snehkr",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proxies": sorted_data or data,
    }

    try:
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, "gfp_proxy.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(to_json, f, sort_keys=True, indent=4)
        print(f"Proxy list saved to {file_path}")
    except Exception as e:
        print(f"Error writing JSON file: {e}")
