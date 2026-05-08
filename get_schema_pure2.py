import urllib.request
import json
import sys

url = "https://gateway.thegraph.com/api/273bca6dba3810c4dfbb103666609a43/subgraphs/id/4xyasjQeREe7PxnF6wVdobZvCw5mhoHZq3T7guRpuNPf"

query = {
    "query": "{ __schema { queryType { fields { name } } } }"
}

data = json.dumps(query).encode('utf-8')
req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})

try:
    with urllib.request.urlopen(req) as response:
        result = response.read().decode('utf-8')
        print("RESULT:")
        print(result)
except Exception as e:
    print("ERROR:")
    print(e)
    
sys.stdout.flush()
