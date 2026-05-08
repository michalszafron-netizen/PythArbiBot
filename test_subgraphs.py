import asyncio
import aiohttp

urls = [
    "https://api.thegraph.com/subgraphs/name/messari/aave-v3-arbitrum",
    "https://gateway.thegraph.com/api/100/subgraphs/id/4xyasjQeREe7PxnF6wVdobZvCw5mhoHZq3T7guRpuNPf",
    "https://gateway-arbitrum.network.thegraph.com/api/100/subgraphs/id/4xyasjQeREe7PxnF6wVdobZvCw5mhoHZq3T7guRpuNPf"
]

query = "{ users(first: 1) { id } }"

async def test():
    with open("subgraph_out.txt", "w") as f:
        async with aiohttp.ClientSession() as session:
            for url in urls:
                try:
                    async with session.post(url, json={'query': query}, timeout=10) as resp:
                        f.write(f"{url}: {resp.status}\n")
                        if resp.status == 200:
                            f.write(f"Success\n")
                except Exception as e:
                    f.write(f"{url}: Failed - {e}\n")

asyncio.run(test())
