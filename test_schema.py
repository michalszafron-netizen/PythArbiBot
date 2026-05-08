import requests
import json
import sys

url = "https://gateway.thegraph.com/api/273bca6dba3810c4dfbb103666609a43/subgraphs/id/4xyasjQeREe7PxnF6wVdobZvCw5mhoHZq3T7guRpuNPf"
query = "{ __schema { queryType { fields { name } } } }"
try:
    r = requests.post(url, json={"query": query})
    with open("schema_out.txt", "w") as f:
        f.write(json.dumps(r.json(), indent=2))
except Exception as e:
    with open("schema_out.txt", "w") as f:
        f.write(str(e))
