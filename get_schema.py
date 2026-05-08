import urllib.request
import urllib.error
import json

url = "https://gateway.thegraph.com/api/273bca6dba3810c4dfbb103666609a43/subgraphs/id/4xyasjQeREe7PxnF6wVdobZvCw5mhoHZq3T7guRpuNPf"
query = "{ __schema { queryType { fields { name } } } }"
req = urllib.request.Request(url, method="POST")
req.add_header("Content-Type", "application/json")

try:
    resp = urllib.request.urlopen(req, data=json.dumps({"query": query}).encode("utf-8"))
    data = json.loads(resp.read().decode("utf-8"))
    fields = [f["name"] for f in data["data"]["__schema"]["queryType"]["fields"]]
    print("FIELDS:", fields)
except urllib.error.HTTPError as e:
    print("HTTP ERROR:", e.code)
    print(e.read().decode("utf-8"))
except Exception as e:
    print("ERROR:", e)
