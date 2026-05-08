import requests
import json

url = "https://api.thegraph.com/subgraphs/name/messari/aave-v3-arbitrum"
query = """
query {
  positions(first: 5, where: {side: BORROWER, isClosed: false}) {
    id
    balance
    isClosed
    account { id }
  }
}
"""

try:
    r = requests.post(url, json={"query": query})
    with open("out.json", "w") as f:
        json.dump({"status": r.status_code, "data": r.json()}, f, indent=2)
except Exception as e:
    with open("out.json", "w") as f:
        f.write(f"Error: {e}")
