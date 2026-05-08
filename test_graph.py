import requests
import sys

url = "https://gateway-arbitrum.network.thegraph.com/api/273bca6dba3810c4dfbb103666609a43/subgraphs/id/4xyasjRpuNPf7p2e1R8yvW62sH7bC99QYd1QY5wSjK9k"
url2 = "https://gateway.thegraph.com/api/273bca6dba3810c4dfbb103666609a43/subgraphs/id/4xyasjRpuNPf7p2e1R8yvW62sH7bC99QYd1QY5wSjK9k"

try:
    r = requests.post(url, json={"query": "{ _meta { block { number } } }"})
    print("URL1:", r.status_code, r.text)
except Exception as e:
    print("URL1 error:", e)

try:
    r2 = requests.post(url2, json={"query": "{ _meta { block { number } } }"})
    print("URL2:", r2.status_code, r2.text)
except Exception as e:
    print("URL2 error:", e)
