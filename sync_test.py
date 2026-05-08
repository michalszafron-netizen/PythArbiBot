import urllib.request, json, traceback

def test():
    urls = [
        "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-arbitrum",
        "https://api.thegraph.com/subgraphs/name/messari/aave-v3-arbitrum",
        "https://gateway-arbitrum.network.thegraph.com/api/100/subgraphs/id/4xyasjRpuNPf7p2e1R8yvW62sH7bC99QYd1QY5wSjK9k",
        "https://aave-api-v2.aave.com/data/liquidity/v3?poolId=0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    ]
    query = {"query": "{ users(first: 5, where: {borrowedReservesCount_gt: 0}) { id } }"}
    data = json.dumps(query).encode("utf-8")
    
    with open("sync_out.txt", "w") as f:
        for url in urls:
            try:
                f.write(f"Testing {url}...\n")
                if "aave-api" in url:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                else:
                    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
                r = urllib.request.urlopen(req, timeout=10)
                resp = r.read().decode()
                f.write(f"Status: {r.status}\nResp: {resp[:200]}\n")
            except Exception as e:
                f.write(f"Failed: {repr(e)}\n{traceback.format_exc()}\n")

test()
