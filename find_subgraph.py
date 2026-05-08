import requests

url = "https://gateway.thegraph.com/network"
query = """
{
  subgraphSearch(text: "Aave V3 Arbitrum", first: 10) {
    id
    currentVersion {
      subgraphDeployment {
        ipfsHash
      }
    }
    metadata {
      displayName
    }
  }
}
"""

try:
    r = requests.post(url, json={"query": query})
    print(r.json())
except Exception as e:
    print("Error:", e)
